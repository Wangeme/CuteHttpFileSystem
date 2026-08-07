from __future__ import annotations

import io
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from chfs.archives import stream_zip_archive
from chfs.models import Permission, Principal
from chfs.paths import SafePathResolver
from chfs.services import FileService


class ArchiveStreamingTests(unittest.TestCase):
    def test_directory_is_streamed_with_unicode_files_and_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "shared"
            target = root / "资料"
            (target / "空目录").mkdir(parents=True)
            (target / "子目录").mkdir()
            (target / "你好.txt").write_text("你好，CHFS", encoding="utf-8")
            (target / "子目录" / "data.bin").write_bytes(b"\x00\x01\x02")
            resolver = SafePathResolver(root)
            service = FileService(resolver, 1024)
            reader = Principal("reader", frozenset({Permission.READ}), authenticated=True)

            sources = service.open_archive(reader, ["资料"])
            payload = b"".join(stream_zip_archive(sources, resolver))

            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"资料/", "资料/空目录/", "资料/子目录/", "资料/你好.txt", "资料/子目录/data.bin"},
                )
                self.assertEqual(archive.read("资料/你好.txt").decode("utf-8"), "你好，CHFS")
                self.assertEqual(archive.read("资料/子目录/data.bin"), b"\x00\x01\x02")

    def test_large_file_is_emitted_in_bounded_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "shared"
            root.mkdir()
            payload = b"x" * (3 * 1024 * 1024 + 17)
            (root / "large.bin").write_bytes(payload)
            resolver = SafePathResolver(root)
            service = FileService(resolver, len(payload))
            reader = Principal("reader", frozenset({Permission.READ}))

            chunks = list(stream_zip_archive(service.open_archive(reader, ["large.bin"]), resolver))

            self.assertGreater(len(chunks), 3)
            self.assertLessEqual(max(map(len, chunks)), 1024 * 1024)
            with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as archive:
                self.assertEqual(archive.read("large.bin"), payload)

    def test_multiple_sources_are_deduplicated_and_keep_their_names(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "shared"
            root.mkdir()
            (root / "a.txt").write_bytes(b"a")
            (root / "b.txt").write_bytes(b"b")
            resolver = SafePathResolver(root)
            service = FileService(resolver, 1024)
            reader = Principal("reader", frozenset({Permission.READ}))

            sources = service.open_archive(reader, ["a.txt", "a.txt", "b.txt"])
            payload = b"".join(stream_zip_archive(sources, resolver))

            self.assertEqual(len(sources), 2)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertEqual(archive.namelist(), ["a.txt", "b.txt"])

    def test_cancelled_consumer_stops_archive_producer(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "shared"
            root.mkdir()
            with (root / "large.bin").open("wb") as stream:
                stream.truncate(16 * 1024 * 1024)
            resolver = SafePathResolver(root)
            service = FileService(resolver, 32 * 1024 * 1024)
            reader = Principal("reader", frozenset({Permission.READ}))
            archive_stream = stream_zip_archive(service.open_archive(reader, ["large.bin"]), resolver)

            next(archive_stream)
            archive_stream.close()
            deadline = time.monotonic() + 2
            while any(thread.name == "chfs-zip-producer" for thread in threading.enumerate()):
                if time.monotonic() >= deadline:
                    self.fail("客户端中断后 ZIP 生产线程没有退出")
                time.sleep(0.02)


if __name__ == "__main__":
    unittest.main()
