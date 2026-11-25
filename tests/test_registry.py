import json
import tempfile
import unittest
from pathlib import Path

from govee_watch.controller import resolve_target
from govee_watch.registry import DeviceRecord, DeviceRegistry, load_registry


class TestRegistry(unittest.TestCase):
    def test_load_missing_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = load_registry(Path(tmp) / "missing.json")
            self.assertEqual(reg.list_devices(), [])

    def test_lookup_by_mac_name_and_unique_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devices.json"
            path.write_text(
                json.dumps(
                    {
                        "devices": [
                            {
                                "mac": "a4:c1:38:9d:bb:76",
                                "name": "Desk Lamp",
                                "model": "H6001",
                                "role": "primary_test",
                                "location": "Office",
                            },
                            {
                                "mac": "da:b9:82:46:5e:92",
                                "name": "Unknown Strip",
                                "model": "H612F",
                                "role": "scan_only",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            reg = load_registry(path)
            self.assertEqual(reg.get_by_mac("A4:C1:38:9D:BB:76").name, "Desk Lamp")
            self.assertEqual(reg.get_by_name("desk lamp").mac, "A4:C1:38:9D:BB:76")
            self.assertEqual(reg.resolve("H6001").mac, "A4:C1:38:9D:BB:76")

            scan_only = reg.get_by_name("Unknown Strip")
            self.assertIsNotNone(scan_only)
            self.assertFalse(scan_only.controllable)

            with self.assertRaises(ValueError):
                resolve_target("Unknown Strip", registry=reg)

    def test_ambiguous_model_requires_name_or_mac(self) -> None:
        reg = DeviceRegistry(
            [
                DeviceRecord(mac="60:74:F4:69:0D:57", name="Kitchen 1", model="H6008"),
                DeviceRecord(mac="60:74:F4:69:0D:58", name="Kitchen 2", model="H6008"),
            ]
        )
        self.assertIsNone(reg.resolve("H6008"))
        self.assertEqual(reg.resolve("Kitchen 2").mac, "60:74:F4:69:0D:58")


if __name__ == "__main__":
    unittest.main()
