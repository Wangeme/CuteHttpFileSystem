from __future__ import annotations

import unittest

from chfs.transfers import TransferRegistry


class TransferRegistryTests(unittest.TestCase):
    def test_download_progress_and_completion_snapshot(self) -> None:
        registry = TransferRegistry(completed_ttl_seconds=10)
        transfer_id = registry.start_download("movie.mkv", "guest", "192.168.0.8", 1000)
        registry.advance(transfer_id, 400)
        active = registry.snapshots()[0]
        self.assertEqual(active["status"], "downloading")
        self.assertEqual(active["transferred_bytes"], 400)
        self.assertGreater(float(active["bytes_per_second"]), 0)

        registry.finish(transfer_id)
        completed = registry.snapshots()[0]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["transferred_bytes"], 1000)


if __name__ == "__main__":
    unittest.main()
