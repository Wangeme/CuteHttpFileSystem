from __future__ import annotations

import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path

from chfs.config import AppConfig
from chfs.gui.controller import ServerController, discover_urls


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
            self.assertTrue(controller.stop())
            self.assertEqual(controller.state, "stopped")
            self.assertIn("starting", states)
            self.assertIn("stopped", states)

    def test_discover_urls_formats_ipv6_and_fixed_host(self) -> None:
        self.assertEqual(discover_urls("127.0.0.1", 8080), ["http://127.0.0.1:8080"])
        self.assertEqual(discover_urls("::1", 8080), ["http://[::1]:8080"])
        self.assertEqual(discover_urls("127.0.0.1", 8443, https=True), ["https://127.0.0.1:8443"])

    def test_lan_addresses_are_prioritized_over_loopback_and_link_local(self) -> None:
        from unittest.mock import patch

        addresses = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.1.2", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fd00::2", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.8", 0)),
        ]
        with patch("chfs.gui.controller.socket.getaddrinfo", return_value=addresses):
            urls = discover_urls("0.0.0.0", 8080)
        self.assertEqual(urls[0], "http://192.168.1.8:8080")
        self.assertEqual(urls[-1], "http://169.254.1.2:8080")


if __name__ == "__main__":
    unittest.main()
