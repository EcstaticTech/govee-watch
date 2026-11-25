# govee-watch

Version `0.4.0`. Single-owner BLE presence service, localhost API, and web UI for Govee bulbs on Windows.

## Setup

1. Create a virtual environment with `python -m venv .venv`.
2. Activate it with `.\.venv\Scripts\Activate.ps1`.
3. Install the package in editable mode with `pip install -e .`.

## Configuration

Settings load from environment variables or a `.env` file via `govee_watch.config`:

- `TARGET_MODELS` (default: `H6008,H6001,H612F`; `H612F` is scan-only)
- `GOVEE_BLE_WRITE_CHAR` (default: `00010203-0405-0607-0809-0a0b0c0d2b11`)
- `GOVEE_BLE_CONNECT_TIMEOUT` (default: `10.0`)
- `GOVEE_BLE_RETRIES` (default: `3`)
- `GOVEE_BLE_RETRY_BACKOFF` (default: `1.0`)
- `GOVEE_RADIO_LOCK_TIMEOUT` (default: `15.0`)
- `GOVEE_PRESENCE_WRITE_INTERVAL` (default: `30.0`)
- `GOVEE_DATA_DIR` (default: `data`)
- `GOVEE_AWAY_SECONDS` (default: `60`)
- `GOVEE_RETENTION_DAYS` (default: `30`)
- `GOVEE_API_HOST` (default: `127.0.0.1`)
- `GOVEE_API_PORT` (default: `8765`)
- `GOVEE_CLI_RETRY_SECONDS` (default: `3.0`)

## Data directory

Store `devices.json` and `presence.db` in `data/` (gitignored):

- `data/devices.json` — Hand-edited device registry. Tool code reads this file and never writes to it.
- `data/presence.db` — SQLite telemetry store with debounced writes and daily 30-day retention pruning.
- `data/unknown_devices.jsonl` — Log of unregistered BLE advertisements with promotion templates.

## Run the service (Phase 4)

Start the background service directly:

```powershell
python -m govee_watch.service
```

Open the local web UI at `http://127.0.0.1:8765/` or Swagger docs at `http://127.0.0.1:8765/docs`.

### Install under NSSM (Windows Service)

```powershell
nssm install govee-watch "M:\projects\govee-watch\.venv\Scripts\python.exe" "-m govee_watch.service"
nssm set govee-watch AppDirectory "M:\projects\govee-watch"
nssm set govee-watch AppExit Default Restart
nssm start govee-watch
```

## CLI Usage (API Client)

After Phase 4, `govee-watch` calls the local service API on `127.0.0.1:8765`:

```powershell
govee-watch registry-check
govee-watch status --json
govee-watch power "H6001 Lamp" --on
govee-watch color "H6001 Lamp" FF8000 --brightness 75
govee-watch power "H6008 Ceiling Light 3" --off
```

## Run unit tests

```powershell
python -m unittest discover -s tests
```
