"""The four check types: ICMP ping, TCP port, HTTP(S) and DNS."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
import shutil
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

from netmon import __version__
from netmon.models import split_host_port, status_matches

log = logging.getLogger("netmon.checks")

MAX_KEYWORD_BODY = 1024 * 1024


@dataclass
class CheckResult:
    ok: bool
    latency_ms: float | None = None
    detail: str = ""
    # Set when the check passed but something needs attention (reported as "degraded").
    warning: str = ""


class HttpClients:
    """Shared connection pools: one that verifies TLS certificates, one that does not."""

    def __init__(self) -> None:
        self._clients: dict[bool, httpx.AsyncClient] = {}

    def get(self, verify: bool = True) -> httpx.AsyncClient:
        client = self._clients.get(verify)
        if client is None:
            client = httpx.AsyncClient(
                verify=verify,
                follow_redirects=True,
                max_redirects=5,
                headers={"User-Agent": f"NetworkHealthMonitor/{__version__}"},
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=0),
            )
            self._clients[verify] = client
        return client

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()


def describe_error(exc: BaseException) -> str:
    """Turn an exception into a short message an operator can act on."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return "Timed out"
    if isinstance(exc, ConnectionRefusedError):
        return "Connection refused"
    if isinstance(exc, socket.gaierror):
        return "DNS lookup failed"
    text = str(exc) or exc.__class__.__name__
    if isinstance(exc, httpx.ProxyError):
        return f"Proxy refused the connection ({text})"[:200]
    lowered = text.lower()
    if "certificate" in lowered and "expired" in lowered:
        return "TLS certificate has expired"
    if "certificate_verify_failed" in lowered or "certificate verify failed" in lowered:
        return "TLS certificate is not trusted"
    if "name or service not known" in lowered or "getaddrinfo failed" in lowered \
            or "nodename nor servname" in lowered or "name resolution" in lowered:
        return "DNS lookup failed"
    if "connection refused" in lowered or "connect call failed" in lowered \
            or "all connection attempts failed" in lowered:
        return "Connection refused"
    if "network is unreachable" in lowered or "no route to host" in lowered:
        return "Network unreachable"
    return re.sub(r"^\[[^\]]*\]\s*", "", text)[:200]


# --- ICMP -------------------------------------------------------------------

_icmp_mode: str | None = None
_PING_TIME_RE = re.compile(r"[=<]\s*([\d.,]+)\s*ms", re.IGNORECASE)


def detect_icmp_mode() -> str:
    """Pick the best available way to ping on this machine.

    ``dgram``  unprivileged ICMP sockets (Linux with ping_group_range, macOS)
    ``raw``    raw sockets (root, or the CAP_NET_RAW capability)
    ``system`` the operating system's ``ping`` command (works for normal users)
    ``none``   nothing available

    Windows always uses ``ping.exe``: raw sockets need Administrator rights there, and
    icmplib's async sockets rely on event-loop calls the Windows loop does not provide.
    """
    global _icmp_mode
    if _icmp_mode is not None:
        return _icmp_mode
    mode = "none"
    if sys.platform != "win32":
        try:
            import icmplib

            for privileged in (False, True):
                try:
                    icmplib.ICMPv4Socket(privileged=privileged).close()
                except icmplib.ICMPLibError:
                    continue
                mode = "raw" if privileged else "dgram"
                break
        except ImportError:
            pass
    if mode == "none" and shutil.which("ping"):
        mode = "system"
    _icmp_mode = mode
    log.info("ICMP mode: %s", mode)
    return mode


def system_ping_command(host: str, timeout: float) -> list[str]:
    if sys.platform == "win32":
        return ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    if sys.platform == "darwin":
        return ["ping", "-n", "-c", "1", "-W", str(int(timeout * 1000)), host]
    return ["ping", "-n", "-c", "1", "-W", str(max(1, math.ceil(timeout))), host]


def parse_system_ping(returncode: int, output: str, elapsed_ms: float) -> CheckResult:
    """Interpret the output of one ``ping`` invocation (any OS, any locale)."""
    # Windows exits 0 for "Destination host unreachable"; a real reply always has a TTL.
    replied = returncode == 0 and (sys.platform != "win32" or "ttl=" in output.lower())
    if not replied:
        lowered = output.lower()
        if "unreachable" in lowered:
            return CheckResult(False, detail="Host unreachable")
        if "unknown host" in lowered or "could not find host" in lowered \
                or "not known" in lowered or "cannot resolve" in lowered:
            return CheckResult(False, detail="DNS lookup failed")
        return CheckResult(False, detail="No reply")
    match = _PING_TIME_RE.search(output)
    latency = float(match.group(1).replace(",", ".")) if match else elapsed_ms
    return CheckResult(True, latency_ms=round(latency, 2))


async def _system_ping(host: str, timeout: float) -> CheckResult:
    start = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        *system_ping_command(host, timeout),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout + 3)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return CheckResult(False, detail="No reply")
    elapsed = (time.perf_counter() - start) * 1000
    return parse_system_ping(proc.returncode or 0, out.decode(errors="replace"), elapsed)


async def check_icmp(monitor: dict[str, Any]) -> CheckResult:
    host, timeout = monitor["target"], float(monitor["timeout"])
    mode = detect_icmp_mode()
    if mode == "system":
        return await _system_ping(host, timeout)
    if mode == "none":
        return CheckResult(
            False, detail="Ping is unavailable: no ICMP permission and no ping command found"
        )

    import icmplib

    try:
        reply = await icmplib.async_ping(
            host, count=1, timeout=timeout, privileged=(mode == "raw")
        )
    except icmplib.NameLookupError:
        return CheckResult(False, detail="DNS lookup failed")
    if not reply.is_alive:
        return CheckResult(False, detail="No reply")
    return CheckResult(True, latency_ms=round(reply.avg_rtt, 2))


# --- TCP ---------------------------------------------------------------------


async def check_tcp(monitor: dict[str, Any]) -> CheckResult:
    host, port = split_host_port(monitor["target"])
    start = time.perf_counter()
    _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), monitor["timeout"])
    latency = (time.perf_counter() - start) * 1000
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return CheckResult(True, latency_ms=round(latency, 2), detail=f"Port {port} open")


# --- HTTP --------------------------------------------------------------------


def _cert_days_left(response: httpx.Response) -> float | None:
    """Days until the server certificate expires, when TLS verification is on."""
    stream = response.extensions.get("network_stream")
    ssl_object = stream.get_extra_info("ssl_object") if stream else None
    if ssl_object is None:
        return None
    try:
        not_after = (ssl_object.getpeercert() or {}).get("notAfter")
        if not not_after:
            return None
        return (ssl.cert_time_to_seconds(not_after) - time.time()) / 86400
    except (ValueError, ssl.SSLError):
        return None


async def _http_request(monitor: dict[str, Any], client: httpx.AsyncClient) -> CheckResult:
    keyword = monitor.get("keyword") or ""
    method = "GET" if keyword else monitor.get("method", "GET")
    start = time.perf_counter()
    async with client.stream(method, monitor["target"], timeout=monitor["timeout"]) as response:
        cert_days = _cert_days_left(response)
        body = b""
        if keyword:
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) >= MAX_KEYWORD_BODY:
                    break
        latency = round((time.perf_counter() - start) * 1000, 2)
        code = response.status_code
        encoding = response.encoding or "utf-8"

    if not status_matches(code, monitor.get("expected_status") or "200-399"):
        return CheckResult(False, latency, f"HTTP {code} {response.reason_phrase}".strip())
    if keyword:
        try:
            text = body.decode(encoding, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        if keyword not in text:
            return CheckResult(False, latency, f"HTTP {code}, keyword {keyword!r} not found")

    warning = ""
    warn_days = monitor.get("cert_warn_days", 14)
    if cert_days is not None and warn_days and cert_days <= warn_days:
        days = max(0, math.floor(cert_days))
        warning = f"TLS certificate expires in {days} day{'s' if days != 1 else ''}"
    return CheckResult(True, latency, f"HTTP {code}", warning)


async def check_http(monitor: dict[str, Any], clients: HttpClients) -> CheckResult:
    client = clients.get(bool(monitor.get("verify_tls", True)))
    # httpx timeouts are per phase; this bounds the whole request.
    return await asyncio.wait_for(_http_request(monitor, client), monitor["timeout"])


# --- DNS ---------------------------------------------------------------------


async def check_dns(monitor: dict[str, Any]) -> CheckResult:
    loop = asyncio.get_running_loop()
    start = time.perf_counter()
    infos = await asyncio.wait_for(
        loop.getaddrinfo(monitor["target"], None, type=socket.SOCK_STREAM), monitor["timeout"]
    )
    latency = round((time.perf_counter() - start) * 1000, 2)
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        return CheckResult(False, detail="No addresses returned")
    summary = ", ".join(addresses[:3]) + (" …" if len(addresses) > 3 else "")
    expected = monitor.get("expected_ip") or ""
    if expected and expected not in addresses:
        return CheckResult(False, latency, f"Resolved to {summary}, expected {expected}")
    return CheckResult(True, latency, f"Resolves to {summary}")


# --- dispatch ----------------------------------------------------------------


async def run_check(monitor: dict[str, Any], clients: HttpClients) -> CheckResult:
    """Run one check. Never raises: every failure becomes a failed CheckResult."""
    try:
        kind = monitor["type"]
        if kind == "icmp":
            return await check_icmp(monitor)
        if kind == "tcp":
            return await check_tcp(monitor)
        if kind == "http":
            return await check_http(monitor, clients)
        if kind == "dns":
            return await check_dns(monitor)
        return CheckResult(False, detail=f"Unknown monitor type {kind!r}")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a check must never crash the scheduler
        return CheckResult(False, detail=describe_error(exc))
