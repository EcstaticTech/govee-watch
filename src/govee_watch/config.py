import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BLE_WRITE_CHAR = "00010203-0405-0607-0809-0a0b0c0d2b11"


@dataclass(frozen=True)
class Settings:
    scan_seconds: int = int(os.getenv("SCAN_SECONDS", "10"))
    target_models: tuple[str, ...] = tuple(
        m.strip().upper()
        for m in os.getenv("TARGET_MODELS", "H6008,H6001,H612F").split(",")
        if m.strip()
    )
    enable_lan_probe: bool = os.getenv("ENABLE_LAN_PROBE", "true").lower() == "true"
    ble_write_char: str = os.getenv("GOVEE_BLE_WRITE_CHAR", DEFAULT_BLE_WRITE_CHAR)
    ble_connect_timeout: float = float(os.getenv("GOVEE_BLE_CONNECT_TIMEOUT", "10.0"))


settings = Settings()