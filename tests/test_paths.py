from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chfs.errors import InvalidPathError
from chfs.paths import SafePathResolver


class SafePathResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "shared"
        self.resolver = SafePathResolver(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_normal_relative_path_is_accepted(self) -> None:
        self.assertEqual(self.resolver.resolve("照片/一.jpg"), self.root / "照片" / "一.jpg")

    def test_parent_absolute_and_drive_paths_are_rejected(self) -> None:
        for value in ("../secret", "a/../../secret", "/etc/passwd", "C:/Windows/system.ini", "C:\\Windows"):
            with self.subTest(value=value), self.assertRaises(InvalidPathError):
                self.resolver.resolve(value)

    def test_root_empty_path_is_accepted(self) -> None:
        self.assertEqual(self.resolver.resolve(""), self.root)


if __name__ == "__main__":
    unittest.main()

