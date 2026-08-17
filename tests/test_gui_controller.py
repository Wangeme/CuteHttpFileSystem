from __future__ import annotations

import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from chfs.config import AppConfig
from chfs.gui.app import CHFSApplication
from chfs.gui.controller import ServerController, discover_urls, group_access_urls


class DesktopSharedTextTests(unittest.TestCase):
    def test_clear_shared_text_does_not_request_confirmation(self) -> None:
        """桌面端清空共享文本应立即执行，不再弹出二次确认框。"""

        widget = MagicMock()
        application = SimpleNamespace(shared_text_widget=widget)
        with patch("chfs.gui.app.messagebox.askyesno") as confirmation:
            CHFSApplication._clear_shared_text(application)

        confirmation.assert_not_called()
        widget.delete.assert_called_once_with("1.0", "end")
        widget.edit_modified.assert_called_once_with(True)
        widget.focus_set.assert_called_once_with()


class ServerControllerTests(unittest.TestCase):
    def test_controller_starts_and_stops_real_server(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            config = AppConfig(share_root=Path(folder) / "shared", host="127.0.0.1", port=port)
            states: list[str] = []
            controller = ServerController(states.append)
            self.assertTrue(controller.start(config))
            self.assertTrue(controller.wait_until_started())
            self.assertEqual(controller.state, "running")
            self.assertFalse(controller.start(config), "运行中不能重复启动")
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                self.assertEqual(response.status, 200)
            saved = controller.update_shared_text("桌面与手机同步")
            self.assertEqual(saved["revision"], 1)
            self.assertEqual(controller.read_shared_text()["text"], "桌面与手机同步")
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/shared-text", timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("桌面与手机同步", response.read().decode("utf-8"))
            self.assertTrue(controller.stop())
            self.assertEqual(controller.state, "stopped")
            self.assertIn("starting", states)
            self.assertIn("stopped", states)

    def test_controller_starts_without_console_streams(self) -> None:
        """窗口版 EXE 中标准输出为空时，Uvicorn 仍应正常启动。"""

        with tempfile.TemporaryDirectory() as folder:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            config = AppConfig(share_root=Path(folder) / "shared", host="127.0.0.1", port=port)
            controller = ServerController()
            with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
                self.assertTrue(controller.start(config))
                self.assertTrue(controller.wait_until_started())
                self.assertEqual(controller.state, "running")
                self.assertTrue(controller.stop())
            self.assertIsNone(controller.last_error)

    def test_discover_urls_formats_ipv6_and_fixed_host(self) -> None:
        self.assertEqual(discover_urls("127.0.0.1", 8080), ["http://127.0.0.1:8080"])
        self.assertEqual(discover_urls("::1", 8080), ["http://[::1]:8080"])
        self.assertEqual(discover_urls("127.0.0.1", 8443, https=True), ["https://127.0.0.1:8443"])

    def test_lan_addresses_are_prioritized_over_loopback_and_link_local(self) -> None:
        addresses = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.1.2", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fd00::2", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.8", 0)),
        ]
        with patch("chfs.gui.controller.socket.getaddrinfo", return_value=addresses):
            urls = discover_urls("0.0.0.0", 8080)
        self.assertEqual(urls, ["http://192.168.1.8:8080", "http://127.0.0.1:8080"])

    def test_ipv6_addresses_are_only_shown_for_ipv6_wildcard(self) -> None:
        addresses = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.8", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fd00::2", 0, 0, 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::2", 0, 0, 3)),
        ]
        with patch("chfs.gui.controller.socket.getaddrinfo", return_value=addresses):
            self.assertEqual(discover_urls("::", 8080), ["http://[fd00::2]:8080", "http://[::1]:8080"])

    def test_access_urls_separate_preferred_lan_from_local_alternatives(self) -> None:
        urls = [
            "http://10.186.56.121:8080",
            "http://172.25.224.1:8080",
            "http://127.0.0.1:8080",
        ]
        lan, local = group_access_urls(urls)
        self.assertEqual(lan, ["http://10.186.56.121:8080"])
        self.assertEqual(local, ["http://172.25.224.1:8080", "http://127.0.0.1:8080"])

    def test_loopback_only_access_remains_available_as_local_address(self) -> None:
        lan, local = group_access_urls(["http://127.0.0.1:8080"])
        self.assertEqual(lan, [])
        self.assertEqual(local, ["http://127.0.0.1:8080"])


if __name__ == "__main__":
    unittest.main()
