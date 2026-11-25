from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

from govee_watch.config import settings
from govee_watch.logging_utils import configure_logging, log_event
from govee_watch.protocol import PacketProfile
from govee_watch.registry import load_registry

logger = logging.getLogger("govee_watch.cli")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="govee-watch",
        description="Strict local API client for the govee-watch service",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="Show live presence from the running service")

    power_p = sub.add_parser("power", help="Turn a bulb ON or OFF")
    power_p.add_argument("target", help="Device name, unique model, or MAC address")
    power_grp = power_p.add_mutually_exclusive_group(required=True)
    power_grp.add_argument("--on", action="store_true", help="Turn bulb ON")
    power_grp.add_argument("--off", action="store_true", help="Turn bulb OFF")

    color_p = sub.add_parser("color", help="Set bulb RGB color and optional brightness")
    color_p.add_argument("target", help="Device name, unique model, or MAC address")
    color_p.add_argument("hex_color", help="RGB color in RRGGBB hex")
    color_p.add_argument("--brightness", type=int, default=None, help="Optional brightness (0..100)")

    status_p = sub.add_parser("status", help="Show registered devices and presence state")
    status_p.add_argument("--json", action="store_true", help="Output JSON")

    sub.add_parser("registry-check", help="Validate devices.json schema and entries")
    return parser


def check_registry_file(path: Path | None = None) -> tuple[bool, list[str]]:
    """Validate devices.json and return (ok, messages)."""
    target = path if path is not None else settings.devices_path
    if not target.exists():
        return False, [f"Registry file not found: {target}"]

    try:
        reg = load_registry(target)
    except Exception as exc:
        return False, [f"Failed to parse {target}: {exc}"]

    errors: list[str] = []
    seen_macs: set[str] = set()
    seen_names: set[str] = set()
    for dev in reg.list_devices():
        if dev.mac in seen_macs:
            errors.append(f"Duplicate MAC address: {dev.mac}")
        seen_macs.add(dev.mac)

        lower_name = dev.name.lower()
        if not lower_name:
            errors.append(f"Empty device name for MAC {dev.mac}")
        elif lower_name in seen_names:
            errors.append(f"Duplicate device name: {dev.name}")
        seen_names.add(lower_name)

        try:
            PacketProfile.from_raw(dev.packet_profile)
        except Exception as exc:
            errors.append(f"Invalid packet_profile for {dev.name} ({dev.mac}): {exc}")

    if errors:
        return False, errors
    return True, [f"Registry OK: {len(reg.list_devices())} device(s) in {target}"]


def api_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    base_url: str | None = None,
    retry_seconds: float | None = None,
    retry_interval: float = 0.5,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Any:
    """Call the local govee-watch HTTP API with bounded retry on connection errors."""
    url = f"{(base_url or settings.api_base_url).rstrip('/')}{path}"
    budget = settings.cli_retry_seconds if retry_seconds is None else max(0.0, retry_seconds)
    deadline = time.monotonic() + budget

    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    while True:
        try:
            with opener(req, timeout=15.0) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(detail)
                detail = parsed.get("detail", detail)
            except Exception:
                pass
            raise RuntimeError(f"API error {exc.code}: {detail}") from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"govee-watch service is unreachable at {url}: {exc}"
                ) from exc
            time.sleep(min(retry_interval, max(0.0, deadline - time.monotonic())))


def _print_devices(rows: list[dict[str, Any]], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    for row in rows:
        state = "present" if row.get("is_present") else "away"
        print(
            f"{row['name']} ({row['mac']}, {row['model']}) | {state} "
            f"| rssi={row.get('last_rssi')} | last_seen={row.get('last_seen')}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "registry-check":
            ok, messages = check_registry_file()
            for msg in messages:
                print(msg)
            return 0 if ok else 1

        if args.command in ("status", "scan"):
            rows = api_request("GET", "/devices")
            _print_devices(rows, as_json=bool(getattr(args, "json", False)))
            return 0

        if args.command == "power":
            res = api_request("POST", "/power", {"target": args.target, "on": bool(args.on)})
            print(res["detail"])
            return 0

        if args.command == "color":
            res = api_request(
                "POST",
                "/color",
                {
                    "target": args.target,
                    "color": args.hex_color,
                    "brightness": args.brightness,
                },
            )
            print(res["detail"])
            return 0

        return 1
    except Exception as exc:
        target = getattr(args, "target", None)
        log_event(
            logger,
            logging.ERROR,
            "CLI command failed",
            command=args.command,
            device=target,
            step=args.command,
            reason=str(exc),
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
