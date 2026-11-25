from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from bleak import BleakClient
from govee_watch.config import settings

# Known MACs from your scan; can also pass any MAC directly to set_power/GoveeController.
KNOWN_DEVICES: dict[str, str] = {
    "H6001": "A4:C1:38:9D:BB:76",
    "H6008": "60:74:F4:69:0D:57",
}


def build_power_packet(turn_on: bool) -> bytes:
    """Build a 20-byte Govee BLE power packet with XOR checksum."""
    cmd_byte = 0x01 if turn_on else 0x00
    body = bytes([0x33, 0x01, cmd_byte]) + bytes(16)
    checksum = 0
    for byte in body:
        checksum ^= byte
    return body + bytes([checksum & 0xFF])


def resolve_address(target: str) -> str:
    """Resolve a known model alias (e.g., H6001, H6008) or return a MAC address."""
    normalized = target.strip()
    upper = normalized.upper()
    if upper in KNOWN_DEVICES:
        return KNOWN_DEVICES[upper]
    return normalized


async def set_power(
    target: str,
    turn_on: bool,
    *,
    char_uuid: str | None = None,
    timeout: float | None = None,
    use_response: bool = False,
) -> bytes:
    """Connect to a Govee BLE device and send an ON or OFF power command."""
    address = resolve_address(target)
    characteristic = char_uuid or settings.ble_write_char
    connect_timeout = timeout if timeout is not None else settings.ble_connect_timeout
    packet = build_power_packet(turn_on)

    async with BleakClient(address, timeout=connect_timeout) as client:
        if not client.is_connected:
            raise RuntimeError(f"Failed to connect to {address}")
        await client.write_gatt_char(characteristic, packet, response=use_response)

    return packet


@dataclass
class GoveeController:
    """BLE controller for H6001, H6008, and other Govee BLE bulbs."""

    write_char: str = field(default_factory=lambda: settings.ble_write_char)
    timeout: float = field(default_factory=lambda: settings.ble_connect_timeout)
    last_known_power: dict[str, bool] = field(default_factory=dict)

    async def turn_on(self, target: str) -> bytes:
        address = resolve_address(target)
        packet = await set_power(
            address,
            True,
            char_uuid=self.write_char,
            timeout=self.timeout,
            use_response=False,
        )
        self.last_known_power[address.upper()] = True
        return packet

    async def turn_off(self, target: str) -> bytes:
        address = resolve_address(target)
        packet = await set_power(
            address,
            False,
            char_uuid=self.write_char,
            timeout=self.timeout,
            use_response=False,
        )
        self.last_known_power[address.upper()] = False
        return packet


async def _cli() -> None:
    parser = argparse.ArgumentParser(description="Control Govee BLE bulbs via govee-watch")
    parser.add_argument(
        "target",
        help="Bulb MAC address or model alias (e.g., H6001, H6008, or A4:C1:38:9D:BB:76)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--on", action="store_true", help="Turn bulb ON")
    group.add_argument("--off", action="store_true", help="Turn bulb OFF")
    parser.add_argument("--char", default=None, help="Override BLE write characteristic UUID")
    args = parser.parse_args()

    controller = GoveeController(write_char=args.char or settings.ble_write_char)
    if args.on:
        pkt = await controller.turn_on(args.target)
        print(f"Sent ON to {args.target} ({resolve_address(args.target)}) | pkt={pkt.hex()}")
    else:
        pkt = await controller.turn_off(args.target)
        print(f"Sent OFF to {args.target} ({resolve_address(args.target)}) | pkt={pkt.hex()}")


if __name__ == "__main__":
    asyncio.run(_cli())