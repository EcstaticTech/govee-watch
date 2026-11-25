from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

PACKET_LENGTH = 20
HEAD_BYTE = 0x33
CMD_POWER = 0x01
CMD_BRIGHTNESS = 0x04
CMD_COLOR = 0x05
DEFAULT_COLOR_MODE = 0x02


@dataclass(frozen=True)
class PacketProfile:
    """Per-device BLE packet parameters."""

    power_cmd: int = CMD_POWER
    brightness_cmd: int = CMD_BRIGHTNESS
    brightness_max: int = 0xFF
    color_cmd: int = CMD_COLOR
    color_mode: int = DEFAULT_COLOR_MODE

    @classmethod
    def from_raw(cls, raw: str | Mapping[str, Any] | None) -> PacketProfile:
        """Build a PacketProfile from a device's packet_profile field."""
        if raw is None or isinstance(raw, str):
            return cls()
        return cls(
            power_cmd=int(raw.get("power_cmd", CMD_POWER)),
            brightness_cmd=int(raw.get("brightness_cmd", CMD_BRIGHTNESS)),
            brightness_max=int(raw.get("brightness_max", 0xFF)),
            color_cmd=int(raw.get("color_cmd", CMD_COLOR)),
            color_mode=int(raw.get("color_mode", DEFAULT_COLOR_MODE)),
        )


def build_frame(payload: bytes | list[int]) -> bytes:
    """Pad payload to 19 bytes and append the 1-byte XOR checksum."""
    if len(payload) > PACKET_LENGTH - 1:
        raise ValueError(f"Payload too long: {len(payload)} bytes (max {PACKET_LENGTH - 1})")
    body = bytes(payload) + bytes((PACKET_LENGTH - 1) - len(payload))
    checksum = 0
    for byte in body:
        checksum ^= byte
    return body + bytes([checksum & 0xFF])


def parse_hex_color(value: str) -> tuple[int, int, int]:
    """Parse RRGGBB or #RRGGBB into an (r, g, b) tuple."""
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        raise ValueError(f"Invalid hex color '{value}': expected 6 hex digits (RRGGBB)")
    try:
        r = int(cleaned[0:2], 16)
        g = int(cleaned[2:4], 16)
        b = int(cleaned[4:6], 16)
    except ValueError as exc:
        raise ValueError(f"Invalid hex color '{value}': non-hex character") from exc
    return r, g, b


def build_power_packet(
    turn_on: bool,
    *,
    profile: str | Mapping[str, Any] | PacketProfile | None = None,
) -> bytes:
    """Build a 20-byte Govee BLE power packet with XOR checksum."""
    prof = profile if isinstance(profile, PacketProfile) else PacketProfile.from_raw(profile)
    state = 0x01 if turn_on else 0x00
    return build_frame([HEAD_BYTE, prof.power_cmd, state])


def build_brightness_packet(
    percent: int,
    *,
    profile: str | Mapping[str, Any] | PacketProfile | None = None,
) -> bytes:
    """Build a 20-byte Govee BLE brightness packet for 0..100 percent."""
    if not 0 <= percent <= 100:
        raise ValueError(f"Brightness must be between 0 and 100, got {percent}")
    prof = profile if isinstance(profile, PacketProfile) else PacketProfile.from_raw(profile)
    scaled = round((percent / 100) * prof.brightness_max)
    return build_frame([HEAD_BYTE, prof.brightness_cmd, scaled & 0xFF])


def build_color_packet(
    r: int,
    g: int,
    b: int,
    *,
    profile: str | Mapping[str, Any] | PacketProfile | None = None,
) -> bytes:
    """Build a 20-byte Govee BLE RGB color packet."""
    for channel, name in ((r, "red"), (g, "green"), (b, "blue")):
        if not 0 <= channel <= 255:
            raise ValueError(f"RGB {name} channel must be 0..255, got {channel}")
    prof = profile if isinstance(profile, PacketProfile) else PacketProfile.from_raw(profile)
    return build_frame([HEAD_BYTE, prof.color_cmd, prof.color_mode, r, g, b])
