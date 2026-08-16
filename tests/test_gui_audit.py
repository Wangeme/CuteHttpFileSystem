from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tkinter as tk

from chfs.config import AppConfig
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

    def test_single_file_event_exposes_public_path_for_local_actions(self) -> None:
        space, paths = CHFSApplication._audit_event_public_paths(
            {
                "action": "file.upload",
                "details": {"space": "shared", "path": "图片/截图.png"},
            }
        )
        self.assertEqual(space, "shared")
        self.assertEqual(paths, ("图片/截图.png",))

    def test_move_event_exposes_destination_paths_for_local_actions(self) -> None:
        space, paths = CHFSApplication._audit_event_public_paths(
            {
                "action": "file.move",
                "details": {
                    "space": "shared",
                    "sources": ["待整理/a.txt", "待整理/b.txt"],
                    "destination": "归档",
                },
            }
        )
        self.assertEqual(space, "shared")
        self.assertEqual(paths, ("归档/a.txt", "归档/b.txt"))

    def test_non_file_event_has_no_local_action_path(self) -> None:
        self.assertEqual(
            CHFSApplication._audit_event_public_paths(
                {"action": "shared_text.update", "details": {"revision": 2}}
            ),
            ("", ()),
        )

    def test_reordered_visible_columns_keep_hidden_column_position(self) -> None:
        order = CHFSApplication._merge_log_column_order(
            ["time", "actor", "action", "ip", "mac", "result"],
            ["action", "time", "ip", "mac", "result"],
        )
        self.assertEqual(order, ["action", "actor", "time", "ip", "mac", "result"])


class OverviewAuditInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.config_path = self.base / "config.json"
        self.share_root = self.base / "share"
        AppConfig(
            share_root=self.share_root,
            audit_log=self.base / "audit.jsonl",
        ).save(self.config_path)
        try:
            self.app = CHFSApplication(self.config_path, auto_start=False)
        except tk.TclError as exc:
            self.temporary_directory.cleanup()
            self.skipTest(f"当前环境无法创建 Tk 窗口：{exc}")
        self.app.withdraw()

    def tearDown(self) -> None:
        app = getattr(self, "app", None)
        if app is not None:
            app.destroy()
        self.temporary_directory.cleanup()

    def test_recent_operation_menu_and_path_copy_are_wired(self) -> None:
        target = self.share_root / "图片" / "截图.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"png")
        event = {
            "action": "file.upload",
            "details": {"space": "shared", "path": "图片/截图.png"},
        }
        item = self.app.overview_log_tree.insert(
            "",
            "end",
            values=self.app._audit_row_values(event),
        )
        self.app._overview_log_events[item] = event
        self.app.overview_log_tree.selection_set(item)

        self.assertTrue(self.app.overview_log_tree.bind("<Double-Button-1>"))
        self.assertTrue(self.app.overview_log_tree.bind("<Button-3>"))
        self.assertEqual(
            [self.app._overview_log_menu.entrycget(index, "label") for index in range(3)],
            ["打开", "复制路径", "在文件资源管理器中打开"],
        )
        self.assertEqual(self.app._selected_overview_log_paths(), [target.resolve()])
        self.app._copy_overview_log_paths()
        self.assertEqual(self.app.clipboard_get(), str(target.resolve()))


if __name__ == "__main__":
    unittest.main()
