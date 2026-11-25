import argparse
import asyncio
from bleak import BleakClient

TARGET_ADDRESS = "A4:C1:38:9D:BB:76"  # H6001 or H6008 MAC from your scan

# Common Govee-style GATT UUIDs vary by hardware revision; verify via --inspect
# or Nordic/Wireshark capture if writes do not change state.
DEFAULT_WRITE_CHAR = "00010203-0405-0607-0809-0a0b0c0d2b11"


def build_power_packet(turn_on: bool) -> bytes:
    """Build a 20-byte Govee power packet with XOR checksum."""
    cmd_byte = 0x01 if turn_on else 0x00
    body = bytes([0x33, 0x01, cmd_byte]) + bytes(16)  # 19 bytes total
    checksum = 0
    for b in body:
        checksum ^= b
    return body + bytes([checksum & 0xFF])


async def inspect_services(address: str) -> None:
    async with BleakClient(address) as client:
        print(f"Connected={client.is_connected} to {address}")
        for service in client.services:
            print(f"[Service] {service.uuid}: {service.description}")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"  [Char] {char.uuid} (handle={char.handle}) props=[{props}]")


async def send_power(address: str, char_uuid: str, turn_on: bool) -> None:
    packet = build_power_packet(turn_on)
    async with BleakClient(address) as client:
        print(f"Connected={client.is_connected} to {address}")
        # Govee BLE devices often expect write-without-response (response=False)
        await client.write_gatt_char(char_uuid, packet, response=False)
        print(f"Sent {'ON' if turn_on else 'OFF'} packet ({packet.hex()}).")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal Govee BLE power test")
    parser.add_argument("--address", default=TARGET_ADDRESS, help="Bulb MAC address")
    parser.add_argument("--char", default=DEFAULT_WRITE_CHAR, help="GATT characteristic UUID")
    parser.add_argument("--inspect", action="store_true", help="List GATT services/characteristics")
    parser.add_argument("--on", action="store_true", help="Turn bulb ON")
    parser.add_argument("--off", action="store_true", help="Turn bulb OFF")
    args = parser.parse_args()

    if args.inspect:
        await inspect_services(args.address)
    elif args.on or args.off:
        await send_power(args.address, args.char, turn_on=args.on)
    else:
        print("Pass --inspect, --on, or --off.")


if __name__ == "__main__":
    asyncio.run(main())