import asyncio
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from govee_watch.cli import api_request, check_registry_file, main
from govee_watch.controller import send_packets


class _FlakyClient:
    attempts = 0
    written: list[tuple[str, bytes, bool]] = []

    def __init__(self, address: str, timeout: float = 10.0) -> None:
        self.address = address
        self.timeout = timeout
        self.is_connected = True

    async def __aenter__(self):
        type(self).attempts += 1
        if type(self).attempts < 2:
            raise TimeoutError("transient connect timeout")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def write_gatt_char(self, char_uuid: str, packet: bytes, response: bool = False) -> None:
        type(self).written.append((char_uuid, packet, response))


class _FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class TestCliAndRetry(unittest.TestCase):
    def test_send_packets_retries_then_succeeds(self) -> None:
        _FlakyClient.attempts = 0
        _FlakyClient.written = []
        pkt = bytes([0x33, 0x01, 0x01]) + bytes(16) + bytes([0x33])

        asyncio.run(
            send_packets(
                "A4:C1:38:9D:BB:76",
                [pkt],
                retries=3,
                backoff=0.0,
                client_factory=_FlakyClient,
            )
        )
        self.assertEqual(_FlakyClient.attempts, 2)
        self.assertEqual(len(_FlakyClient.written), 1)

    def test_registry_check_detects_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devices.json"
            path.write_text(
                json.dumps(
                    {
                        "devices": [
                            {"mac": "A4:C1:38:9D:BB:76", "name": "Lamp", "model": "H6001"},
                            {"mac": "A4:C1:38:9D:BB:76", "name": "Lamp", "model": "H6001"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            ok, messages = check_registry_file(path)
            self.assertFalse(ok)
            self.assertTrue(any("Duplicate" in m for m in messages))

    def test_api_request_retries_connection_then_succeeds(self) -> None:
        attempts = {"count": 0}

        def _flaky_open(req, timeout=15.0):
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise urllib.error.URLError("connection refused")
            return _FakeHttpResponse({"ok": True})

        res = api_request(
            "GET",
            "/devices",
            base_url="http://127.0.0.1:8765",
            retry_seconds=1.0,
            retry_interval=0.01,
            opener=_flaky_open,
        )
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(res, {"ok": True})

    def test_cli_returns_nonzero_when_service_unreachable(self) -> None:
        with patch(
            "govee_watch.cli.api_request",
            side_effect=RuntimeError("govee-watch service is unreachable"),
        ):
            code = main(["power", "A4:C1:38:9D:BB:76", "--off"])
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
