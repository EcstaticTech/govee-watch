import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from govee_watch.presence import PresenceStore, log_unknown_device


class TestPresenceStore(unittest.TestCase):
    def test_record_seen_rolling_avg_and_away_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PresenceStore(Path(tmp) / "presence.db", rolling_window=2)
            t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
            store.record_seen("a4:c1:38:9d:bb:76", -60, "Govee_H6001_BB76", seen_at=t0)
            store.record_seen(
                "A4:C1:38:9D:BB:76",
                -80,
                "Govee_H6001_BB76",
                seen_at=t0 + timedelta(seconds=10),
            )
            store.record_seen(
                "A4:C1:38:9D:BB:76",
                -70,
                "Govee_H6001_BB76",
                seen_at=t0 + timedelta(seconds=20),
            )

            present = store.get_status(
                "A4:C1:38:9D:BB:76",
                away_seconds=60,
                now=t0 + timedelta(seconds=50),
            )
            self.assertIsNotNone(present)
            self.assertEqual(present["ad_count"], 3)
            self.assertEqual(present["last_rssi"], -70)
            self.assertEqual(present["avg_rssi"], -75.0)
            self.assertTrue(present["is_present"])

            away = store.get_status(
                "A4:C1:38:9D:BB:76",
                away_seconds=60,
                now=t0 + timedelta(seconds=90),
            )
            self.assertFalse(away["is_present"])

    def test_prune_old_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PresenceStore(Path(tmp) / "presence.db")
            now = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
            store.record_seen("A4:C1:38:9D:BB:76", -65, seen_at=now - timedelta(days=45))
            store.record_seen("A4:C1:38:9D:BB:76", -60, seen_at=now - timedelta(days=5))

            deleted = store.prune_old_samples(retention_days=30, now=now)
            self.assertEqual(deleted, 1)

    def test_log_unknown_device(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "unknown_devices.jsonl"
            log_unknown_device(
                "60:74:f4:69:0d:58",
                "ihoment_H6008_0D58",
                -62,
                {1: b"\x01\x02"},
                log_path=log_path,
            )
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["mac"], "60:74:F4:69:0D:58")
            self.assertEqual(payload["promotion_template"]["model"], "H6008")


if __name__ == "__main__":
    unittest.main()
