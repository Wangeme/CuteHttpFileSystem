from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chfs.errors import InvalidPathError, PermissionDeniedError
from chfs.models import Permission, Principal
from chfs.shared_text import MAX_SHARED_TEXT_BYTES, SharedTextStore


class SharedTextStoreTests(unittest.TestCase):
    def test_update_is_persisted_and_reloaded(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state" / "shared-text.json"
            writer = Principal("writer", frozenset({Permission.READ, Permission.WRITE}), True)
            saved = SharedTextStore(path).update(writer, "电脑到手机")
            self.assertEqual(saved["revision"], 1)
            self.assertEqual(SharedTextStore(path).read(writer)["text"], "电脑到手机")

    def test_permissions_and_utf8_limit_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = SharedTextStore(Path(folder) / "shared-text.json")
            reader = Principal("reader", frozenset({Permission.READ}), True)
            writer = Principal("writer", frozenset({Permission.WRITE}), True)
            self.assertEqual(store.read(reader)["text"], "")
            with self.assertRaises(PermissionDeniedError):
                store.update(reader, "no")
            with self.assertRaises(PermissionDeniedError):
                store.read(writer)
            with self.assertRaises(InvalidPathError):
                store.update(writer, "中" * (MAX_SHARED_TEXT_BYTES // 3 + 1))


if __name__ == "__main__":
    unittest.main()
