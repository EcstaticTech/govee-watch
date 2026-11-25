from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from govee_watch.config import DEFAULT_BLE_WRITE_CHAR, settings

SCAN_ONLY_MODELS: frozenset[str] = frozenset({"H612F"})


@dataclass(frozen=True)
class DeviceRecord:
    """Registered Govee BLE device metadata from devices.json."""

    mac: str
    name: str
    model: str
    role: str = "power_target"
    location: str = ""
    write_char: str = DEFAULT_BLE_WRITE_CHAR
    packet_profile: str | dict[str, Any] = "default"
    notes: str = ""
    away_seconds: int | None = None

    @property
    def controllable(self) -> bool:
        """Return True if the device is eligible for control commands."""
        return (
            self.role.strip().lower() != "scan_only"
            and self.model.strip().upper() not in SCAN_ONLY_MODELS
        )


@dataclass
class DeviceRegistry:
    """Read-only in-memory index of registered devices."""

    devices: list[DeviceRecord] = field(default_factory=list)
    _by_mac: dict[str, DeviceRecord] = field(init=False, repr=False, default_factory=dict)
    _by_name: dict[str, DeviceRecord] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._by_mac = {d.mac.upper(): d for d in self.devices}
        self._by_name = {d.name.strip().lower(): d for d in self.devices if d.name.strip()}

    def get_by_mac(self, mac: str) -> DeviceRecord | None:
        """Look up a device by MAC address (case-insensitive)."""
        return self._by_mac.get(mac.strip().upper())

    def get_by_name(self, name: str) -> DeviceRecord | None:
        """Look up a device by its configured name (case-insensitive)."""
        return self._by_name.get(name.strip().lower())

    def resolve(self, target: str) -> DeviceRecord | None:
        """Resolve a MAC address, device name, or unique model name."""
        cleaned = target.strip()
        if not cleaned:
            return None

        by_mac = self.get_by_mac(cleaned)
        if by_mac is not None:
            return by_mac

        by_name = self.get_by_name(cleaned)
        if by_name is not None:
            return by_name

        upper = cleaned.upper()
        model_matches = [d for d in self.devices if d.model.upper() == upper]
        if len(model_matches) == 1:
            return model_matches[0]
        return None

    def list_devices(self) -> list[DeviceRecord]:
        """Return all registered devices in file order."""
        return list(self.devices)


def load_registry(path: Path | None = None) -> DeviceRegistry:
    """Load devices.json in read-only mode. Return an empty registry if missing."""
    target_path = path if path is not None else settings.devices_path
    if not target_path.exists():
        return DeviceRegistry()

    raw = json.loads(target_path.read_text(encoding="utf-8"))
    entries = raw.get("devices", []) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError(f"Invalid registry format in {target_path}: expected a list of devices")

    records: list[DeviceRecord] = []
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError(f"Invalid device entry in {target_path}: expected object")
        mac = str(item["mac"]).strip().upper()
        name = str(item["name"]).strip()
        model = str(item["model"]).strip().upper()
        away_raw = item.get("away_seconds")
        records.append(
            DeviceRecord(
                mac=mac,
                name=name,
                model=model,
                role=str(item.get("role", "power_target")).strip(),
                location=str(item.get("location", "")).strip(),
                write_char=str(item.get("write_char") or settings.ble_write_char).strip(),
                packet_profile=item.get("packet_profile", "default"),
                notes=str(item.get("notes", "")).strip(),
                away_seconds=int(away_raw) if away_raw is not None else None,
            )
        )
    return DeviceRegistry(records)
