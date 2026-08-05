from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from chfs.errors import PermissionDeniedError, ResourceConflictError, UploadTooLargeError
from chfs.models import Permission, Principal
from chfs.paths import SafePathResolver
from chfs.services import FileService, bytes_chunks


class FileServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "shared"
        resolver = SafePathResolver(self.root)
        self.root = resolver.root
        self.service = FileService(resolver, max_upload_bytes=8)
        self.reader = Principal("reader", frozenset({Permission.READ}), True)
        self.writer = Principal("writer", frozenset({Permission.READ, Permission.WRITE, Permission.DELETE}), True)

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_upload_list_download_and_delete_round_trip(self) -> None:
        entry = await self.service.upload(self.writer, "hello.txt", bytes_chunks([b"hello", b"!"]))
        self.assertEqual(entry.size, 6)
        self.assertEqual([item.name for item in self.service.list_directory(self.reader)], ["hello.txt"])
        self.assertEqual(self.service.open_download(self.reader, "hello.txt").read_bytes(), b"hello!")
        self.service.delete(self.writer, "hello.txt")
        self.assertEqual(self.service.list_directory(self.reader), [])

    async def test_upload_limit_removes_temporary_file(self) -> None:
        with self.assertRaises(UploadTooLargeError):
            await self.service.upload(self.writer, "large.bin", bytes_chunks([b"12345678", b"9"]))
        self.assertFalse((self.root / "large.bin").exists())
        self.assertEqual(list(self.root.glob(".chfs-upload-*")), [])

    async def test_existing_file_requires_explicit_overwrite(self) -> None:
        (self.root / "same.txt").write_bytes(b"old")
        with self.assertRaises(ResourceConflictError):
            await self.service.upload(self.writer, "same.txt", bytes_chunks([b"new"]))
        self.assertEqual((self.root / "same.txt").read_bytes(), b"old")
        await self.service.upload(self.writer, "same.txt", bytes_chunks([b"new"]), overwrite=True)
        self.assertEqual((self.root / "same.txt").read_bytes(), b"new")

    async def test_read_only_principal_cannot_upload(self) -> None:
        with self.assertRaises(PermissionDeniedError):
            await self.service.upload(self.reader, "no.txt", bytes_chunks([b"x"]))

    async def test_directory_creation_and_recursive_delete_are_explicit(self) -> None:
        self.service.create_directory(self.writer, "a/b")
        (self.root / "a" / "b" / "item.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ResourceConflictError):
            self.service.delete(self.writer, "a")
        self.service.delete(self.writer, "a", recursive=True)
        self.assertFalse((self.root / "a").exists())

    async def test_copy_and_move_multiple_entries(self) -> None:
        (self.root / "source").mkdir()
        (self.root / "source" / "a.txt").write_text("a", encoding="utf-8")
        (self.root / "source" / "folder").mkdir()
        (self.root / "source" / "folder" / "b.txt").write_text("b", encoding="utf-8")
        (self.root / "copy-target").mkdir()
        (self.root / "move-target").mkdir()

        copied = self.service.copy_or_move(
            self.writer,
            ["source/a.txt", "source/folder"],
            "copy-target",
        )
        self.assertEqual({item.name for item in copied}, {"a.txt", "folder"})
        self.assertEqual((self.root / "copy-target" / "folder" / "b.txt").read_text(), "b")

        moved = self.service.copy_or_move(
            self.writer,
            ["source/a.txt"],
            "move-target",
            move=True,
        )
        self.assertEqual(moved[0].path, "move-target/a.txt")
        self.assertFalse((self.root / "source" / "a.txt").exists())

    async def test_copy_rejects_existing_target_and_descendant(self) -> None:
        (self.root / "source").mkdir()
        (self.root / "source" / "item.txt").write_text("x", encoding="utf-8")
        (self.root / "target").mkdir()
        (self.root / "target" / "item.txt").write_text("existing", encoding="utf-8")
        with self.assertRaises(ResourceConflictError):
            self.service.copy_or_move(self.writer, ["source/item.txt"], "target")
        with self.assertRaises(ResourceConflictError):
            self.service.copy_or_move(self.writer, ["source"], "source")


if __name__ == "__main__":
    unittest.main()

