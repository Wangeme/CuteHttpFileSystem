from __future__ import annotations

import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import tkinter as tk

from chfs.config import AppConfig
from chfs.gui.app import CHFSApplication


def _free_port() -> int:
    """向系统申请一个暂未占用的本机 TCP 端口。"""

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class GUIAutoSaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.config_path = self.base / "config.json"
        try:
            self.app = CHFSApplication(self.config_path, auto_start=False)
        except tk.TclError as exc:
            self.temporary_directory.cleanup()
            self.skipTest(f"当前环境无法创建 Tk 窗口：{exc}")
        self.app.withdraw()

    def tearDown(self) -> None:
        app = getattr(self, "app", None)
        if app is not None:
            if app.controller.state != "stopped":
                app.controller.stop()
            app.destroy()
        self.temporary_directory.cleanup()

    def _pump_events(self, timeout: float, condition: object | None = None) -> bool:
        """驱动 Tk 定时器，直到条件满足或超时。"""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.update()
            if callable(condition) and condition():
                return True
            time.sleep(0.02)
        self.app.update()
        return bool(callable(condition) and condition())

    def test_valid_edit_is_debounced_and_invalid_intermediate_input_is_not_saved(self) -> None:
        port = _free_port()
        self.app.host_var.set("127.0.0.1")
        self.app.port_var.set(str(port))

        self.assertTrue(self._pump_events(2.0, self.config_path.exists))
        self.assertEqual(AppConfig.load(self.config_path).port, port)

        self.app.port_var.set("")
        self._pump_events(1.0)

        self.assertEqual(AppConfig.load(self.config_path).port, port)
        self.assertIn("尚未保存", self.app.auto_save_status_var.get())

    def test_running_service_restarts_once_with_latest_saved_port(self) -> None:
        first_port = _free_port()
        second_port = _free_port()
        self.app.host_var.set("127.0.0.1")
        self.app.port_var.set(str(first_port))
        self.assertTrue(self._pump_events(2.0, self.config_path.exists))
        self.app._start_server()
        self.assertTrue(self.app.controller.wait_until_started())

        self.app.port_var.set(str(second_port))
        self.assertTrue(
            self._pump_events(
                8.0,
                lambda: (
                    self.app.controller.state == "running"
                    and self.app.config.port == second_port
                    and not self.app._pending_config_restart
                ),
            )
        )
        with urllib.request.urlopen(
            f"http://127.0.0.1:{second_port}/api/health",
            timeout=3,
        ) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(AppConfig.load(self.config_path).port, second_port)


class _FakeVariable:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class _FakeController:
    def __init__(self) -> None:
        self.state = "running"
        self.stop_called = threading.Event()
        self.started_with: AppConfig | None = None

    def stop(self) -> bool:
        self.state = "stopped"
        self.stop_called.set()
        return True

    def start(self, config: AppConfig) -> bool:
        self.started_with = config
        self.state = "running"
        return True


class _AutoSaveHarness:
    """不创建窗口，仅复用 GUI 的保存和重启状态机做确定性测试。"""

    _save_config = CHFSApplication._save_config
    _request_config_restart = CHFSApplication._request_config_restart
    _advance_config_restart = CHFSApplication._advance_config_restart


class GUIAutoSaveStateMachineTests(unittest.TestCase):
    def test_changed_config_is_saved_then_running_service_uses_latest_value(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            current = AppConfig(share_root=base / "shared", port=8080)
            updated = AppConfig(share_root=base / "shared", port=9090)
            harness = _AutoSaveHarness()
            harness.config_path = base / "config.json"
            harness.config = current
            harness.controller = _FakeController()
            harness.auto_save_status_var = _FakeVariable()
            harness._pending_config_restart = False
            harness._restart_stop_requested = False
            harness._build_config = lambda: updated

            self.assertTrue(
                harness._save_config(
                    quiet=True,
                    restart_running=True,
                    automatic=True,
                )
            )
            self.assertTrue(harness.controller.stop_called.wait(timeout=2))
            self.assertEqual(AppConfig.load(harness.config_path), updated)
            self.assertTrue(harness._pending_config_restart)

            self.assertEqual(harness._advance_config_restart(), "running")
            self.assertIs(harness.controller.started_with, updated)
            self.assertFalse(harness._pending_config_restart)
            self.assertEqual(harness.auto_save_status_var.value, "最新配置已应用")


if __name__ == "__main__":
    unittest.main()
