"""Request/validation models shared by the API, the importer and the CLI."""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MONITOR_TYPES = ("icmp", "tcp", "http", "dns")

# Type-specific fields that live in the ``options`` JSON column.
OPTION_FIELDS = {
    "http": ("method", "expected_status", "keyword", "verify_tls", "cert_warn_days"),
    "dns": ("expected_ip",),
    "icmp": (),
    "tcp": (),
}

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9_]([A-Za-z0-9_.-]{0,251}[A-Za-z0-9_.])?$")


def is_valid_host(host: str) -> bool:
    """True for an IP address or a syntactically valid hostname.

    Deliberately strict: hosts are passed to the system ``ping`` command on
    platforms without raw-socket access, so nothing that could be read as a
    command-line option may get through.
    """
    if not host or host.startswith("-"):
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(_HOSTNAME_RE.match(host))


def split_host_port(target: str) -> tuple[str, int]:
    """Parse ``host:port`` / ``[ipv6]:port``. Raises ValueError when malformed."""
    target = target.strip()
    if target.startswith("["):
        host, sep, port = target[1:].partition("]:")
    else:
        host, sep, port = target.rpartition(":")
    if not sep or not port.isdigit():
        raise ValueError("expected host:port, for example 192.168.1.10:3389")
    if not is_valid_host(host):
        raise ValueError(f"invalid host {host!r}")
    if not 1 <= int(port) <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return host, int(port)


def parse_status_spec(spec: str) -> list[tuple[int, int]]:
    """Parse ``"200-399"`` / ``"200,301,401-403"`` into inclusive ranges."""
    ranges: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        low, sep, high = part.partition("-")
        if not low.strip().isdigit() or (sep and not high.strip().isdigit()):
            raise ValueError(f"invalid status code range {part!r}")
        lo = int(low)
        hi = int(high) if sep else lo
        if not (100 <= lo <= hi <= 599):
            raise ValueError(f"invalid status code range {part!r}")
        ranges.append((lo, hi))
    if not ranges:
        raise ValueError("at least one status code is required")
    return ranges


def status_matches(code: int, spec: str) -> bool:
    return any(lo <= code <= hi for lo, hi in parse_status_spec(spec))


class MonitorIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=80)
    type: Literal["icmp", "tcp", "http", "dns"]
    target: str = Field(min_length=1, max_length=2000)
    group: str = Field(default="", max_length=60)
    interval: int = Field(default=60, ge=5, le=86400)
    timeout: float = Field(default=5, ge=0.5, le=60)
    retries: int = Field(default=2, ge=0, le=10)
    latency_warn_ms: int | None = Field(default=None, ge=1, le=600000)
    enabled: bool = True

    # HTTP
    method: Literal["GET", "HEAD"] = "GET"
    expected_status: str = Field(default="200-399", max_length=100)
    keyword: str = Field(default="", max_length=200)
    verify_tls: bool = True
    cert_warn_days: int = Field(default=14, ge=0, le=365)

    # DNS
    expected_ip: str = Field(default="", max_length=45)

    @field_validator("expected_status")
    @classmethod
    def _check_status_spec(cls, value: str) -> str:
        parse_status_spec(value)
        return value

    @field_validator("expected_ip")
    @classmethod
    def _check_expected_ip(cls, value: str) -> str:
        if value:
            try:
                ipaddress.ip_address(value)
            except ValueError as exc:
                raise ValueError("must be an IP address") from exc
        return value

    @model_validator(mode="after")
    def _check_target(self) -> MonitorIn:
        if self.type in ("icmp", "dns"):
            if not is_valid_host(self.target):
                raise ValueError("target must be a hostname or IP address")
        elif self.type == "tcp":
            split_host_port(self.target)
        else:
            parts = urlsplit(self.target)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ValueError("target must be a URL starting with http:// or https://")
        if self.timeout > self.interval:
            raise ValueError("timeout cannot be longer than the check interval")
        return self

    def options(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in OPTION_FIELDS[self.type]}


# --- notification channels -------------------------------------------------


class _ChannelConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


class EmailConfig(_ChannelConfig):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "ssl", "none"] = "starttls"
    username: str = Field(default="", max_length=255)
    password: str = Field(default="", max_length=1000)
    from_addr: str = Field(min_length=3, max_length=255)
    to_addrs: str = Field(min_length=3, max_length=2000)

    @field_validator("from_addr")
    @classmethod
    def _check_from(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError("must be an email address")
        return value

    @field_validator("to_addrs")
    @classmethod
    def _check_to(cls, value: str) -> str:
        addrs = [a.strip() for a in value.replace(";", ",").split(",") if a.strip()]
        if not addrs or any("@" not in a for a in addrs):
            raise ValueError("must be one or more email addresses separated by commas")
        return ", ".join(addrs)


class WebhookConfig(_ChannelConfig):
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("must be a URL starting with http:// or https://")
        return value


class TelegramConfig(_ChannelConfig):
    bot_token: str = Field(min_length=10, max_length=200)
    chat_id: str = Field(min_length=1, max_length=100)

    @field_validator("bot_token")
    @classmethod
    def _check_token(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9:_*-]+", value):
            raise ValueError("does not look like a Telegram bot token")
        return value


CHANNEL_CONFIGS: dict[str, type[_ChannelConfig]] = {
    "email": EmailConfig,
    "slack": WebhookConfig,
    "discord": WebhookConfig,
    "teams": WebhookConfig,
    "telegram": TelegramConfig,
    "webhook": WebhookConfig,
}

# Values that are never sent back to the browser once saved.
SECRET_FIELDS = ("password", "bot_token", "url")
SECRET_MASK = "********"


class NotifierIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=80)
    type: Literal["email", "slack", "discord", "teams", "telegram", "webhook"]
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


def validate_channel_config(
    kind: str, config: dict[str, Any], previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Validate a channel config, keeping saved secrets when the mask is sent back."""
    merged = dict(config)
    for field in SECRET_FIELDS:
        if merged.get(field) == SECRET_MASK and previous and field in previous:
            merged[field] = previous[field]
    return CHANNEL_CONFIGS[kind].model_validate(merged).model_dump()


def mask_channel_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (SECRET_MASK if key in SECRET_FIELDS and value else value)
        for key, value in config.items()
    }


class SettingsIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    site_title: str = Field(default="Network Health Monitor", min_length=1, max_length=60)
    public_view: bool = False
    notify_degraded: bool = True
    retention_days: int = Field(default=14, ge=1, le=365)


class PasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    password: str = Field(max_length=200)


class PasswordChangeIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(min_length=8, max_length=200)
