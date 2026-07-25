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
        self.assertGreater(busy.upload_bytes_per_second, 50_000_000)
        self.assertEqual(busy.download_bytes_per_second, 0)
        self.assertGreater(busy.beats_per_minute, first.beats_per_minute + 70)

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

    def test_short_upload_chunk_gap_does_not_flicker_to_idle(self) -> None:
        model = BandwidthHeartbeatModel()
        upload = {
            "id": "chunked-upload",
            "direction": "upload",
            "status": "uploading",
            "transferred_bytes": 0,
        }
        model.sample([upload], now=1.0)
        upload["transferred_bytes"] = 16 * 1024 * 1024
        active = model.sample([upload], now=1.2)
        upload["status"] = "waiting"
        between_chunks = model.sample([upload], now=1.4)
        self.assertEqual(active.mode, "upload")
        self.assertEqual(between_chunks.mode, "upload")
        self.assertGreater(between_chunks.upload_bytes_per_second, 0)

    def test_upload_and_download_rates_are_reported_separately(self) -> None:
        model = BandwidthHeartbeatModel()
        snapshots = [
            {"id": "up", "direction": "upload", "status": "uploading", "transferred_bytes": 0},
            {"id": "down", "direction": "download", "status": "downloading", "transferred_bytes": 0},
        ]
        model.sample(snapshots, now=1.0)
        snapshots[0]["transferred_bytes"] = 10_000_000
        snapshots[1]["transferred_bytes"] = 20_000_000
        state = model.sample(snapshots, now=1.2)
        self.assertEqual(state.mode, "mixed")
        self.assertGreater(state.upload_bytes_per_second, 0)
        self.assertGreater(state.download_bytes_per_second, state.upload_bytes_per_second)

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
