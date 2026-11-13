"""The monitoring engine: schedules checks, tracks state and raises alerts."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from netmon.checks import CheckResult, HttpClients, run_check
from netmon.db import Database
from netmon.notify import Alert, Notifier

log = logging.getLogger("netmon.engine")

RECENT_CHECKS = 40          # heartbeats kept in memory per monitor for the dashboard
MAX_CONCURRENT_CHECKS = 50
RETRY_DELAY = 10            # seconds between re-checks while a failure is unconfirmed
UPTIME_CACHE_SECONDS = 30
PRUNE_INTERVAL = 3600

Checker = Callable[[dict[str, Any], HttpClients], Awaitable[CheckResult]]


@dataclass
class Runtime:
    """Live state of one monitor."""

    monitor: dict[str, Any]
    status: str                      # pending | up | degraded | down | paused
    since: float
    last_check: float | None = None
    latency_ms: float | None = None
    detail: str = ""
    # A state different from ``status`` that has been observed but not yet confirmed.
    pending_state: str | None = None
    pending_count: int = 0
    pending_since: float = 0.0
    recent: deque = field(default_factory=lambda: deque(maxlen=RECENT_CHECKS))
    task: asyncio.Task | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)


class Engine:
    def __init__(self, db: Database, notifier: Notifier | None = None,
                 checker: Checker = run_check):
        self.db = db
        self.notifier = notifier or Notifier(db)
        self._checker = checker
        self._http = HttpClients()
        self._runtimes: dict[int, Runtime] = {}
        self._sem = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
        self._background: set[asyncio.Task] = set()
        self._prune_task: asyncio.Task | None = None
        self._uptime: dict[int, dict[str, float | None]] = {}
        self._uptime_at = 0.0
        self._running = False

    # --- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        for monitor in self.db.list_monitors():
            self._load(monitor)
        self._prune_task = asyncio.create_task(self._prune_loop())
        log.info("Engine started with %d monitor(s)", len(self._runtimes))

    async def stop(self) -> None:
        self._running = False
        tasks = [rt.task for rt in self._runtimes.values() if rt.task]
        if self._prune_task:
            tasks.append(self._prune_task)
        tasks.extend(self._background)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._http.aclose()
        await self.notifier.aclose()

    def _load(self, monitor: dict[str, Any]) -> Runtime:
        """Create the runtime for a monitor and start its check loop if enabled."""
        monitor_id = monitor["id"]
        previous = self._runtimes.get(monitor_id)
        if previous and previous.task:
            previous.task.cancel()

        status = monitor["status"] if monitor["enabled"] else "paused"
        if monitor["enabled"] and status == "paused":
            status = "pending"
        rt = Runtime(monitor=monitor, status=status, since=monitor["status_since"])
        if previous:
            rt.recent, rt.last_check = previous.recent, previous.last_check
            rt.latency_ms, rt.detail = previous.latency_ms, previous.detail
        else:
            for check in self.db.recent_checks(monitor_id, RECENT_CHECKS):
                rt.recent.append((check["ts"], check["state"], check["latency_ms"]))
                rt.last_check, rt.latency_ms = check["ts"], check["latency_ms"]
                rt.detail = check["detail"]
        self._runtimes[monitor_id] = rt
        if monitor["enabled"] and self._running:
            rt.task = asyncio.create_task(self._run(rt), name=f"monitor-{monitor_id}")
        return rt

    # --- changes coming from the API --------------------------------------

    def reload(self, monitor_id: int) -> None:
        """Pick up a created, edited, paused or resumed monitor."""
        monitor = self.db.get_monitor(monitor_id)
        if monitor is None:
            self.remove(monitor_id)
            return
        previous = self._runtimes.get(monitor_id)
        now = time.time()
        if not monitor["enabled"] and monitor["status"] != "paused":
            self.db.close_incidents(monitor_id, now)
            self.db.set_monitor_status(monitor_id, "paused", now)
            monitor = self.db.get_monitor(monitor_id)
        elif monitor["enabled"] and monitor["status"] == "paused":
            self.db.set_monitor_status(monitor_id, "pending", now)
            monitor = self.db.get_monitor(monitor_id)
        elif previous and (previous.monitor["type"], previous.monitor["target"]) != (
            monitor["type"], monitor["target"]
        ):
            # A different target is a different thing to watch: start from a clean slate.
            self.db.close_incidents(monitor_id, now)
            self.db.set_monitor_status(monitor_id, "pending", now)
            monitor = self.db.get_monitor(monitor_id)
            previous.recent.clear()
            previous.last_check = previous.latency_ms = None
            previous.detail = ""
        self._load(monitor)
        self._uptime_at = 0.0

    def remove(self, monitor_id: int) -> None:
        rt = self._runtimes.pop(monitor_id, None)
        if rt and rt.task:
            rt.task.cancel()

    def check_now(self, monitor_id: int) -> bool:
        rt = self._runtimes.get(monitor_id)
        if rt is None or rt.task is None:
            return False
        rt.wake.set()
        return True

    # --- check loop -------------------------------------------------------

    async def _run(self, rt: Runtime) -> None:
        monitor = rt.monitor
        # Spread monitors out so they do not all fire in the same instant.
        await asyncio.sleep(random.uniform(0, min(monitor["interval"], 3)))
        while True:
            try:
                async with self._sem:
                    result = await self._checker(monitor, self._http)
                self.process(rt, result)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the loop alive whatever happens
                log.exception("Check loop error for monitor %s", monitor["name"])
            delay = monitor["interval"]
            if rt.pending_state == "down":
                delay = min(delay, RETRY_DELAY)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(rt.wake.wait(), delay)
            rt.wake.clear()

    def process(self, rt: Runtime, result: CheckResult, now: float | None = None) -> None:
        """Record one check result and apply the state machine."""
        now = now or time.time()
        monitor = rt.monitor
        warn = monitor.get("latency_warn_ms")
        if not result.ok:
            observed, detail = "down", result.detail or "Check failed"
        elif result.warning:
            observed, detail = "degraded", result.warning
        elif warn and result.latency_ms is not None and result.latency_ms > warn:
            observed = "degraded"
            detail = f"Slow response: {result.latency_ms:.0f} ms (threshold {warn} ms)"
        else:
            observed, detail = "up", result.detail

        self.db.record_check(monitor["id"], now, observed, result.latency_ms, detail)
        rt.recent.append((now, observed, result.latency_ms))
        rt.last_check, rt.latency_ms, rt.detail = now, result.latency_ms, detail

        if observed == rt.status:
            rt.pending_state, rt.pending_count = None, 0
            return
        if observed == rt.pending_state:
            rt.pending_count += 1
        else:
            rt.pending_state, rt.pending_count, rt.pending_since = observed, 1, now
        # Good news is believed at once; bad news must repeat ``retries`` more times,
        # so one dropped packet never pages anyone.
        recovering = rt.status in ("down", "pending") and observed != "down"
        needed = 1 if recovering else monitor["retries"] + 1
        if rt.pending_count >= needed:
            self._transition(rt, observed, detail, rt.pending_since, now)

    def _transition(self, rt: Runtime, new: str, detail: str, since: float,
                    now: float) -> None:
        monitor, old = rt.monitor, rt.status
        problem_started = rt.since
        rt.status, rt.since = new, since
        rt.pending_state, rt.pending_count = None, 0
        self.db.set_monitor_status(monitor["id"], new, since)
        if old in ("down", "degraded"):
            self.db.close_incidents(monitor["id"], since)
        if new in ("down", "degraded"):
            self.db.open_incident(monitor["id"], new, since, detail)
        self._uptime_at = 0.0
        log.info("%s (%s %s): %s -> %s%s", monitor["name"], monitor["type"],
                 monitor["target"], old, new, f" [{detail}]" if detail else "")

        duration = None
        if new == "down":
            kind = "down"
        elif old == "down":
            kind, duration = "recovered", now - problem_started
        elif new == "degraded":
            kind = "degraded"
        elif old == "degraded":
            kind, duration = "resolved", now - problem_started
        else:
            return  # pending -> up: nothing to announce
        if kind in ("degraded", "resolved") and not self.db.get_setting("notify_degraded"):
            return
        alert = Alert(
            kind=kind, monitor_id=monitor["id"], name=monitor["name"],
            monitor_type=monitor["type"], target=monitor["target"], status=new,
            previous_status=old, detail=detail, timestamp=now, duration=duration,
            site_title=self.db.get_setting("site_title"),
        )
        task = asyncio.create_task(self.notifier.dispatch(alert))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _prune_loop(self) -> None:
        while True:
            try:
                removed = self.db.prune(int(self.db.get_setting("retention_days")))
                if removed:
                    log.info("Pruned %d old check(s)", removed)
            except Exception:  # noqa: BLE001
                log.exception("Pruning failed")
            await asyncio.sleep(PRUNE_INTERVAL)

    # --- read side --------------------------------------------------------

    def uptime(self) -> dict[int, dict[str, float | None]]:
        now = time.time()
        if now - self._uptime_at > UPTIME_CACHE_SECONDS:
            self._uptime, self._uptime_at = self.db.uptime_summary(now), now
        return self._uptime

    def snapshot(self) -> list[dict[str, Any]]:
        """Current state of every monitor, in display order."""
        uptime = self.uptime()
        empty = {"24h": None, "7d": None, "30d": None}
        items = []
        for rt in self._runtimes.values():
            item = {k: v for k, v in rt.monitor.items() if k not in ("status", "status_since")}
            item.update(
                status=rt.status,
                since=rt.since,
                last_check=rt.last_check,
                latency_ms=rt.latency_ms,
                detail=rt.detail,
                recent=[[round(ts), state, latency] for ts, state, latency in rt.recent],
                uptime=uptime.get(rt.monitor["id"], empty),
            )
            items.append(item)
        items.sort(key=lambda m: (m["group"].lower(), m["name"].lower(), m["id"]))
        return items

    def get(self, monitor_id: int) -> dict[str, Any] | None:
        return next((m for m in self.snapshot() if m["id"] == monitor_id), None)
