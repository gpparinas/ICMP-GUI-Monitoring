from conftest import PASSWORD, monitor_fields


def test_first_run_setup_then_login(client):
    assert client.get("/api/session").json()["setup_required"] is True
    assert client.get("/api/overview").status_code == 401

    assert client.post("/api/setup", json={"password": "short"}).status_code == 422
    assert client.post("/api/setup", json={"password": PASSWORD}).status_code == 200
    session = client.get("/api/session").json()
    assert session["setup_required"] is False and session["authenticated"] is True
    # Setup cannot be repeated to take over an existing install.
    assert client.post("/api/setup", json={"password": "another password"}).status_code == 409

    client.post("/api/logout")
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    assert client.get("/api/overview").status_code == 200


def test_login_is_throttled(admin):
    admin.post("/api/logout")
    for _ in range(5):
        assert admin.post("/api/login", json={"password": "nope"}).status_code == 401
    assert admin.post("/api/login", json={"password": PASSWORD}).status_code == 429


def test_public_view_is_read_only(admin):
    admin.post("/api/monitors", json=monitor_fields())
    admin.put("/api/settings", json={"public_view": True})
    admin.post("/api/logout")

    assert admin.get("/api/overview").status_code == 200
    assert admin.get("/api/incidents").status_code == 200
    assert admin.post("/api/monitors", json=monitor_fields(name="x")).status_code == 401
    assert admin.delete("/api/monitors/1").status_code == 401
    assert admin.get("/api/notifiers").status_code == 401
    assert admin.get("/api/settings").status_code == 401
    assert admin.get("/api/export").status_code == 401


def test_cross_site_writes_are_refused(admin):
    response = admin.post("/api/monitors", json=monitor_fields(),
                          headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    response = admin.post("/api/monitors", json=monitor_fields(),
                          headers={"Origin": "http://testserver"})
    assert response.status_code == 201


def test_monitor_crud(admin):
    created = admin.post("/api/monitors", json=monitor_fields(
        name="Intranet", type="http", target="https://intranet.lan", keyword="Welcome"))
    assert created.status_code == 201
    monitor = created.json()
    assert monitor["status"] == "pending" and monitor["keyword"] == "Welcome"

    bad = admin.post("/api/monitors", json=monitor_fields(type="tcp", target="nope"))
    assert bad.status_code == 422

    updated = admin.put(f"/api/monitors/{monitor['id']}",
                        json=monitor_fields(name="Intranet portal", type="http",
                                            target="https://intranet.lan"))
    assert updated.json()["name"] == "Intranet portal"

    assert admin.post(f"/api/monitors/{monitor['id']}/pause").json()["status"] == "paused"
    assert admin.post(f"/api/monitors/{monitor['id']}/check").status_code == 409
    assert admin.post(f"/api/monitors/{monitor['id']}/resume").json()["status"] == "pending"

    overview = admin.get("/api/overview").json()
    assert overview["counts"]["pending"] == 1 and len(overview["monitors"]) == 1

    assert admin.delete(f"/api/monitors/{monitor['id']}").status_code == 200
    assert admin.get(f"/api/monitors/{monitor['id']}").status_code == 404
    assert admin.put("/api/monitors/999", json=monitor_fields()).status_code == 404


def test_history_and_report(admin, app):
    import time

    monitor_id = admin.post("/api/monitors", json=monitor_fields()).json()["id"]
    now = time.time()
    for i in range(10):
        state, latency = ("down", None) if i == 3 else ("up", 20.0 + i)
        app.state.db.record_check(monitor_id, now - i * 60, state, latency, "")

    history = admin.get(f"/api/monitors/{monitor_id}/history?range=1h").json()
    assert history["summary"]["checks"] == 10
    assert history["summary"]["uptime"] == 0.9
    assert history["summary"]["max_ms"] == 29.0
    assert any(point["up"] == 0 and point["avg"] is None for point in history["points"])
    week = admin.get(f"/api/monitors/{monitor_id}/history?range=7d").json()
    assert week["summary"]["checks"] == 10
    assert admin.get(f"/api/monitors/{monitor_id}/history?range=1y").status_code == 400

    report = admin.get("/api/report.csv?days=7")
    assert report.headers["content-type"].startswith("text/csv")
    lines = report.text.strip().splitlines()
    assert lines[0].startswith("Monitor,Group,Type,Target")
    assert lines[1].startswith("Router,Network,ICMP,192.168.1.1,pending,90.000,")


def test_notifier_secrets_never_leave_the_server(admin):
    created = admin.post("/api/notifiers", json={
        "name": "IT chat", "type": "slack",
        "config": {"url": "https://hooks.slack.com/services/T000/B000/secret"},
    })
    assert created.status_code == 201
    channel = created.json()
    assert "secret" not in created.text and channel["config"]["url"] == "********"

    # Renaming without retyping the URL keeps the stored one.
    renamed = admin.put(f"/api/notifiers/{channel['id']}", json={
        "name": "IT team chat", "type": "slack", "config": channel["config"]})
    assert renamed.status_code == 200
    stored = admin.app.state.db.get_notifier(channel["id"])
    assert stored["config"]["url"].endswith("/secret") and stored["name"] == "IT team chat"

    bad = admin.post("/api/notifiers", json={"name": "x", "type": "email", "config": {}})
    assert bad.status_code == 422 and "host" in bad.json()["detail"]
    assert admin.delete(f"/api/notifiers/{channel['id']}").status_code == 200
    assert admin.get("/api/notifiers").json() == []


def test_import_legacy_config_and_export(admin):
    legacy = {
        "ip_addresses": {"Google DNS": "8.8.8.8", "Gateway": "192.168.1.1"},
        "web_services": {"Website": "https://example.com", "Broken": "not a url"},
        "alert_threshold": 200,
    }
    result = admin.post("/api/import", json=legacy).json()
    assert result["imported"] == 3 and result["skipped"] == 0
    assert len(result["errors"]) == 1 and result["errors"][0].startswith("Broken")

    # Importing the same file again adds nothing.
    assert admin.post("/api/import", json=legacy).json()["imported"] == 0

    exported = admin.get("/api/export").json()
    names = {m["name"]: m for m in exported["monitors"]}
    assert names["Google DNS"]["type"] == "icmp" and names["Google DNS"]["latency_warn_ms"] == 200
    assert names["Website"]["group"] == "Web Services"
    assert "status" not in names["Website"] and "id" not in names["Website"]

    assert admin.post("/api/import", json={"unexpected": 1}).status_code == 400


def test_password_change_signs_out_other_sessions(admin, app):
    from fastapi.testclient import TestClient

    other = TestClient(app)
    assert other.post("/api/login", json={"password": PASSWORD}).status_code == 200
    assert other.get("/api/settings").status_code == 200

    assert admin.post("/api/password",
                      json={"current": "wrong", "new": "a brand new password"}).status_code == 400
    assert admin.post("/api/password",
                      json={"current": PASSWORD, "new": "a brand new password"}).status_code == 200
    assert admin.get("/api/settings").status_code == 200  # this browser stays signed in
    assert other.get("/api/settings").status_code == 401


def test_dashboard_is_served_with_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/healthz").json()["status"] == "ok"


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SVG = (b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
       b"<script>alert(1)</script></svg>")


def upload(client, data, content_type="image/png"):
    return client.put("/api/branding/logo", content=data, headers={"Content-Type": content_type})


def test_logo_upload_replace_and_remove(admin, tmp_path):
    assert admin.get("/api/session").json()["logo"] is None
    assert admin.get("/api/branding/logo").status_code == 404

    response = upload(admin, PNG)
    assert response.status_code == 200
    version = response.json()["logo"]
    assert version and admin.get("/api/session").json()["logo"] == version
    assert admin.get("/api/overview").json()["logo"] == version
    logo = admin.get("/api/branding/logo")
    assert logo.status_code == 200 and logo.content == PNG
    assert logo.headers["content-type"] == "image/png"

    # Replacing with another format leaves exactly one logo behind.
    assert upload(admin, SVG, "image/svg+xml").status_code == 200
    assert sorted(p.name for p in (tmp_path / "branding").iterdir()) == ["logo.svg"]
    svg = admin.get("/api/branding/logo")
    assert svg.headers["content-type"].startswith("image/svg+xml")
    # Script inside an uploaded SVG must never run, even when opened directly.
    assert "sandbox" in svg.headers["content-security-policy"]
    assert "default-src 'none'" in svg.headers["content-security-policy"]

    assert admin.delete("/api/branding/logo").json() == {"logo": None}
    assert admin.get("/api/session").json()["logo"] is None
    assert admin.get("/api/branding/logo").status_code == 404


def test_logo_is_validated_and_admin_only(admin):
    # The file's bytes decide, not the declared type.
    assert upload(admin, b"<html>not an image</html>").status_code == 415
    assert upload(admin, PNG + b"\x00" * (512 * 1024)).status_code == 413
    assert admin.get("/api/session").json()["logo"] is None

    upload(admin, PNG)
    admin.post("/api/logout")
    # Anyone may see the logo (the sign-in page shows it), but only the admin may change it.
    assert admin.get("/api/branding/logo").status_code == 200
    assert upload(admin, PNG).status_code == 401
    assert admin.delete("/api/branding/logo").status_code == 401
