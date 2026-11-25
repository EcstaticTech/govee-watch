from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from govee_watch import __version__
from govee_watch.service import GoveeService


class DeviceStatusResponse(BaseModel):
    name: str
    mac: str
    model: str
    role: str
    location: str
    controllable: bool
    last_seen: str | None
    last_rssi: int | None
    avg_rssi: float | None
    ad_count: int
    is_present: bool
    power_state: bool | None


class PowerRequest(BaseModel):
    target: str = Field(..., min_length=1)
    on: bool


class ColorRequest(BaseModel):
    target: str = Field(..., min_length=1)
    color: str = Field(..., min_length=6, max_length=7)
    brightness: int | None = Field(default=None, ge=0, le=100)


class CommandResponse(BaseModel):
    ok: bool
    mac: str
    name: str
    detail: str


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>govee-watch</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 780px; margin: 2rem auto; padding: 0 1rem; background: #11161c; color: #e6edf3; }
  h1 { font-size: 1.4rem; margin-bottom: 1rem; }
  .card { background: #1a212b; border: 1px solid #2b3544; border-radius: 8px; padding: 1rem; margin-bottom: 0.75rem; display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
  .meta { font-size: 0.85rem; color: #9fb0c3; }
  .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }
  .present { background: #1b4332; color: #74c69d; }
  .away { background: #3d2229; color: #f28482; }
  .controls { display: flex; gap: 0.5rem; align-items: center; }
  button { background: #2b3544; color: #e6edf3; border: 1px solid #3f4d61; border-radius: 6px; padding: 0.4rem 0.75rem; cursor: pointer; }
  button:hover { background: #3a475b; }
  input[type="color"] { width: 38px; height: 32px; border: none; background: transparent; cursor: pointer; }
  input[type="range"] { width: 90px; }
  #msg { margin-top: 0.75rem; font-size: 0.85rem; color: #9fb0c3; min-height: 1.2rem; }
</style>
</head>
<body>
  <h1>govee-watch</h1>
  <div id="devices">Loading devices...</div>
  <div id="msg"></div>
<script>
async function loadDevices() {
  const res = await fetch('/devices');
  const items = await res.json();
  const root = document.getElementById('devices');
  root.innerHTML = '';
  for (const d of items) {
    const card = document.createElement('div');
    card.className = 'card';
    const badge = d.is_present ? '<span class="badge present">present</span>' : '<span class="badge away">away</span>';
    const info = document.createElement('div');
    info.innerHTML = `<strong>${d.name}</strong> ${badge}<div class="meta">${d.model} · ${d.mac}${d.location ? ' · ' + d.location : ''} · RSSI: ${d.last_rssi ?? '-'}</div>`;
    card.appendChild(info);

    const ctrl = document.createElement('div');
    ctrl.className = 'controls';
    if (d.controllable) {
      const onBtn = document.createElement('button');
      onBtn.textContent = 'ON';
      onBtn.onclick = () => sendPower(d.mac, true);
      const offBtn = document.createElement('button');
      offBtn.textContent = 'OFF';
      offBtn.onclick = () => sendPower(d.mac, false);
      ctrl.appendChild(onBtn);
      ctrl.appendChild(offBtn);
      if (d.model === 'H6001') {
        const picker = document.createElement('input');
        picker.type = 'color';
        picker.value = '#ff8000';
        const bright = document.createElement('input');
        bright.type = 'range';
        bright.min = 0;
        bright.max = 100;
        bright.value = 100;
        const applyBtn = document.createElement('button');
        applyBtn.textContent = 'Apply';
        applyBtn.onclick = () => sendColor(d.mac, picker.value.slice(1), Number(bright.value));
        ctrl.appendChild(picker);
        ctrl.appendChild(bright);
        ctrl.appendChild(applyBtn);
      }
    } else {
      ctrl.innerHTML = '<span class="meta">scan-only</span>';
    }
    card.appendChild(ctrl);
    root.appendChild(card);
  }
}
async function sendPower(target, on) {
  const res = await fetch('/power', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target, on })
  });
  const data = await res.json();
  document.getElementById('msg').textContent = res.ok ? data.detail : (data.detail || 'Error');
}
async function sendColor(target, color, brightness) {
  const res = await fetch('/color', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target, color, brightness })
  });
  const data = await res.json();
  document.getElementById('msg').textContent = res.ok ? data.detail : (data.detail || 'Error');
}
loadDevices();
setInterval(loadDevices, 10000);
</script>
</body>
</html>
"""


def create_app(service: GoveeService) -> FastAPI:
    """Create the localhost FastAPI application backed by GoveeService."""
    app = FastAPI(title="govee-watch", version=__version__)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return _DASHBOARD_HTML

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "radio_state": service.state.value}

    @app.get("/devices", response_model=list[DeviceStatusResponse])
    async def list_devices() -> list[dict]:
        return service.list_device_statuses()

    @app.get("/devices/{mac}", response_model=DeviceStatusResponse)
    async def get_device(mac: str) -> dict:
        item = service.get_device_status(mac)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Device '{mac}' not found")
        return item

    @app.post("/power", response_model=CommandResponse)
    async def post_power(req: PowerRequest) -> CommandResponse:
        try:
            mac, name = await service.execute_power(req.target, req.on)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        state = "ON" if req.on else "OFF"
        return CommandResponse(ok=True, mac=mac, name=name, detail=f"Sent {state} to {name} ({mac})")

    @app.post("/color", response_model=CommandResponse)
    async def post_color(req: ColorRequest) -> CommandResponse:
        try:
            mac, name = await service.execute_color(req.target, req.color, req.brightness)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return CommandResponse(
            ok=True,
            mac=mac,
            name=name,
            detail=f"Sent color {req.color.upper()} to {name} ({mac})",
        )

    return app
