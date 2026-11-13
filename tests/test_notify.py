import asyncio
import http.server
import json
import socketserver
import threading

import pytest

from netmon.notify import Alert, Notifier, format_duration


class Receiver(http.server.BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Receiver.received.append((self.path, json.loads(body)))
        code = 500 if self.path == "/broken" else 200
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class FakeSmtp(socketserver.StreamRequestHandler):
    """Just enough SMTP to accept one message."""

    messages: list = []

    def handle(self):
        self.wfile.write(b"220 test ESMTP\r\n")
        in_data, lines = False, []
        for raw in self.rfile:
            line = raw.decode().rstrip("\r\n")
            if in_data:
                if line == ".":
                    FakeSmtp.messages.append("\n".join(lines))
                    in_data = False
                    self.wfile.write(b"250 queued\r\n")
                else:
                    lines.append(line)
            elif line.upper().startswith("DATA"):
                in_data = True
                self.wfile.write(b"354 go ahead\r\n")
            elif line.upper().startswith("QUIT"):
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


@pytest.fixture
def servers(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    Receiver.received.clear()
    FakeSmtp.messages.clear()
    web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeSmtp)
    for server in (web, smtp):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{web.server_address[1]}", smtp.server_address[1]
    web.shutdown()
    smtp.shutdown()


def alert(kind="down", **overrides):
    fields = {
        "kind": kind, "monitor_id": 7, "name": "File server", "monitor_type": "tcp",
        "target": "192.168.1.10:445", "status": "down", "previous_status": "up",
        "detail": "Connection refused", "timestamp": 1_700_000_000.0, "site_title": "Acme",
    }
    fields.update(overrides)
    return Alert(**fields)


def dispatch(db, event):
    async def go():
        notifier = Notifier(db)
        try:
            await notifier.dispatch(event)
        finally:
            await notifier.aclose()

    asyncio.run(go())


def test_format_duration():
    assert format_duration(0) == "0s"
    assert format_duration(75) == "1m 15s"
    assert format_duration(3 * 86400 + 2 * 3600 + 5) == "3d 2h"


def test_alert_text():
    recovered = alert("recovered", status="up", previous_status="down", detail="Port 445 open",
                      duration=754)
    assert recovered.subject == "[Acme] RECOVERED: File server"
    assert "Duration: 12m 34s" in recovered.text
    assert "Target: 192.168.1.10:445 (TCP)" in recovered.text
    assert recovered.payload()["duration_seconds"] == 754


def test_webhook_style_channels(db, servers):
    base, _ = servers
    for kind in ("slack", "discord", "teams", "webhook"):
        db.create_notifier(kind, kind, {"url": f"{base}/{kind}"}, True)
    db.create_notifier("switched off", "webhook", {"url": f"{base}/off"}, False)

    dispatch(db, alert())
    bodies = dict(Receiver.received)
    assert set(bodies) == {"/slack", "/discord", "/teams", "/webhook"}
    assert bodies["/slack"]["text"].startswith("*🔴 DOWN: File server*")
    assert "Connection refused" in bodies["/discord"]["content"]
    card = bodies["/teams"]["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard" and card["body"][0]["text"] == "🔴 DOWN: File server"
    assert bodies["/webhook"]["event"] == "down"
    assert bodies["/webhook"]["monitor"] == {
        "id": 7, "name": "File server", "type": "tcp", "target": "192.168.1.10:445"}
    assert all(c["last_sent_at"] for c in db.list_notifiers() if c["enabled"])


def test_one_failing_channel_does_not_block_the_others(db, servers):
    base, _ = servers
    broken = db.create_notifier("broken", "webhook", {"url": f"{base}/broken"}, True)
    working = db.create_notifier("working", "webhook", {"url": f"{base}/fine"}, True)

    dispatch(db, alert())
    assert db.get_notifier(broken)["last_error"].startswith("HTTP 500")
    assert db.get_notifier(working)["last_error"] == ""
    assert db.get_notifier(working)["last_sent_at"] is not None


def test_error_text_never_contains_the_secret(db):
    secret_url = "http://127.0.0.1:9/hooks/TOP-SECRET-TOKEN"
    channel_id = db.create_notifier("dead", "slack", {"url": secret_url}, True)
    dispatch(db, alert())
    error = db.get_notifier(channel_id)["last_error"]
    assert error and "TOP-SECRET-TOKEN" not in error


def test_email(db, servers):
    _, smtp_port = servers
    db.create_notifier("mail", "email", {
        "host": "127.0.0.1", "port": smtp_port, "security": "none", "username": "",
        "password": "", "from_addr": "monitor@example.com",
        "to_addrs": "it@example.com, owner@example.com",
    }, True)

    dispatch(db, alert())
    assert db.list_notifiers()[0]["last_error"] == ""
    message = FakeSmtp.messages[0]
    assert "Subject: [Acme] DOWN: File server" in message
    assert "To: it@example.com, owner@example.com" in message
