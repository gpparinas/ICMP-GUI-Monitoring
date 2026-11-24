"""HTTP API and static dashboard."""

from __future__ import annotations

import csv
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from netmon import __version__
from netmon.auth import SESSION_COOKIE, SESSION_TTL, Auth, LoginThrottle
from netmon.db import Database
from netmon.engine import Engine
from netmon.importer import export_monitors, import_monitors
from netmon.models import (
    LoginIn,
    MonitorIn,
    NotifierIn,
    PasswordChangeIn,
    PasswordIn,
    SettingsIn,
    mask_channel_config,
    validate_channel_config,
)
from netmon.notify import Alert, format_duration

log = logging.getLogger("netmon.api")

STATIC_DIR = Path(__file__).parent / "static"
COMMON_FIELDS = {"name", "type", "target", "group", "interval", "timeout", "retries",
                 "latency_warn_ms", "enabled"}

# range name -> (window in seconds, bucket size in seconds, read from hourly rollups?)
HISTORY_RANGES = {
    "1h": (3600, 30, False),
    "24h": (86400, 600, False),
    "7d": (7 * 86400, 3600, True),
    "30d": (30 * 86400, 4 * 3600, True),
}

# Custom logo: shown in the top bar and as the browser-tab icon.
LOGO_MAX_BYTES = 512 * 1024
LOGO_TYPES = {  # file extension -> content type
    "png": "image/png", "jpg": "image/jpeg", "gif": "image/gif",
    "webp": "image/webp", "ico": "image/x-icon", "svg": "image/svg+xml",
}
# An uploaded SVG may contain script. Served with this policy it can never run any,
# even if someone opens the logo's address directly.
LOGO_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"


def logo_kind(data: bytes) -> str | None:
    """The logo file type, judged from the bytes themselves rather than the file name."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"\x00\x00\x01\x00":
        return "ico"
    try:
        head = data[:4096].decode("utf-8-sig").lstrip().lower()
    except UnicodeDecodeError:
        return None
    if head.startswith("<") and "<svg" in head:
        return "svg"
    return None


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


def create_app(data_dir: str | Path, *, start_engine: bool = True) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    branding_dir = data_dir / "branding"
    db = Database(data_dir / "netmon.db")
    auth = Auth(db)
    engine = Engine(db)
    throttle = LoginThrottle()

    initial_password = os.environ.get("NETMON_ADMIN_PASSWORD")
    if initial_password and not auth.is_configured():
        auth.set_password(initial_password)
        log.info("Admin password set from NETMON_ADMIN_PASSWORD")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_engine:
            await engine.start()
        try:
            yield
        finally:
            await engine.stop()
            db.close()

    app = FastAPI(title="Network Health Monitor", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.engine, app.state.auth = db, engine, auth

    # --- middleware & auth -------------------------------------------------

    @app.middleware("http")
    async def guard(request: Request, call_next):
        is_api = request.url.path.startswith("/api/")
        if is_api and request.method not in ("GET", "HEAD", "OPTIONS"):
            # Browsers send Origin on cross-site writes; refuse anything not from this site.
            origin = request.headers.get("origin")
            if origin:
                hosts = {request.headers.get("host"), request.headers.get("x-forwarded-host")}
                if urlsplit(origin).netloc not in hosts:
                    return JSONResponse({"detail": "Cross-site request refused"}, 403)
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers.setdefault("Cache-Control", "no-store" if is_api else "no-cache")
        return response

    def is_admin(request: Request) -> bool:
        return auth.session_valid(request.cookies.get(SESSION_COOKIE))

    def require_admin(request: Request) -> None:
        if not is_admin(request):
            raise HTTPException(401, "Sign in required")

    def require_viewer(request: Request) -> None:
        if not is_admin(request) and not db.get_setting("public_view"):
            raise HTTPException(401, "Sign in required")

    admin = [Depends(require_admin)]
    viewer = [Depends(require_viewer)]

    def start_session(request: Request, response: Response) -> None:
        response.set_cookie(
            SESSION_COOKIE, auth.create_session(), max_age=SESSION_TTL, httponly=True,
            samesite="strict", secure=request.url.scheme == "https", path="/",
        )

    def logo_file() -> Path | None:
        for ext in LOGO_TYPES:
            path = branding_dir / f"logo.{ext}"
            if path.is_file():
                return path
        return None

    def logo_version() -> str | None:
        """Changes whenever the logo does, so browsers fetch the new one; None when unset."""
        path = logo_file()
        return format(path.stat().st_mtime_ns, "x") if path else None

    def monitor_or_404(monitor_id: int) -> dict[str, Any]:
        monitor = engine.get(monitor_id)
        if monitor is None:
            raise HTTPException(404, "Monitor not found")
        return monitor

    # --- session -----------------------------------------------------------

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": __version__}

    @app.get("/api/session")
    async def session(request: Request):
        return {
            "version": __version__,
            "setup_required": not auth.is_configured(),
            "authenticated": is_admin(request),
            "public_view": bool(db.get_setting("public_view")),
            "site_title": db.get_setting("site_title"),
            "logo": logo_version(),
        }

    @app.post("/api/setup")
    async def setup(body: PasswordIn, request: Request, response: Response):
        if auth.is_configured():
            raise HTTPException(409, "An admin password is already set")
        auth.set_password(body.password)
        start_session(request, response)
        return {"ok": True}

    @app.post("/api/login")
    async def login(body: LoginIn, request: Request, response: Response):
        client = request.client.host if request.client else "unknown"
        wait = throttle.retry_after(client)
        if wait:
            raise HTTPException(429, f"Too many attempts. Try again in {wait} seconds.")
        if not auth.check_password(body.password):
            throttle.failed(client)
            raise HTTPException(401, "Wrong password")
        throttle.succeeded(client)
        start_session(request, response)
        return {"ok": True}

    @app.post("/api/logout")
    async def logout(request: Request, response: Response):
        auth.end_session(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/password", dependencies=admin)
    async def change_password(body: PasswordChangeIn, request: Request, response: Response):
        if not auth.check_password(body.current):
            raise HTTPException(400, "Current password is wrong")
        auth.set_password(body.new)  # signs out every session, including this one
        start_session(request, response)
        return {"ok": True}

    # --- dashboard data ----------------------------------------------------

    @app.get("/api/overview", dependencies=viewer)
    async def overview():
        monitors = engine.snapshot()
        counts = {s: 0 for s in ("up", "degraded", "down", "paused", "pending")}
        for monitor in monitors:
            counts[monitor["status"]] = counts.get(monitor["status"], 0) + 1
        ratios = [m["uptime"]["24h"] for m in monitors if m["uptime"]["24h"] is not None]
        return {
            "now": time.time(),
            "site_title": db.get_setting("site_title"),
            "logo": logo_version(),
            "counts": counts,
            "uptime_24h": sum(ratios) / len(ratios) if ratios else None,
            "monitors": monitors,
        }

    @app.get("/api/monitors/{monitor_id}", dependencies=viewer)
    async def monitor_detail(monitor_id: int):
        monitor = monitor_or_404(monitor_id)
        monitor["incidents"] = db.list_incidents(monitor_id, limit=20)
        monitor["checks"] = list(reversed(db.recent_checks(monitor_id, 20)))
        return monitor

    @app.get("/api/monitors/{monitor_id}/history", dependencies=viewer)
    async def monitor_history(monitor_id: int, range: str = Query("24h")):
        monitor_or_404(monitor_id)
        if range not in HISTORY_RANGES:
            raise HTTPException(400, f"range must be one of {', '.join(HISTORY_RANGES)}")
        window, bucket, use_rollups = HISTORY_RANGES[range]
        now = time.time()
        since = now - window
        # bucket start -> [checks, ok, latency sum, latency samples, latency max]
        buckets: dict[int, list[float]] = {}
        if use_rollups:
            for row in db.hourly_since(monitor_id, since):
                agg = buckets.setdefault(int(row["hour"] // bucket) * bucket, [0, 0, 0.0, 0, 0.0])
                agg[0] += row["total"]
                agg[1] += row["ok"]
                agg[2] += row["lat_sum"]
                agg[3] += row["lat_n"]
                agg[4] = max(agg[4], row["lat_max"])
        else:
            for row in db.checks_since(monitor_id, since):
                agg = buckets.setdefault(int(row["ts"] // bucket) * bucket, [0, 0, 0.0, 0, 0.0])
                agg[0] += 1
                if row["state"] != "down":
                    agg[1] += 1
                    if row["latency_ms"] is not None:
                        agg[2] += row["latency_ms"]
                        agg[3] += 1
                        agg[4] = max(agg[4], row["latency_ms"])
        points = [
            {
                "t": start,
                "n": int(agg[0]),
                "up": agg[1] / agg[0],
                "avg": round(agg[2] / agg[3], 2) if agg[3] else None,
                "max": round(agg[4], 2) if agg[3] else None,
            }
            for start, agg in sorted(buckets.items())
        ]
        total = sum(a[0] for a in buckets.values())
        samples = sum(a[3] for a in buckets.values())
        return {
            "range": range,
            "from": since,
            "to": now,
            "bucket": bucket,
            "points": points,
            "summary": {
                "checks": int(total),
                "uptime": sum(a[1] for a in buckets.values()) / total if total else None,
                "avg_ms": round(sum(a[2] for a in buckets.values()) / samples, 2)
                if samples else None,
                "max_ms": round(max(a[4] for a in buckets.values()), 2) if samples else None,
            },
        }

    @app.get("/api/incidents", dependencies=viewer)
    async def incidents(monitor_id: int | None = None, limit: int = Query(200, ge=1, le=1000)):
        return {"now": time.time(), "incidents": db.list_incidents(monitor_id, limit)}

    # --- monitor management ------------------------------------------------

    @app.post("/api/monitors", dependencies=admin, status_code=201)
    async def create_monitor(body: MonitorIn):
        monitor_id = db.create_monitor(body.model_dump(include=COMMON_FIELDS), body.options())
        engine.reload(monitor_id)
        return engine.get(monitor_id)

    @app.put("/api/monitors/{monitor_id}", dependencies=admin)
    async def update_monitor(monitor_id: int, body: MonitorIn):
        monitor_or_404(monitor_id)
        db.update_monitor(monitor_id, body.model_dump(include=COMMON_FIELDS), body.options())
        engine.reload(monitor_id)
        return engine.get(monitor_id)

    @app.delete("/api/monitors/{monitor_id}", dependencies=admin)
    async def delete_monitor(monitor_id: int):
        monitor_or_404(monitor_id)
        engine.remove(monitor_id)
        db.delete_monitor(monitor_id)
        return {"ok": True}

    @app.post("/api/monitors/{monitor_id}/pause", dependencies=admin)
    async def pause_monitor(monitor_id: int):
        monitor_or_404(monitor_id)
        db.set_monitor_enabled(monitor_id, False)
        engine.reload(monitor_id)
        return engine.get(monitor_id)

    @app.post("/api/monitors/{monitor_id}/resume", dependencies=admin)
    async def resume_monitor(monitor_id: int):
        monitor_or_404(monitor_id)
        db.set_monitor_enabled(monitor_id, True)
        engine.reload(monitor_id)
        return engine.get(monitor_id)

    @app.post("/api/monitors/{monitor_id}/check", dependencies=admin)
    async def check_monitor(monitor_id: int):
        monitor = monitor_or_404(monitor_id)
        if not engine.check_now(monitor_id):
            raise HTTPException(409, f"{monitor['name']} is paused")
        return {"ok": True}

    # --- notification channels ---------------------------------------------

    def public_notifier(channel: dict[str, Any]) -> dict[str, Any]:
        return {**channel, "config": mask_channel_config(channel["config"])}

    def notifier_or_404(notifier_id: int) -> dict[str, Any]:
        channel = db.get_notifier(notifier_id)
        if channel is None:
            raise HTTPException(404, "Notification channel not found")
        return channel

    def checked_config(body: NotifierIn, previous: dict[str, Any] | None) -> dict[str, Any]:
        from pydantic import ValidationError

        try:
            return validate_channel_config(body.type, body.config, previous)
        except ValidationError as exc:
            first = exc.errors()[0]
            field = first["loc"][-1] if first["loc"] else "config"
            raise HTTPException(422, f"{field}: {first['msg']}") from exc

    @app.get("/api/notifiers", dependencies=admin)
    async def list_notifiers():
        return [public_notifier(c) for c in db.list_notifiers()]

    @app.post("/api/notifiers", dependencies=admin, status_code=201)
    async def create_notifier(body: NotifierIn):
        config = checked_config(body, None)
        notifier_id = db.create_notifier(body.name, body.type, config, body.enabled)
        return public_notifier(db.get_notifier(notifier_id))

    @app.put("/api/notifiers/{notifier_id}", dependencies=admin)
    async def update_notifier(notifier_id: int, body: NotifierIn):
        current = notifier_or_404(notifier_id)
        previous = current["config"] if current["type"] == body.type else None
        config = checked_config(body, previous)
        db.update_notifier(notifier_id, body.name, body.type, config, body.enabled)
        return public_notifier(db.get_notifier(notifier_id))

    @app.delete("/api/notifiers/{notifier_id}", dependencies=admin)
    async def delete_notifier(notifier_id: int):
        notifier_or_404(notifier_id)
        db.delete_notifier(notifier_id)
        return {"ok": True}

    @app.post("/api/notifiers/{notifier_id}/test", dependencies=admin)
    async def test_notifier(notifier_id: int):
        channel = notifier_or_404(notifier_id)
        alert = Alert(
            kind="test", monitor_id=None, name="Test alert", monitor_type="test",
            target="example", status="up", previous_status="up",
            detail="If you can read this, alerts from this channel work.",
            timestamp=time.time(), site_title=db.get_setting("site_title"),
        )
        error = await engine.notifier.deliver(channel, alert)
        if error:
            raise HTTPException(502, f"Delivery failed: {error}")
        return {"ok": True}

    # --- settings, backup, reports -----------------------------------------

    @app.get("/api/settings", dependencies=admin)
    async def get_settings():
        return db.get_settings()

    @app.put("/api/settings", dependencies=admin)
    async def put_settings(body: SettingsIn):
        for key, value in body.model_dump().items():
            db.set_setting(key, value)
        return db.get_settings()

    # The logo is public: the sign-in page shows it too.
    @app.get("/api/branding/logo")
    async def get_logo():
        path = logo_file()
        if path is None:
            raise HTTPException(404, "No logo has been uploaded")
        return FileResponse(path, media_type=LOGO_TYPES[path.suffix[1:]], headers={
            "Cache-Control": "no-cache", "Content-Security-Policy": LOGO_CSP,
        })

    @app.put("/api/branding/logo", dependencies=admin)
    async def put_logo(request: Request):
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > LOGO_MAX_BYTES:
            raise HTTPException(413, "The logo must be 512 KB or smaller")
        data = await request.body()
        if len(data) > LOGO_MAX_BYTES:
            raise HTTPException(413, "The logo must be 512 KB or smaller")
        kind = logo_kind(data)
        if kind is None:
            raise HTTPException(415, "Use a PNG, JPEG, SVG, WebP, GIF or ICO image")
        branding_dir.mkdir(exist_ok=True)
        target = branding_dir / f"logo.{kind}"
        temp = target.with_suffix(".tmp")
        temp.write_bytes(data)
        os.replace(temp, target)
        for ext in LOGO_TYPES:
            if ext != kind:
                (branding_dir / f"logo.{ext}").unlink(missing_ok=True)
        return {"logo": logo_version()}

    @app.delete("/api/branding/logo", dependencies=admin)
    async def delete_logo():
        for ext in LOGO_TYPES:
            (branding_dir / f"logo.{ext}").unlink(missing_ok=True)
        return {"logo": None}

    @app.get("/api/export", dependencies=admin)
    async def export():
        return JSONResponse(
            export_monitors(db),
            headers={"Content-Disposition": 'attachment; filename="netmon-monitors.json"'},
        )

    @app.post("/api/import", dependencies=admin)
    async def import_(request: Request):
        try:
            data = await request.json()
            result = import_monitors(db, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc) or "Invalid JSON") from exc
        for monitor_id in result.pop("ids"):
            engine.reload(monitor_id)
        return result

    @app.get("/api/report.csv", dependencies=viewer)
    async def report(days: int = Query(30, ge=1, le=365)):
        stats = db.report(days)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["Monitor", "Group", "Type", "Target", "Current status",
                         f"Uptime % ({days}d)", "Avg response (ms)", "Max response (ms)",
                         "Outages", "Total downtime"])
        for monitor in engine.snapshot():
            row = stats.get(monitor["id"], {})
            uptime = row.get("uptime")
            writer.writerow([
                _csv_safe(monitor["name"]), _csv_safe(monitor["group"]),
                monitor["type"].upper(), _csv_safe(monitor["target"]), monitor["status"],
                "" if uptime is None else f"{uptime * 100:.3f}",
                "" if row.get("avg_ms") is None else f"{row['avg_ms']:.1f}",
                "" if row.get("max_ms") is None else f"{row['max_ms']:.1f}",
                row.get("outages", 0),
                format_duration(row.get("downtime_s", 0)),
            ])
        return Response(
            out.getvalue(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="uptime-report-{days}d.csv"'},
        )

    # --- dashboard ---------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _csv_safe(value: str) -> str:
    """Stop spreadsheet apps from treating a cell as a formula."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value
