from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chfs.audit import AuditLogger, resolve_mac_address


class AuditLoggerTests(unittest.TestCase):
    def test_loopback_is_identified_as_local_machine(self) -> None:
        self.assertEqual(resolve_mac_address("127.0.0.1"), "本机")
        self.assertEqual(resolve_mac_address("::1"), "本机")

    def test_event_contains_source_ip_and_mac(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audit.jsonl"
            with patch("chfs.audit.resolve_mac_address", return_value="AA-BB-CC-DD-EE-FF"):
                AuditLogger(path).record(
                    "file.upload",
                    actor="guest",
                    source="192.168.1.20",
                    success=True,
                    path="demo.bin",
                )
            event = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(event["source_ip"], "192.168.1.20")
            self.assertEqual(event["source_mac"], "AA-BB-CC-DD-EE-FF")
            self.assertEqual(event["details"]["path"], "demo.bin")


if __name__ == "__main__":
    unittest.main()
