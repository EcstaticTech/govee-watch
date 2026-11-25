import asyncio
from bleak import BleakScanner
from govee_watch.config import settings


async def scan_ble(duration: int) -> None:
    print(f"Scanning BLE for {duration}s...")
    devices = await BleakScanner.discover(timeout=duration, return_adv=True)

    for address, (device, adv) in devices.items():
        name = device.name or adv.local_name or "Unknown"
        if any(model in name.upper() for model in settings.target_models) or "GOVEE" in name.upper():
            print(f"{address} | name={name} | rssi={adv.rssi} | mfg={dict(adv.manufacturer_data)}")


if __name__ == "__main__":
    asyncio.run(scan_ble(settings.scan_seconds))