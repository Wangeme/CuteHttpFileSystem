from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chfs.config import AppConfig
from chfs.errors import InvalidConfigurationError
from chfs.models import Permission


class AppConfigTests(unittest.TestCase):
    def test_default_mode_is_open_without_authentication(self) -> None:
        config = AppConfig.from_dict({"share_root": "data"}, base_dir=Path.cwd())
        self.assertEqual(
            config.guest_permissions,
            frozenset({Permission.READ, Permission.WRITE, Permission.DELETE}),
        )

    def test_relative_paths_and_networks_are_normalized(self) -> None:
        base = Path(tempfile.gettempdir()).resolve()
        config = AppConfig.from_dict(
            {
                "share_root": "data",
                "audit_log": "logs/audit.jsonl",
                "allow_networks": ["192.168.1.8/24"],
                "guest_permissions": ["read"],
            },
            base_dir=base,
        )
        self.assertEqual(config.share_root, base / "data")
        self.assertEqual(config.allow_networks, ("192.168.1.0/24",))
        self.assertEqual(config.guest_permissions, frozenset({Permission.READ}))

    def test_unknown_key_and_boolean_port_are_rejected(self) -> None:
        with self.assertRaises(InvalidConfigurationError):
            AppConfig.from_dict({"share_root": "data", "typo": 1}, base_dir=Path.cwd())
        with self.assertRaises(InvalidConfigurationError):
            AppConfig.from_dict({"share_root": "data", "port": True}, base_dir=Path.cwd())

    def test_tls_files_must_be_configured_as_existing_pair(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with self.assertRaises(InvalidConfigurationError):
                AppConfig.from_dict({"share_root": "data", "tls_certificate": "cert.pem"}, base_dir=base)
            (base / "cert.pem").write_text("certificate", encoding="utf-8")
            (base / "key.pem").write_text("key", encoding="utf-8")
            config = AppConfig.from_dict(
                {"share_root": "data", "tls_certificate": "cert.pem", "tls_private_key": "key.pem"},
                base_dir=base,
            )
            self.assertEqual(config.tls_certificate, base / "cert.pem")


if __name__ == "__main__":
    unittest.main()
