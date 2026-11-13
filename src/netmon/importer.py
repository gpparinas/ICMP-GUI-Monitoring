"""Import and export of monitor definitions, including the v1 ``config.json`` format."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from netmon.db import Database
from netmon.models import OPTION_FIELDS, MonitorIn

EXPORT_VERSION = 1
_COMMON_FIELDS = ("name", "type", "target", "group", "interval", "timeout", "retries",
                  "latency_warn_ms", "enabled")


def export_monitors(db: Database) -> dict[str, Any]:
    monitors = []
    for monitor in db.list_monitors():
        fields = (*_COMMON_FIELDS, *OPTION_FIELDS[monitor["type"]])
        monitors.append({name: monitor[name] for name in fields if name in monitor})
    return {"netmon_export": EXPORT_VERSION, "monitors": monitors}


def _from_legacy(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate the v1 config: {"ip_addresses": {...}, "web_services": {...}}."""
    threshold = data.get("alert_threshold")
    warn = int(threshold) if isinstance(threshold, (int, float)) and threshold >= 1 else None
    entries: list[dict[str, Any]] = []
    for name, address in (data.get("ip_addresses") or {}).items():
        entries.append({"name": name, "type": "icmp", "target": address,
                        "group": "Network Devices", "latency_warn_ms": warn})
    for name, url in (data.get("web_services") or {}).items():
        entries.append({"name": name, "type": "http", "target": url,
                        "group": "Web Services", "latency_warn_ms": warn})
    return entries


def parse_import(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("The file must contain a JSON object")
    if isinstance(data.get("monitors"), list):
        return [entry for entry in data["monitors"] if isinstance(entry, dict)]
    if "ip_addresses" in data or "web_services" in data:
        return _from_legacy(data)
    raise ValueError("Unrecognised file: expected a netmon export or a v1 config.json")


def import_monitors(db: Database, data: Any) -> dict[str, Any]:
    """Add monitors from an export. Entries that already exist are skipped."""
    existing = {(m["type"], m["target"].lower()) for m in db.list_monitors()}
    created: list[int] = []
    skipped = 0
    errors: list[str] = []
    for index, entry in enumerate(parse_import(data), start=1):
        try:
            monitor = MonitorIn.model_validate(entry)
        except ValidationError as exc:
            label = entry.get("name") or f"entry {index}"
            errors.append(f"{label}: {exc.errors()[0]['msg']}")
            continue
        key = (monitor.type, monitor.target.lower())
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        fields = monitor.model_dump(include=set(_COMMON_FIELDS))
        created.append(db.create_monitor(fields, monitor.options()))
    return {"imported": len(created), "skipped": skipped, "errors": errors, "ids": created}
