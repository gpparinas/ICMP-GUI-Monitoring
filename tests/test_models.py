import pytest
from pydantic import ValidationError

from netmon.models import (
    SECRET_MASK,
    MonitorIn,
    is_valid_host,
    mask_channel_config,
    split_host_port,
    status_matches,
    validate_channel_config,
)


@pytest.mark.parametrize("host", ["192.168.1.1", "printer.office.lan", "::1", "srv_01", "a"])
def test_valid_hosts(host):
    assert is_valid_host(host)


@pytest.mark.parametrize("host", ["", "-c", "-oProxyCommand=x", "a b", "host;rm -rf /", "a/b"])
def test_invalid_hosts(host):
    assert not is_valid_host(host)


def test_split_host_port():
    assert split_host_port("192.168.1.10:3389") == ("192.168.1.10", 3389)
    assert split_host_port("[::1]:443") == ("::1", 443)
    for bad in ("example.com", "example.com:0", "example.com:99999", ":80", "-x:80"):
        with pytest.raises(ValueError):
            split_host_port(bad)


def test_status_matches():
    assert status_matches(204, "200-399")
    assert status_matches(401, "200,401-403")
    assert not status_matches(500, "200-399")


def test_monitor_target_validation():
    MonitorIn(name="a", type="http", target="https://example.com/health")
    MonitorIn(name="a", type="tcp", target="db.lan:5432")
    with pytest.raises(ValidationError):
        MonitorIn(name="a", type="http", target="example.com")
    with pytest.raises(ValidationError):
        MonitorIn(name="a", type="icmp", target="-f 10.0.0.1")
    with pytest.raises(ValidationError):
        MonitorIn(name="a", type="tcp", target="db.lan")
    with pytest.raises(ValidationError):
        MonitorIn(name="a", type="http", target="https://x.test", expected_status="abc")
    with pytest.raises(ValidationError):
        MonitorIn(name="a", type="icmp", target="10.0.0.1", interval=5, timeout=10)


def test_monitor_options_are_type_specific():
    http = MonitorIn(name="a", type="http", target="https://x.test", keyword="OK")
    assert http.options()["keyword"] == "OK"
    assert MonitorIn(name="a", type="icmp", target="10.0.0.1").options() == {}


def test_channel_secrets_are_masked_and_kept():
    saved = validate_channel_config("telegram", {"bot_token": "123456:ABC-def", "chat_id": "42"})
    masked = mask_channel_config(saved)
    assert masked["bot_token"] == SECRET_MASK and masked["chat_id"] == "42"
    # Saving the form again without retyping the secret keeps the stored one.
    again = validate_channel_config("telegram", {**masked, "chat_id": "43"}, previous=saved)
    assert again == {"bot_token": "123456:ABC-def", "chat_id": "43"}


def test_email_config_validation():
    config = validate_channel_config("email", {
        "host": "smtp.example.com", "from_addr": "monitor@example.com",
        "to_addrs": "it@example.com; boss@example.com",
    })
    assert config["port"] == 587 and config["to_addrs"] == "it@example.com, boss@example.com"
    with pytest.raises(ValidationError):
        validate_channel_config("email", {"host": "h", "from_addr": "x", "to_addrs": "y"})
    with pytest.raises(ValidationError):
        validate_channel_config("slack", {"url": "ftp://hooks"})
