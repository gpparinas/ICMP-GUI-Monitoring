import asyncio

from conftest import monitor_fields
from netmon.checks import CheckResult
from netmon.engine import Engine

UP = CheckResult(True, 12.0, "ok")
DOWN = CheckResult(False, None, "No reply")
SLOW = CheckResult(True, 900.0, "ok")


class RecordingNotifier:
    def __init__(self):
        self.alerts = []

    async def dispatch(self, alert):
        self.alerts.append(alert)

    async def aclose(self):
        pass


def make(db, **overrides):
    """An engine with one monitor loaded but no background check loop."""
    notifier = RecordingNotifier()
    engine = Engine(db, notifier)
    monitor_id = db.create_monitor(monitor_fields(**overrides), {})
    runtime = engine._load(db.get_monitor(monitor_id))
    return engine, runtime, notifier


def feed(engine, runtime, results, start=1000.0, step=60.0):
    """Process results inside an event loop and let alert tasks finish."""
    async def go():
        for i, result in enumerate(results):
            engine.process(runtime, result, now=start + i * step)
        await asyncio.sleep(0)
        await asyncio.gather(*engine._background)

    asyncio.run(go())


def kinds(notifier):
    return [alert.kind for alert in notifier.alerts]


def test_first_success_is_quiet(db):
    engine, rt, notifier = make(db)
    feed(engine, rt, [UP])
    assert rt.status == "up" and notifier.alerts == []


def test_down_needs_retries_before_alerting(db):
    engine, rt, notifier = make(db, retries=2)
    feed(engine, rt, [UP, DOWN, DOWN])
    assert rt.status == "up" and notifier.alerts == []  # two failures: not confirmed yet

    feed(engine, rt, [DOWN], start=1180.0)
    assert rt.status == "down" and kinds(notifier) == ["down"]
    assert rt.since == 1060.0  # the outage is dated from the first failed check
    incident = db.list_incidents()[0]
    assert incident["severity"] == "down" and incident["started_at"] == 1060.0
    assert incident["ended_at"] is None and incident["cause"] == "No reply"


def test_single_blip_never_alerts(db):
    engine, rt, notifier = make(db, retries=2)
    feed(engine, rt, [UP, DOWN, UP, DOWN, UP])
    assert rt.status == "up" and notifier.alerts == [] and db.list_incidents() == []


def test_recovery_is_immediate_and_reports_duration(db):
    engine, rt, notifier = make(db, retries=0)
    feed(engine, rt, [UP, DOWN, DOWN, UP])
    assert rt.status == "up" and kinds(notifier) == ["down", "recovered"]
    assert notifier.alerts[1].duration == 120.0
    incident = db.list_incidents()[0]
    assert incident["ended_at"] - incident["started_at"] == 120.0


def test_slow_responses_degrade_then_resolve(db):
    engine, rt, notifier = make(db, retries=1, latency_warn_ms=500)
    feed(engine, rt, [UP, SLOW])
    assert rt.status == "up"  # one slow reply is not enough
    feed(engine, rt, [SLOW, UP], start=1120.0)
    assert rt.status == "degraded" and kinds(notifier) == ["degraded"]  # one fast reply neither
    feed(engine, rt, [UP], start=1240.0)
    assert rt.status == "up" and kinds(notifier) == ["degraded", "resolved"]


def test_degraded_alerts_can_be_switched_off(db):
    db.set_setting("notify_degraded", False)
    engine, rt, notifier = make(db, retries=0, latency_warn_ms=500)
    feed(engine, rt, [UP, SLOW, UP, DOWN, UP])
    assert kinds(notifier) == ["down", "recovered"]


def test_warning_from_check_degrades(db):
    engine, rt, notifier = make(db, retries=0)
    feed(engine, rt, [CheckResult(True, 20.0, "HTTP 200", "TLS certificate expires in 3 days")])
    assert rt.status == "degraded" and rt.detail == "TLS certificate expires in 3 days"
    assert kinds(notifier) == ["degraded"]


def test_state_survives_restart_without_duplicate_alert(db):
    engine, rt, notifier = make(db, retries=0)
    feed(engine, rt, [DOWN])
    assert kinds(notifier) == ["down"]

    restarted_notifier = RecordingNotifier()
    restarted = Engine(db, restarted_notifier)
    runtime = restarted._load(db.get_monitor(rt.monitor["id"]))
    assert runtime.status == "down" and len(runtime.recent) == 1
    feed(restarted, runtime, [DOWN], start=2000.0)
    assert restarted_notifier.alerts == []
    feed(restarted, runtime, [UP], start=2060.0)
    assert kinds(restarted_notifier) == ["recovered"]


def test_pause_closes_incident_and_resume_starts_pending(db):
    engine, rt, notifier = make(db, retries=0)
    feed(engine, rt, [DOWN])
    monitor_id = rt.monitor["id"]

    db.set_monitor_enabled(monitor_id, False)
    engine.reload(monitor_id)
    assert engine.get(monitor_id)["status"] == "paused"
    assert db.list_incidents()[0]["ended_at"] is not None

    db.set_monitor_enabled(monitor_id, True)
    engine.reload(monitor_id)
    assert engine.get(monitor_id)["status"] == "pending"


def test_scheduler_runs_checks_and_check_now(db, monkeypatch):
    monkeypatch.setattr("netmon.engine.random.uniform", lambda low, high: 0)  # no start jitter
    calls = []

    async def checker(monitor, clients):
        calls.append(monitor["name"])
        return UP

    async def go():
        engine = Engine(db, RecordingNotifier(), checker=checker)
        monitor_id = db.create_monitor(monitor_fields(interval=3600, timeout=5), {})
        await engine.start()
        await asyncio.sleep(0.05)
        assert calls == ["Router"]
        assert engine.check_now(monitor_id)
        await asyncio.sleep(0.05)
        assert calls == ["Router", "Router"]
        snapshot = engine.snapshot()[0]
        assert snapshot["status"] == "up" and len(snapshot["recent"]) == 2
        assert snapshot["uptime"]["24h"] == 1.0
        await engine.stop()

    asyncio.run(go())
