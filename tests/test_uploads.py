from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from chfs.errors import IntegrityMismatchError, UploadTooLargeError
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


if __name__ == "__main__":
    unittest.main()
