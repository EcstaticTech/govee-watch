from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from bleak import BleakClient
from bleak.exc import BleakError

from govee_watch.config import settings
from govee_watch.logging_utils import configure_logging, log_event
from govee_watch.protocol import (
    build_brightness_packet,
    build_color_packet,
    build_power_packet,
    parse_hex_color,
)
from govee_watch.registry import DeviceRegistry, load_registry

logger = logging.getLogger("govee_watch.controller")


def resolve_target(
    target: str,
    *,
    registry: DeviceRegistry | None = None,
    char_override: str | None = None,
) -> tuple[str, str, str | dict[str, Any]]:
    """Resolve a device name, model, or MAC to (mac, write_char, packet_profile)."""
    reg = registry if registry is not None else load_registry()
    record = reg.resolve(target)
    if record is not None:
        if not record.controllable:
            raise ValueError(
                f"Device '{record.name}' ({record.mac}, model={record.model}) is scan-only"
            )
        return record.mac, char_override or record.write_char, record.packet_profile

    cleaned = target.strip()
    if ":" in cleaned or "-" in cleaned:
        return cleaned.upper(), char_override or settings.ble_write_char, "default"

    raise ValueError(
        f"Unknown device '{target}'. Add it to {settings.devices_path} or pass a MAC address."
    )


def resolve_address(target: str, *, registry: DeviceRegistry | None = None) -> str:
    """Resolve a known device name/model or return a MAC address."""
    address, _, _ = resolve_target(target, registry=registry)
    return address


async def send_packets(
    target: str,
    packets: list[bytes],
    *,
    char_uuid: str | None = None,
    timeout: float | None = None,
    use_response: bool = False,
    registry: DeviceRegistry | None = None,
    retries: int | None = None,
    backoff: float | None = None,
    client_factory: Callable[..., Any] = BleakClient,
) -> list[bytes]:
    """Connect to a Govee BLE device and write packets with retry and backoff."""
    address, resolved_char, _ = resolve_target(target, registry=registry, char_override=char_uuid)
    connect_timeout = timeout if timeout is not None else settings.ble_connect_timeout
    max_attempts = max(1, retries if retries is not None else settings.ble_retries)
    base_backoff = max(0.0, backoff if backoff is not None else settings.ble_retry_backoff)

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        step = "connect"
        try:
            async with client_factory(address, timeout=connect_timeout) as client:
                if not client.is_connected:
                    raise RuntimeError(f"Failed to connect to {address}")
                step = "write"
                for packet in packets:
                    await client.write_gatt_char(resolved_char, packet, response=use_response)
            log_event(
                logger,
                logging.INFO,
                "BLE command succeeded",
                device=target,
                mac=address,
                step=step,
                attempt=attempt,
                packets=len(packets),
            )
            return packets
        except (BleakError, TimeoutError, OSError, RuntimeError) as exc:
            last_error = exc
            log_event(
                logger,
                logging.WARNING if attempt < max_attempts else logging.ERROR,
                "BLE command attempt failed",
                device=target,
                mac=address,
                step=step,
                attempt=attempt,
                max_attempts=max_attempts,
                reason=str(exc),
            )
            if attempt < max_attempts and base_backoff > 0:
                await asyncio.sleep(base_backoff * (2 ** (attempt - 1)))

    assert last_error is not None
    raise last_error


async def set_power(
    target: str,
    turn_on: bool,
    *,
    char_uuid: str | None = None,
    timeout: float | None = None,
    use_response: bool = False,
    registry: DeviceRegistry | None = None,
    retries: int | None = None,
    backoff: float | None = None,
) -> bytes:
    """Connect to a Govee BLE device and send an ON or OFF power command."""
    _, _, profile = resolve_target(target, registry=registry, char_override=char_uuid)
    packet = build_power_packet(turn_on, profile=profile)
    await send_packets(
        target,
        [packet],
        char_uuid=char_uuid,
        timeout=timeout,
        use_response=use_response,
        registry=registry,
        retries=retries,
        backoff=backoff,
    )
    return packet


async def set_brightness(
    target: str,
    percent: int,
    *,
    char_uuid: str | None = None,
    timeout: float | None = None,
    use_response: bool = False,
    registry: DeviceRegistry | None = None,
    retries: int | None = None,
    backoff: float | None = None,
) -> bytes:
    """Connect to a Govee BLE device and set brightness (0..100)."""
    _, _, profile = resolve_target(target, registry=registry, char_override=char_uuid)
    packet = build_brightness_packet(percent, profile=profile)
    await send_packets(
        target,
        [packet],
        char_uuid=char_uuid,
        timeout=timeout,
        use_response=use_response,
        registry=registry,
        retries=retries,
        backoff=backoff,
    )
    return packet


async def set_color(
    target: str,
    hex_color: str,
    *,
    char_uuid: str | None = None,
    timeout: float | None = None,
    use_response: bool = False,
    registry: DeviceRegistry | None = None,
    retries: int | None = None,
    backoff: float | None = None,
) -> bytes:
    """Connect to a Govee BLE device and set RGB color (RRGGBB)."""
    _, _, profile = resolve_target(target, registry=registry, char_override=char_uuid)
    r, g, b = parse_hex_color(hex_color)
    packet = build_color_packet(r, g, b, profile=profile)
    await send_packets(
        target,
        [packet],
        char_uuid=char_uuid,
        timeout=timeout,
        use_response=use_response,
        registry=registry,
        retries=retries,
        backoff=backoff,
    )
    return packet


@dataclass
class GoveeController:
    """BLE transport controller for registered Govee BLE bulbs."""

    write_char: str | None = None
    timeout: float = field(default_factory=lambda: settings.ble_connect_timeout)
    registry: DeviceRegistry = field(default_factory=load_registry)
    retries: int = field(default_factory=lambda: settings.ble_retries)
    backoff: float = field(default_factory=lambda: settings.ble_retry_backoff)
    last_known_power: dict[str, bool] = field(default_factory=dict)

    async def turn_on(self, target: str) -> bytes:
        address, _, _ = resolve_target(target, registry=self.registry, char_override=self.write_char)
        packet = await set_power(
            address,
            True,
            char_uuid=self.write_char,
            timeout=self.timeout,
            registry=self.registry,
            retries=self.retries,
            backoff=self.backoff,
        )
        self.last_known_power[address] = True
        return packet

    async def turn_off(self, target: str) -> bytes:
        address, _, _ = resolve_target(target, registry=self.registry, char_override=self.write_char)
        packet = await set_power(
            address,
            False,
            char_uuid=self.write_char,
            timeout=self.timeout,
            registry=self.registry,
            retries=self.retries,
            backoff=self.backoff,
        )
        self.last_known_power[address] = False
        return packet

    async def set_brightness(self, target: str, percent: int) -> bytes:
        return await set_brightness(
            target,
            percent,
            char_uuid=self.write_char,
            timeout=self.timeout,
            registry=self.registry,
            retries=self.retries,
            backoff=self.backoff,
        )

    async def set_color(self, target: str, hex_color: str) -> bytes:
        return await set_color(
            target,
            hex_color,
            char_uuid=self.write_char,
            timeout=self.timeout,
            registry=self.registry,
            retries=self.retries,
            backoff=self.backoff,
        )


async def _cli() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Control Govee BLE bulbs via govee-watch")
    parser.add_argument(
        "target",
        help="Bulb name, model (if unique), or MAC address (for example, A4:C1:38:9D:BB:76)",
    )
    power_group = parser.add_mutually_exclusive_group()
    power_group.add_argument("--on", action="store_true", help="Turn bulb ON")
    power_group.add_argument("--off", action="store_true", help="Turn bulb OFF")
    parser.add_argument("--color", default=None, help="Set RGB color in RRGGBB hex")
    parser.add_argument("--brightness", type=int, default=None, help="Set brightness (0..100)")
    parser.add_argument("--char", default=None, help="Override BLE write characteristic UUID")
    args = parser.parse_args()

    if not any([args.on, args.off, args.color is not None, args.brightness is not None]):
        parser.error("Specify at least one of --on, --off, --color, or --brightness")

    reg = load_registry()
    address, _, profile = resolve_target(args.target, registry=reg, char_override=args.char)
    packets: list[tuple[str, bytes]] = []

    if args.on:
        packets.append(("ON", build_power_packet(True, profile=profile)))
    elif args.off:
        packets.append(("OFF", build_power_packet(False, profile=profile)))

    if args.brightness is not None:
        packets.append(
            (f"BRIGHTNESS({args.brightness}%)", build_brightness_packet(args.brightness, profile=profile))
        )

    if args.color is not None:
        r, g, b = parse_hex_color(args.color)
        packets.append((f"COLOR({r},{g},{b})", build_color_packet(r, g, b, profile=profile)))

    await send_packets(
        args.target,
        [pkt for _, pkt in packets],
        char_uuid=args.char,
        registry=reg,
    )
    for label, pkt in packets:
        print(f"Sent {label} to {args.target} ({address}) | pkt={pkt.hex()}")


if __name__ == "__main__":
    asyncio.run(_cli())
