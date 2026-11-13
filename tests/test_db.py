from conftest import monitor_fields


def test_monitor_roundtrip(db):
    monitor_id = db.create_monitor(monitor_fields(type="http", target="https://x.test"),
                                   {"keyword": "OK", "verify_tls": True})
    monitor = db.get_monitor(monitor_id)
    assert monitor["name"] == "Router" and monitor["keyword"] == "OK"
    assert monitor["status"] == "pending" and monitor["enabled"] is True

    db.update_monitor(monitor_id, monitor_fields(name="Core router", enabled=False), {})
    monitor = db.get_monitor(monitor_id)
    assert monitor["name"] == "Core router" and monitor["enabled"] is False
    assert "keyword" not in monitor


def test_rollups_and_uptime(db):
    monitor_id = db.create_monitor(monitor_fields(), {})
    now = 1_700_000_000.0
    for i in range(8):
        db.record_check(monitor_id, now - i * 60, "up", 10.0 + i, "")
    db.record_check(monitor_id, now - 9 * 60, "down", None, "No reply")
    db.record_check(monitor_id, now - 10 * 60, "degraded", 500.0, "Slow")

    uptime = db.uptime_summary(now)[monitor_id]
    assert uptime["24h"] == uptime["30d"] == 0.9  # degraded still counts as reachable

    report = db.report(30, now)[monitor_id]
    assert report["uptime"] == 0.9
    assert report["max_ms"] == 500.0
    assert round(report["avg_ms"], 2) == round((sum(10 + i for i in range(8)) + 500) / 9, 2)


def test_incidents_and_report_downtime(db):
    monitor_id = db.create_monitor(monitor_fields(), {})
    now = 1_700_000_000.0
    db.open_incident(monitor_id, "down", now - 600, "No reply")
    db.close_incidents(monitor_id, now - 300)
    db.open_incident(monitor_id, "down", now - 120, "No reply")  # still ongoing

    incidents = db.list_incidents()
    assert [i["ended_at"] is None for i in incidents] == [True, False]  # open ones first
    assert incidents[0]["monitor_name"] == "Router"

    report = db.report(30, now)[monitor_id]
    assert report["outages"] == 2
    assert report["downtime_s"] == 300 + 120


def test_prune_and_cascade(db):
    monitor_id = db.create_monitor(monitor_fields(), {})
    now = 1_700_000_000.0
    db.record_check(monitor_id, now - 20 * 86400, "up", 1.0, "")
    db.record_check(monitor_id, now, "up", 1.0, "")
    assert db.prune(14, now) == 1
    assert len(db.recent_checks(monitor_id, 10)) == 1
    # Rollups outlive raw checks, so long-range uptime is still available.
    assert db.uptime_summary(now)[monitor_id]["30d"] == 1.0

    db.delete_monitor(monitor_id)
    assert db.recent_checks(monitor_id, 10) == []
    assert db.uptime_summary(now) == {}


def test_settings_defaults_and_overrides(db):
    assert db.get_setting("retention_days") == 14
    db.set_setting("retention_days", 30)
    db.set_setting("public_view", True)
    assert db.get_settings()["retention_days"] == 30
    assert db.get_settings()["public_view"] is True
