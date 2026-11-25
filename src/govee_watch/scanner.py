import asyncio
from bleak import BleakScanner

from govee_watch.config import settings
from govee_watch.presence import PresenceStore, log_unknown_device
from govee_watch.registry import DeviceRegistry, load_registry


def _is_govee_candidate(address: str, adv_name: str, registry: DeviceRegistry) -> bool:
    if registry.get_by_mac(address) is not None:
        return True
    upper = adv_name.upper()
    return any(model in upper for model in settings.target_models) or "GOVEE" in upper


async def scan_ble(
    duration: int,
    *,
    registry: DeviceRegistry | None = None,
    store: PresenceStore | None = None,
) -> None:
    """Scan BLE advertisements, update presence telemetry, and log unknown devices."""
    reg = registry if registry is not None else load_registry()
    presence = store if store is not None else PresenceStore()
    presence.prune_old_samples(settings.retention_days)

    print(f"Scanning BLE for {duration}s...")
    devices = await BleakScanner.discover(timeout=duration, return_adv=True)

    for address, (device, adv) in devices.items():
        adv_name = device.name or adv.local_name or "Unknown"
        if not _is_govee_candidate(address, adv_name, reg):
            continue

        presence.record_seen(address, adv.rssi, adv_name)
        status = presence.get_status(address)
        last_seen = status["last_seen"] if status else "just now"
        record = reg.get_by_mac(address)

        if record is not None:
            loc = f" | loc={record.location}" if record.location else ""
            print(
                f"{record.mac} | name={record.name} | model={record.model}{loc} "
                f"| rssi={adv.rssi} | last_seen={last_seen}"
            )
        else:
            log_unknown_device(address, adv_name, adv.rssi, dict(adv.manufacturer_data))
            print(
                f"{address.upper()} | [UNKNOWN] adv_name={adv_name} "
                f"| rssi={adv.rssi} | mfg={dict(adv.manufacturer_data)}"
            )


if __name__ == "__main__":
    asyncio.run(scan_ble(settings.scan_seconds))
