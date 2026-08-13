from __future__ import annotations

import unittest

from chfs.gui.app import CHFSApplication


class AuditDisplayTests(unittest.TestCase):
    """审计表必须完整展示单路径、多路径以及复制/移动目标。"""

    def test_single_path_includes_file_space(self) -> None:
        text = CHFSApplication._audit_action_text(
            {
                "action": "file.download",
                "details": {"space": "computer", "path": "C/Users/admin/Downloads/a.zip"},
            }
        )
        self.assertEqual(text, "下载文件 · [此电脑] C/Users/admin/Downloads/a.zip")

    def test_archive_lists_every_selected_path(self) -> None:
        text = CHFSApplication._audit_action_text(
            {
                "action": "archive.download",
                "details": {"space": "shared", "paths": ["资料", "图片/原图"]},
            }
        )
        self.assertEqual(text, "打包下载 · [共享目录] 资料；图片/原图")

    def test_copy_lists_sources_and_destination(self) -> None:
        text = CHFSApplication._audit_action_text(
            {
                "action": "file.copy",
                "details": {
                    "space": "shared",
                    "sources": ["资料/a.txt", "资料/b.txt"],
                    "destination": "备份",
                },
            }
        )
        self.assertEqual(
            text,
            "复制文件 · [共享目录] 源：资料/a.txt；资料/b.txt → 目标：备份",
        )

    def test_reordered_visible_columns_keep_hidden_column_position(self) -> None:
        order = CHFSApplication._merge_log_column_order(
            ["time", "actor", "action", "ip", "mac", "result"],
            ["action", "time", "ip", "mac", "result"],
        )
        self.assertEqual(order, ["action", "actor", "time", "ip", "mac", "result"])


if __name__ == "__main__":
    unittest.main()
