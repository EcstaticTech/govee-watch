import asyncio
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from govee_watch.api import create_app
from govee_watch.presence import PresenceDebouncer, PresenceStore
from govee_watch.registry import DeviceRecord, DeviceRegistry
from govee_watch.service import GoveeService, RadioState


class _FakeScanner:
    instances: list["_FakeScanner"] = []

    def __init__(self, detection_callback=None) -> None:
        self.callback = detection_callback
        self.started = False
        self.stopped = False
        type(self).instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _FakeClient:
    written: list[tuple[str, str, bytes]] = []

    def __init__(self, address: str, timeout: float = 10.0) -> None:
        self.address = address
        self.is_connected = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def write_gatt_char(self, char_uuid: str, packet: bytes, response: bool = False) -> None:
        type(self).written.append((self.address, char_uuid, packet))


class TestServiceAndApi(unittest.TestCase):
    def test_debouncer_throttles_and_flushes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PresenceStore(Path(tmp) / "presence.db")
            debouncer = PresenceDebouncer(store, write_interval_seconds=30.0, rssi_delta_threshold=10)
            t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)

            # First observation writes immediately
            self.assertTrue(debouncer.observe("A4:C1:38:9D:BB:76", -60, "H6001", seen_at=t0))
            # Within 30s and small delta -> debounced in memory only
            self.assertFalse(
                debouncer.observe("A4:C1:38:9D:BB:76", -62, "H6001", seen_at=t0 + timedelta(seconds=5))
            )
            live = debouncer.get_live_status("A4:C1:38:9D:BB:76", now=t0 + timedelta(seconds=6))
            self.assertEqual(live["ad_count"], 2)
            self.assertEqual(live["last_rssi"], -62)

            # Flush persists pending count to SQLite
            self.assertEqual(debouncer.flush(), 1)
            persisted = store.get_status("A4:C1:38:9D:BB:76", now=t0 + timedelta(seconds=6))
            self.assertEqual(persisted["ad_count"], 2)

    def test_service_lifecycle_lock_timeout_and_shutdown(self) -> None:
        _FakeScanner.instances = []
        _FakeClient.written = []

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                reg = DeviceRegistry(
                    [DeviceRecord(mac="A4:C1:38:9D:BB:76", name="H6001 Lamp", model="H6001")]
                )
                store = PresenceStore(Path(tmp) / "presence.db")
                svc = GoveeService(
                    registry=reg,
                    store=store,
                    lock_timeout=0.05,
                    write_interval_seconds=30.0,
                    scanner_factory=_FakeScanner,
                    client_factory=_FakeClient,
                )
                await svc.start()
                self.assertEqual(svc.state, RadioState.SCANNING)
                self.assertTrue(_FakeScanner.instances[-1].started)

                # Simulate advertisement callback then debounced flush on shutdown
                svc._on_detection(
                    SimpleNamespace(address="A4:C1:38:9D:BB:76", name="H6001"),
                    SimpleNamespace(rssi=-58, local_name="H6001", manufacturer_data={}),
                )
                svc._on_detection(
                    SimpleNamespace(address="A4:C1:38:9D:BB:76", name="H6001"),
                    SimpleNamespace(rssi=-59, local_name="H6001", manufacturer_data={}),
                )

                # Verify lock timeout when lock is already held
                await svc._radio_lock.acquire()
                try:
                    with self.assertRaises(TimeoutError):
                        await svc.execute_power("H6001 Lamp", True)
                finally:
                    svc._radio_lock.release()

                await svc.shutdown()
                self.assertEqual(svc.state, RadioState.IDLE)
                self.assertTrue(_FakeScanner.instances[-1].stopped)
                persisted = store.get_status("A4:C1:38:9D:BB:76")
                self.assertEqual(persisted["ad_count"], 2)

        asyncio.run(_run())

    def test_fastapi_endpoints_and_scan_only_guard(self) -> None:
        _FakeScanner.instances = []
        _FakeClient.written = []
        with tempfile.TemporaryDirectory() as tmp:
            reg = DeviceRegistry(
                [
                    DeviceRecord(mac="A4:C1:38:9D:BB:76", name="H6001 Lamp", model="H6001"),
                    DeviceRecord(
                        mac="DA:B9:82:46:5E:92",
                        name="Unknown H612F",
                        model="H612F",
                        role="scan_only",
                    ),
                ]
            )
            store = PresenceStore(Path(tmp) / "presence.db")
            svc = GoveeService(
                registry=reg,
                store=store,
                scanner_factory=_FakeScanner,
                client_factory=_FakeClient,
            )
            app = create_app(svc)
            client = TestClient(app)

            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/health").json()["status"], "ok")

            devices = client.get("/devices").json()
            self.assertEqual(len(devices), 2)

            power_res = client.post("/power", json={"target": "H6001 Lamp", "on": True})
            self.assertEqual(power_res.status_code, 200)
            self.assertTrue(power_res.json()["ok"])

            color_res = client.post(
                "/color",
                json={"target": "H6001 Lamp", "color": "FF8000", "brightness": 75},
            )
            self.assertEqual(color_res.status_code, 200)

            blocked = client.post("/power", json={"target": "Unknown H612F", "on": True})
            self.assertEqual(blocked.status_code, 400)


if __name__ == "__main__":
    unittest.main()
