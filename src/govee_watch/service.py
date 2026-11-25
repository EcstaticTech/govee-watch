from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
import logging
import signal
from typing import Any, AsyncIterator, Callable

from bleak import BleakClient, BleakScanner

from govee_watch.config import settings
from govee_watch.controller import resolve_target, send_packets
from govee_watch.logging_utils import configure_logging, log_event
from govee_watch.presence import PresenceDebouncer, PresenceStore, log_unknown_device
from govee_watch.protocol import (
    build_brightness_packet,
    build_color_packet,
    build_power_packet,
    parse_hex_color,
)
from govee_watch.registry import DeviceRegistry, load_registry

logger = logging.getLogger("govee_watch.service")
RETENTION_INTERVAL_SECONDS = 86400
UNKNOWN_LOG_DEBOUNCE_SECONDS = 300.0


class RadioState(str, Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    WRITING = "writing"


class GoveeService:
    """Single-owner BLE service with callback scanner, serialized writes, and periodic pruning."""

    def __init__(
        self,
        *,
        registry: DeviceRegistry | None = None,
        store: PresenceStore | None = None,
        lock_timeout: float | None = None,
        write_interval_seconds: float | None = None,
        scanner_factory: Callable[..., Any] = BleakScanner,
        client_factory: Callable[..., Any] = BleakClient,
    ) -> None:
        self.registry = registry if registry is not None else load_registry()
        self.store = store if store is not None else PresenceStore()
        self.debouncer = PresenceDebouncer(
            self.store,
            write_interval_seconds=write_interval_seconds,
        )
        self.lock_timeout = (
            lock_timeout if lock_timeout is not None else settings.radio_lock_timeout
        )
        self.scanner_factory = scanner_factory
        self.client_factory = client_factory

        self.state: RadioState = RadioState.IDLE
        self.power_state: dict[str, bool] = {}
        self._radio_lock = asyncio.Lock()
        self._scanner: Any = None
        self._running = False
        self._stop_event = asyncio.Event()
        self._retention_task: asyncio.Task[None] | None = None
        self._unknown_last_logged: dict[str, datetime] = {}

    @asynccontextmanager
    async def _acquire_radio(self, target_state: RadioState) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self._radio_lock.acquire(), timeout=self.lock_timeout)
        except TimeoutError as exc:
            log_event(
                logger,
                logging.ERROR,
                "Radio lock acquisition timed out",
                requested_state=target_state.value,
                current_state=self.state.value,
                timeout=self.lock_timeout,
            )
            raise TimeoutError(
                f"Timed out after {self.lock_timeout}s waiting for BLE radio lock"
            ) from exc

        self.state = target_state
        try:
            yield
        finally:
            self.state = RadioState.SCANNING if self._scanner is not None else RadioState.IDLE
            self._radio_lock.release()

    def _is_govee_candidate(self, address: str, adv_name: str) -> bool:
        if self.registry.get_by_mac(address) is not None:
            return True
        upper = adv_name.upper()
        return any(model in upper for model in settings.target_models) or "GOVEE" in upper

    def _on_detection(self, device: Any, adv: Any) -> None:
        address = str(device.address).upper()
        adv_name = device.name or getattr(adv, "local_name", None) or "Unknown"
        if not self._is_govee_candidate(address, adv_name):
            return

        rssi = getattr(adv, "rssi", None)
        self.debouncer.observe(address, rssi, adv_name)

        if self.registry.get_by_mac(address) is None:
            now = datetime.now(timezone.utc)
            last = self._unknown_last_logged.get(address)
            if last is None or (now - last).total_seconds() >= UNKNOWN_LOG_DEBOUNCE_SECONDS:
                mfg = dict(getattr(adv, "manufacturer_data", {}) or {})
                log_unknown_device(address, adv_name, rssi, mfg, seen_at=now)
                self._unknown_last_logged[address] = now

    async def _start_scanner_unlocked(self) -> None:
        if self._scanner is not None:
            return
        scanner = self.scanner_factory(detection_callback=self._on_detection)
        await scanner.start()
        self._scanner = scanner
        self.state = RadioState.SCANNING

    async def _stop_scanner_unlocked(self) -> None:
        if self._scanner is None:
            return
        scanner = self._scanner
        self._scanner = None
        try:
            await scanner.stop()
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "Error stopping BLE scanner",
                step="stop_scanner",
                reason=str(exc),
            )

    async def start(self) -> None:
        """Start the callback BLE scanner and daily retention pruning loop."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()

        pruned = self.store.prune_old_samples(settings.retention_days)
        log_event(
            logger,
            logging.INFO,
            "Service starting",
            devices=len(self.registry.list_devices()),
            pruned_samples=pruned,
        )

        async with self._acquire_radio(RadioState.SCANNING):
            await self._start_scanner_unlocked()

        self._retention_task = asyncio.create_task(self._retention_loop())

    async def _retention_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=RETENTION_INTERVAL_SECONDS,
                )
                break
            except TimeoutError:
                deleted = self.store.prune_old_samples(settings.retention_days)
                log_event(
                    logger,
                    logging.INFO,
                    "Periodic retention pruning completed",
                    deleted_samples=deleted,
                    retention_days=settings.retention_days,
                )

    async def shutdown(self) -> None:
        """Stop scanner, flush debounced presence to SQLite, and release resources."""
        if not self._running and self._scanner is None:
            self.debouncer.flush()
            return

        log_event(logger, logging.INFO, "Service shutting down")
        self._running = False
        self._stop_event.set()

        if self._retention_task is not None:
            self._retention_task.cancel()
            try:
                await self._retention_task
            except asyncio.CancelledError:
                pass
            self._retention_task = None

        try:
            async with self._acquire_radio(RadioState.IDLE):
                await self._stop_scanner_unlocked()
        except TimeoutError:
            await self._stop_scanner_unlocked()

        flushed = self.debouncer.flush()
        self.state = RadioState.IDLE
        log_event(logger, logging.INFO, "Service shutdown complete", flushed_devices=flushed)

    async def execute_power(self, target: str, turn_on: bool) -> tuple[str, str]:
        """Serialize a power command through the radio lock."""
        record = self.registry.resolve(target)
        mac, _, profile = resolve_target(target, registry=self.registry)
        packet = build_power_packet(turn_on, profile=profile)

        async with self._acquire_radio(RadioState.WRITING):
            await self._stop_scanner_unlocked()
            try:
                await send_packets(
                    mac,
                    [packet],
                    registry=self.registry,
                    client_factory=self.client_factory,
                )
                self.power_state[mac] = turn_on
            finally:
                if self._running:
                    await self._start_scanner_unlocked()

        name = record.name if record is not None else mac
        return mac, name

    async def execute_color(
        self,
        target: str,
        hex_color: str,
        brightness: int | None = None,
    ) -> tuple[str, str]:
        """Serialize a color (and optional brightness) command through the radio lock."""
        record = self.registry.resolve(target)
        mac, _, profile = resolve_target(target, registry=self.registry)
        r, g, b = parse_hex_color(hex_color)
        packets = [build_color_packet(r, g, b, profile=profile)]
        if brightness is not None:
            packets.append(build_brightness_packet(brightness, profile=profile))

        async with self._acquire_radio(RadioState.WRITING):
            await self._stop_scanner_unlocked()
            try:
                await send_packets(
                    mac,
                    packets,
                    registry=self.registry,
                    client_factory=self.client_factory,
                )
            finally:
                if self._running:
                    await self._start_scanner_unlocked()

        name = record.name if record is not None else mac
        return mac, name

    def list_device_statuses(self) -> list[dict[str, Any]]:
        """Return merged registry + live presence state for all registered devices."""
        results: list[dict[str, Any]] = []
        for dev in self.registry.list_devices():
            threshold = dev.away_seconds if dev.away_seconds is not None else settings.away_seconds
            pres = self.debouncer.get_live_status(dev.mac, away_seconds=threshold)
            results.append(
                {
                    "name": dev.name,
                    "mac": dev.mac,
                    "model": dev.model,
                    "role": dev.role,
                    "location": dev.location,
                    "controllable": dev.controllable,
                    "last_seen": pres["last_seen"] if pres else None,
                    "last_rssi": pres["last_rssi"] if pres else None,
                    "avg_rssi": pres["avg_rssi"] if pres else None,
                    "ad_count": pres["ad_count"] if pres else 0,
                    "is_present": pres["is_present"] if pres else False,
                    "power_state": self.power_state.get(dev.mac),
                }
            )
        return results

    def get_device_status(self, mac_or_name: str) -> dict[str, Any] | None:
        """Return status for one registered device by MAC or name."""
        record = self.registry.resolve(mac_or_name)
        if record is None:
            return None
        for item in self.list_device_statuses():
            if item["mac"] == record.mac:
                return item
        return None


async def _serve() -> None:
    import uvicorn
    from govee_watch.api import create_app

    configure_logging()
    service = GoveeService()
    app = create_app(service)

    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    def _handle_signal(signum: int, _frame: Any) -> None:
        log_event(logger, logging.INFO, "Received shutdown signal", signal=signum)
        server.should_exit = True

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_signal)

    await service.start()
    try:
        await server.serve()
    finally:
        await service.shutdown()


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
