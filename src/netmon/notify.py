"""Alert delivery: email, Slack, Discord, Microsoft Teams, Telegram and generic webhooks."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

import httpx

from netmon.db import Database

log = logging.getLogger("netmon.notify")

EVENT_LABELS = {
    "down": ("🔴", "DOWN"),
    "recovered": ("🟢", "RECOVERED"),
    "degraded": ("🟡", "DEGRADED"),
    "resolved": ("🟢", "BACK TO NORMAL"),
    "test": ("🔔", "TEST"),
}


def format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(days, "d"), (hours, "h"), (minutes, "m"), (secs, "s")]
    shown = [f"{value}{unit}" for value, unit in parts if value][:2]
    return " ".join(shown) or "0s"


@dataclass
class Alert:
    kind: str  # down | recovered | degraded | resolved | test
    monitor_id: int | None
    name: str
    monitor_type: str
    target: str
    status: str
    previous_status: str
    detail: str
    timestamp: float
    duration: float | None = None  # how long the problem lasted (recovery events)
    site_title: str = "Network Health Monitor"

    @property
    def title(self) -> str:
        icon, label = EVENT_LABELS.get(self.kind, ("", self.kind.upper()))
        return f"{icon} {label}: {self.name}".strip()

    @property
    def subject(self) -> str:
        _, label = EVENT_LABELS.get(self.kind, ("", self.kind.upper()))
        return f"[{self.site_title}] {label}: {self.name}"

    def lines(self) -> list[str]:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(self.timestamp))
        lines = [f"Target: {self.target} ({self.monitor_type.upper()})"]
        if self.detail:
            lines.append(f"Detail: {self.detail}")
        if self.duration is not None:
            lines.append(f"Duration: {format_duration(self.duration)}")
        lines.append(f"Time: {stamp}")
        return lines

    @property
    def text(self) -> str:
        return "\n".join([self.title, *self.lines()])

    def payload(self) -> dict[str, Any]:
        return {
            "event": self.kind,
            "monitor": {
                "id": self.monitor_id,
                "name": self.name,
                "type": self.monitor_type,
                "target": self.target,
            },
            "status": self.status,
            "previous_status": self.previous_status,
            "detail": self.detail,
            "duration_seconds": None if self.duration is None else round(self.duration),
            "timestamp": int(self.timestamp),
            "source": self.site_title,
        }


def _send_email(config: dict[str, Any], alert: Alert) -> None:
    message = EmailMessage()
    message["Subject"] = alert.subject
    message["From"] = config["from_addr"]
    message["To"] = config["to_addrs"]
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message.set_content(f"{alert.text}\n\n-- \nSent by {alert.site_title}\n")

    context = ssl.create_default_context()
    if config["security"] == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            config["host"], config["port"], timeout=20, context=context
        )
    else:
        server = smtplib.SMTP(config["host"], config["port"], timeout=20)
    with server:
        if config["security"] == "starttls":
            server.starttls(context=context)
        if config.get("username"):
            server.login(config["username"], config.get("password", ""))
        server.send_message(message)


def _teams_card(alert: Alert) -> dict[str, Any]:
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": alert.title, "weight": "Bolder",
                         "size": "Medium", "wrap": True},
                        *[{"type": "TextBlock", "text": line, "wrap": True, "spacing": "Small"}
                          for line in alert.lines()],
                    ],
                },
            }
        ],
    }


class Notifier:
    def __init__(self, db: Database):
        self.db = db
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, url: str, body: dict[str, Any]) -> None:
        response = await self._http().post(url, json=body)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200].strip()}")

    async def send(self, channel: dict[str, Any], alert: Alert) -> None:
        """Deliver one alert to one channel. Raises on failure."""
        kind, config = channel["type"], channel["config"]
        if kind == "email":
            await asyncio.to_thread(_send_email, config, alert)
        elif kind == "slack":
            body = "\n".join(alert.lines())
            await self._post(config["url"], {"text": f"*{alert.title}*\n{body}"})
        elif kind == "discord":
            body = "\n".join(alert.lines())
            await self._post(config["url"], {"content": f"**{alert.title}**\n{body}"})
        elif kind == "teams":
            await self._post(config["url"], _teams_card(alert))
        elif kind == "telegram":
            await self._post(
                f"https://api.telegram.org/bot{config['bot_token']}/sendMessage",
                {"chat_id": config["chat_id"], "text": alert.text,
                 "disable_web_page_preview": True},
            )
        elif kind == "webhook":
            await self._post(config["url"], alert.payload())
        else:
            raise RuntimeError(f"Unknown channel type {kind!r}")

    async def deliver(self, channel: dict[str, Any], alert: Alert) -> str:
        """Deliver and record the outcome. Returns an error message, or '' on success."""
        try:
            await self.send(channel, alert)
            error = ""
        except Exception as exc:  # noqa: BLE001 - one bad channel must not stop the others
            error = _safe_error(exc, channel)
            log.warning("Alert via %s %r failed: %s", channel["type"], channel["name"], error)
        self.db.set_notifier_result(channel["id"], error)
        return error

    async def dispatch(self, alert: Alert) -> None:
        channels = [c for c in self.db.list_notifiers() if c["enabled"]]
        if channels:
            await asyncio.gather(*(self.deliver(channel, alert) for channel in channels))


def _safe_error(exc: Exception, channel: dict[str, Any]) -> str:
    """Error text for the UI and logs, with any secret from the channel config removed."""
    if isinstance(exc, httpx.TimeoutException):
        text = "Timed out"
    elif isinstance(exc, httpx.HTTPError):
        text = f"{exc.__class__.__name__}: {exc}"
    else:
        text = str(exc) or exc.__class__.__name__
    for key in ("password", "bot_token", "url"):
        secret = channel["config"].get(key)
        if secret:
            text = text.replace(str(secret), "***")
    return text[:300]
