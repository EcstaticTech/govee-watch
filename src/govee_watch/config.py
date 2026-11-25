import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BLE_WRITE_CHAR = "00010203-0405-0607-0809-0a0b0c0d2b11"


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    scan_seconds: int = int(os.getenv("SCAN_SECONDS", "10"))
    target_models: tuple[str, ...] = tuple(
        m.strip().upper()
        for m in os.getenv("TARGET_MODELS", "H6008,H6001,H612F").split(",")
        if m.strip()
    )
    ble_write_char: str = os.getenv("GOVEE_BLE_WRITE_CHAR", DEFAULT_BLE_WRITE_CHAR)
    ble_connect_timeout: float = float(os.getenv("GOVEE_BLE_CONNECT_TIMEOUT", "10.0"))
    ble_retries: int = int(os.getenv("GOVEE_BLE_RETRIES", "3"))
    ble_retry_backoff: float = float(os.getenv("GOVEE_BLE_RETRY_BACKOFF", "1.0"))
    radio_lock_timeout: float = float(os.getenv("GOVEE_RADIO_LOCK_TIMEOUT", "15.0"))
    presence_write_interval: float = float(os.getenv("GOVEE_PRESENCE_WRITE_INTERVAL", "30.0"))
    data_dir: Path = Path(os.getenv("GOVEE_DATA_DIR", "data"))
    away_seconds: int = int(os.getenv("GOVEE_AWAY_SECONDS", "60"))
    retention_days: int = int(os.getenv("GOVEE_RETENTION_DAYS", "30"))
    api_host: str = os.getenv("GOVEE_API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("GOVEE_API_PORT", "8765"))
    cli_retry_seconds: float = float(os.getenv("GOVEE_CLI_RETRY_SECONDS", "3.0"))

    @property
    def api_base_url(self) -> str:
        return f"http://{self.api_host}:{self.api_port}"

    @property
    def devices_path(self) -> Path:
        return self.data_dir / "devices.json"

    @property
    def presence_db_path(self) -> Path:
        return self.data_dir / "presence.db"

    @property
    def unknown_log_path(self) -> Path:
        return self.data_dir / "unknown_devices.jsonl"


settings = Settings()
