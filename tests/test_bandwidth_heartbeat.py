from __future__ import annotations

import unittest

from chfs.gui.bandwidth_heartbeat import BandwidthHeartbeatModel


class BandwidthHeartbeatModelTests(unittest.TestCase):
    def test_upload_delta_drives_pink_heart_frequency(self) -> None:
        model = BandwidthHeartbeatModel(reference_bytes_per_second=100_000_000)
        upload = {
            "id": "phone-upload",
            "direction": "upload",
            "status": "uploading",
            "transferred_bytes": 10_000_000,
        }
        first = model.sample([upload], now=1.0)
        self.assertEqual(first.mode, "upload")
        self.assertGreater(first.beats_per_minute, 34)

        upload["transferred_bytes"] = 60_000_000
        busy = model.sample([upload], now=1.5)
        self.assertEqual(busy.mode, "upload")
        self.assertGreater(busy.bytes_per_second, 75_000_000)
        self.assertGreater(busy.beats_per_minute, first.beats_per_minute + 100)

    def test_download_and_mixed_modes_are_detected(self) -> None:
        model = BandwidthHeartbeatModel()
        download = {
            "id": "download",
            "direction": "download",
            "status": "downloading",
            "transferred_bytes": 0,
        }
        self.assertEqual(model.sample([download], now=1.0).mode, "download")
        upload = {
            "id": "upload",
            "direction": "upload",
            "status": "uploading",
            "transferred_bytes": 0,
        }
        self.assertEqual(model.sample([download, upload], now=1.2).mode, "mixed")

    def test_completed_transfers_return_to_idle(self) -> None:
        model = BandwidthHeartbeatModel()
        transfer = {
            "id": "finished",
            "direction": "download",
            "status": "completed",
            "transferred_bytes": 100,
        }
        state = model.sample([transfer], now=1.0)
        self.assertEqual(state.mode, "idle")
        self.assertEqual(state.bytes_per_second, 0)

    def test_reference_rate_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            BandwidthHeartbeatModel(reference_bytes_per_second=0)
