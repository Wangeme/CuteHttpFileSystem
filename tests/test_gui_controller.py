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


if __name__ == "__main__":
    unittest.main()
