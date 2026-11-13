import asyncio
import http.server
import os
import socket
import sys
import threading

import pytest

from netmon import checks
from netmon.checks import HttpClients, describe_error, parse_system_ping, run_check


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        code = 500 if self.path == "/broken" else 200
        body = b"service is OK" if code == 200 else b"boom"
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def http_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", server.server_address[1]
    server.shutdown()


def check(**monitor):
    async def go():
        clients = HttpClients()
        try:
            return await run_check({"timeout": 3, **monitor}, clients)
        finally:
            await clients.aclose()

    return asyncio.run(go())


def closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_http_ok(http_server, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    result = check(type="http", target=http_server[0] + "/")
    assert result.ok and result.detail == "HTTP 200" and result.latency_ms > 0


def test_http_bad_status(http_server, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    result = check(type="http", target=http_server[0] + "/broken")
    assert not result.ok and result.detail.startswith("HTTP 500")
    # ...unless that status is what the operator expects.
    assert check(type="http", target=http_server[0] + "/broken", expected_status="500").ok


def test_http_keyword(http_server, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    assert check(type="http", target=http_server[0], keyword="is OK").ok
    result = check(type="http", target=http_server[0], keyword="maintenance", method="HEAD")
    assert not result.ok and "keyword" in result.detail


def test_http_connection_refused(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    result = check(type="http", target=f"http://127.0.0.1:{closed_port()}/")
    assert not result.ok and result.detail == "Connection refused"


def test_tcp_open_and_closed(http_server):
    result = check(type="tcp", target=f"127.0.0.1:{http_server[1]}")
    assert result.ok and result.latency_ms is not None
    result = check(type="tcp", target=f"127.0.0.1:{closed_port()}")
    assert not result.ok and result.detail == "Connection refused"


def test_dns():
    result = check(type="dns", target="localhost")
    assert result.ok and ("127.0.0.1" in result.detail or "::1" in result.detail)
    result = check(type="dns", target="localhost", expected_ip="10.9.9.9")
    assert not result.ok and "expected 10.9.9.9" in result.detail
    assert not check(type="dns", target="no-such-host.invalid").ok


def test_unknown_type_fails_cleanly():
    assert not check(type="snmp", target="x").ok


def test_describe_error():
    assert describe_error(asyncio.TimeoutError()) == "Timed out"
    assert describe_error(ConnectionRefusedError()) == "Connection refused"
    assert describe_error(OSError("[Errno 101] Network is unreachable")) == "Network unreachable"
    assert describe_error(
        Exception("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                  "certificate has expired")) == "TLS certificate has expired"


LINUX_OK = """PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=11.4 ms

--- 8.8.8.8 ping statistics ---
1 packets transmitted, 1 received, 0% packet loss, time 0ms
"""
WINDOWS_OK = """
Pinging 192.168.1.1 with 32 bytes of data:
Reply from 192.168.1.1: bytes=32 time<1ms TTL=64
"""
WINDOWS_UNREACHABLE = """
Pinging 192.168.1.50 with 32 bytes of data:
Reply from 192.168.1.20: Destination host unreachable.
"""
GERMAN_WINDOWS_OK = "Antwort von 192.168.1.1: Bytes=32 Zeit=3ms TTL=64"


def test_parse_system_ping_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    result = parse_system_ping(0, LINUX_OK, 50.0)
    assert result.ok and result.latency_ms == 11.4
    assert not parse_system_ping(1, "1 packets transmitted, 0 received", 50.0).ok
    assert parse_system_ping(2, "ping: nohost: Name or service not known", 5.0).detail \
        == "DNS lookup failed"


def test_parse_system_ping_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert parse_system_ping(0, WINDOWS_OK, 30.0).latency_ms == 1.0
    assert parse_system_ping(0, GERMAN_WINDOWS_OK, 30.0).latency_ms == 3.0
    # Windows reports success when a router answers "unreachable" on the target's behalf.
    result = parse_system_ping(0, WINDOWS_UNREACHABLE, 30.0)
    assert not result.ok and result.detail == "Host unreachable"


def test_system_ping_command_never_uses_a_shell(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert checks.system_ping_command("10.0.0.1", 2.5) == \
        ["ping", "-n", "-c", "1", "-W", "3", "10.0.0.1"]
    monkeypatch.setattr(sys, "platform", "win32")
    assert checks.system_ping_command("10.0.0.1", 2.5) == \
        ["ping", "-n", "1", "-w", "2500", "10.0.0.1"]


FAKE_PING = """#!/bin/sh
for host; do :; done
if [ "$host" = "10.0.0.1" ]; then
  echo "64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=4.20 ms"
  exit 0
fi
echo "1 packets transmitted, 0 received, 100% packet loss"
exit 1
"""


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script as a stand-in for ping")
def test_icmp_falls_back_to_the_ping_command(tmp_path, monkeypatch):
    fake = tmp_path / "ping"
    fake.write_text(FAKE_PING)
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(checks, "_icmp_mode", "system")

    result = check(type="icmp", target="10.0.0.1")
    assert result.ok and result.latency_ms == 4.2
    result = check(type="icmp", target="10.0.0.2")
    assert not result.ok and result.detail == "No reply"


def test_icmp_reports_when_ping_is_impossible(monkeypatch):
    monkeypatch.setattr(checks, "_icmp_mode", "none")
    result = check(type="icmp", target="10.0.0.1")
    assert not result.ok and "unavailable" in result.detail
