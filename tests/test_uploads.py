from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chfs.errors import IntegrityMismatchError, ResourceConflictError, UploadTooLargeError
from chfs.models import Permission, Principal
from chfs.paths import SafePathResolver
from chfs.services import bytes_chunks
from chfs.uploads import ResumableUploadManager


class ResumableUploadManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "shared"
        self.manager = ResumableUploadManager(SafePathResolver(self.root), max_upload_bytes=64 * 1024 * 1024)
        self.principal = Principal("guest", frozenset({Permission.WRITE}), False)

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_resume_integrity_and_atomic_commit(self) -> None:
        first = b"a" * (1024 * 1024)
        second = b"b" * (1024 * 1024 + 17)
        expected = first + second
        session = self.manager.create(self.principal, "large.bin", len(expected), "resume-1")
        snapshot = self.manager.snapshots()[0]
        self.assertEqual(snapshot["direction"], "upload")
        self.assertEqual(snapshot["status"], "waiting")
        self.assertEqual(snapshot["total_bytes"], len(expected))
        self.assertFalse((self.root / "large.bin").exists(), "事务完成前不能暴露目标文件")

        first_digest = hashlib.sha256(first).digest()
        await self.manager.append(
            self.principal,
            session.upload_id,
            0,
            first_digest.hex(),
            bytes_chunks([first[:12345], first[12345:]]),
        )
        resumed = self.manager.create(self.principal, "large.bin", len(expected), "resume-1")
        self.assertEqual(resumed.upload_id, session.upload_id)
        self.assertEqual(resumed.offset, len(first))

        with self.assertRaises(IntegrityMismatchError):
            await self.manager.append(
                self.principal,
                session.upload_id,
                len(first),
                "0" * 64,
                bytes_chunks([second]),
            )
        self.assertEqual(session.offset, len(first), "坏分块不得推进续传偏移")

        second_digest = hashlib.sha256(second).digest()
        await self.manager.append(
            self.principal,
            session.upload_id,
            len(first),
            second_digest.hex(),
            bytes_chunks([second]),
        )
        manifest = hashlib.sha256(first_digest + second_digest).hexdigest()
        entry, file_hash, manifest_hash = self.manager.complete(self.principal, session.upload_id, manifest)
        self.assertEqual(entry.size, len(expected))
        self.assertEqual(file_hash, hashlib.sha256(expected).hexdigest())
        self.assertEqual(manifest_hash, manifest)
        self.assertEqual((self.root / "large.bin").read_bytes(), expected)
        self.assertEqual(list(self.root.glob(".chfs-resume-*")), [])
        self.assertEqual(self.manager.snapshots(), [])

    async def test_limit_and_cancel_cleanup(self) -> None:
        with self.assertRaises(UploadTooLargeError):
            self.manager.create(self.principal, "too-large.bin", 65 * 1024 * 1024, "resume-large")
        session = self.manager.create(self.principal, "cancel.bin", 4, "resume-cancel")
        await self.manager.append(
            self.principal,
            session.upload_id,
            0,
            hashlib.sha256(b"part").hexdigest(),
            bytes_chunks([b"part"]),
        )
        self.manager.cancel(self.principal, session.upload_id)
        self.assertFalse(session.temporary.exists())
        self.assertFalse((self.root / "cancel.bin").exists())

    async def test_fast_mode_uses_size_offset_and_server_file_hash(self) -> None:
        content = b"fast-mode" * 10000
        session = self.manager.create(self.principal, "fast.bin", len(content), "resume-fast")
        await self.manager.append(
            self.principal,
            session.upload_id,
            0,
            None,
            bytes_chunks([content]),
        )
        entry, file_hash, _manifest = self.manager.complete(self.principal, session.upload_id, None)
        self.assertEqual(entry.size, len(content))
        self.assertEqual(file_hash, hashlib.sha256(content).hexdigest())
        self.assertEqual((self.root / "fast.bin").read_bytes(), content)

    async def test_request_body_is_written_incrementally(self) -> None:
        """消费下一个网络小块前，前一个小块应已写入临时文件。"""

        first = b"first-part"
        second = b"second-part"
        session = self.manager.create(
            self.principal,
            "streamed.bin",
            len(first) + len(second),
            "resume-streamed",
        )

        async def network_chunks():
            yield first
            self.assertEqual(session.temporary.read_bytes(), first)
            yield second

        await self.manager.append(
            self.principal,
            session.upload_id,
            0,
            None,
            network_chunks(),
        )
        self.assertEqual(session.temporary.read_bytes(), first + second)

    async def test_oversized_stream_rolls_back_file_and_hash(self) -> None:
        """流式请求越界时不得留下尾部数据，也不得污染完整文件哈希。"""

        session = self.manager.create(self.principal, "rollback.bin", 16, "resume-rollback")
        with patch("chfs.uploads.MAX_CHUNK_SIZE", 8):
            with self.assertRaises(UploadTooLargeError):
                await self.manager.append(
                    self.principal,
                    session.upload_id,
                    0,
                    None,
                    bytes_chunks([b"12345", b"6789"]),
                )
        self.assertEqual(session.offset, 0)
        self.assertEqual(session.temporary.read_bytes(), b"")

        content = b"abcdefghABCDEFGH"
        await self.manager.append(
            self.principal,
            session.upload_id,
            0,
            None,
            bytes_chunks([content]),
        )
        _entry, file_hash, _manifest = self.manager.complete(self.principal, session.upload_id, None)
        self.assertEqual(file_hash, hashlib.sha256(content).hexdigest())

    async def test_concurrent_chunks_cannot_interleave(self) -> None:
        """同一会话的第二个请求必须等待，并在锁内重新检查 offset。"""

        session = self.manager.create(self.principal, "ordered.bin", 8, "resume-ordered")
        first_part_written = asyncio.Event()
        release_first_request = asyncio.Event()

        async def slow_request():
            yield b"aaaa"
            first_part_written.set()
            await release_first_request.wait()
            yield b"bbbb"

        first_task = asyncio.create_task(
            self.manager.append(self.principal, session.upload_id, 0, None, slow_request())
        )
        await first_part_written.wait()
        second_task = asyncio.create_task(
            self.manager.append(
                self.principal,
                session.upload_id,
                0,
                None,
                bytes_chunks([b"XXXXXXXX"]),
            )
        )
        release_first_request.set()
        await first_task
        with self.assertRaises(ResourceConflictError):
            await second_task
        self.assertEqual(session.offset, 8)
        self.assertEqual(session.temporary.read_bytes(), b"aaaabbbb")

    async def test_cancelled_request_rolls_back_before_retry(self) -> None:
        """客户端断线取消协程后，已流入磁盘的数据必须先回滚再允许重试。"""

        content = b"retry-after-cancel"
        session = self.manager.create(
            self.principal,
            "cancelled-request.bin",
            len(content),
            "resume-cancelled-request",
        )
        first_part_written = asyncio.Event()
        wait_forever = asyncio.Event()

        async def interrupted_request():
            yield content[:5]
            first_part_written.set()
            await wait_forever.wait()

        task = asyncio.create_task(
            self.manager.append(
                self.principal,
                session.upload_id,
                0,
                None,
                interrupted_request(),
            )
        )
        await first_part_written.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(session.offset, 0)
        self.assertEqual(session.temporary.read_bytes(), b"")

        await self.manager.append(
            self.principal,
            session.upload_id,
            0,
            None,
            bytes_chunks([content]),
        )
        _entry, file_hash, _manifest = self.manager.complete(self.principal, session.upload_id, None)
        self.assertEqual(file_hash, hashlib.sha256(content).hexdigest())


if __name__ == "__main__":
    unittest.main()
