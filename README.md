# govee-watch

BLE presence/advertisement scanner and GATT controller for Govee bulbs on Windows.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

## Configuration

Settings are loaded from environment variables (or a `.env` file) via `govee_watch.config`:

- `SCAN_SECONDS` (default: `10`)
- `TARGET_MODELS` (default: `H6008,H6001,H612F`)
- `ENABLE_LAN_PROBE` (default: `true`)
- `GOVEE_BLE_WRITE_CHAR` (default: `00010203-0405-0607-0809-0a0b0c0d2b11`)
- `GOVEE_BLE_CONNECT_TIMEOUT` (default: `10.0`)

## Usage

### Scan for Govee BLE advertisements

```powershell
python -m govee_watch.scanner
```

### Control a bulb (`ON` / `OFF`)

```powershell
python -m govee_watch.controller H6001 --on
python -m govee_watch.controller H6001 --off
```

You can also pass a MAC address directly (e.g. `60:74:F4:69:0D:57`).
