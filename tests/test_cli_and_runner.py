"""Tests for CLI parsing, model mapping, sanitization, and nested resource cleanup."""

from __future__ import annotations

import pytest

from apple_watch_health.cli import build_parser, main
from apple_watch_health.device import classify_watch_model, friendly_model, sanitize_log_message
from apple_watch_health.profile import PRESETS, resolve_place
from apple_watch_health.runner import RunConfig, run_session


def test_cli_presets_and_features_exit_zero(capsys) -> None:
    assert main(["presets"]) == 0
    out = capsys.readouterr().out
    assert "hk" in out and "us-sf" in out

    assert main(["features"]) == 0
    feat_out = capsys.readouterr().out
    assert "高血压通知" in feat_out and "睡眠呼吸暂停" in feat_out


def test_cli_validation_rejects_invalid_duration_and_partial_coords() -> None:
    assert main(["probe", "--duration", "99999"]) == 2
    assert main(["send", "--lat", "22.28", "--no-sandbox"]) in (1, 2)
    parser = build_parser()
    ns = parser.parse_args(["send", "--preset", "hk"])
    assert ns.preset in PRESETS

    loc_ns = parser.parse_args(["locate", "--place", "东京"])
    assert loc_ns.place == "东京"


def test_resolve_place_and_restore(capsys, tmp_path, monkeypatch) -> None:
    lat, lon, _alt, label = resolve_place("东京", allow_online=False)
    assert round(lat, 2) == 35.66 and round(lon, 2) == 139.70
    assert "东京" in label

    lat2, lon2, _alt2, _ = resolve_place("22.2819, 114.1589", allow_online=False)
    assert lat2 == pytest.approx(22.2819) and lon2 == pytest.approx(114.1589)

    lat3, lon3, _alt3, _ = resolve_place("us-sf", allow_online=False)
    assert lat3 == pytest.approx(37.7749) and lon3 == pytest.approx(-122.4194)

    with pytest.raises(ValueError):
        resolve_place("99.0, 200.0", allow_online=False)

    monkeypatch.setenv("AWH_DATA_DIR", str(tmp_path))
    assert main(["restore"]) == 0
    out = capsys.readouterr().out
    assert "零系统副作用" in out


def test_model_mapping_and_log_sanitization() -> None:
    assert friendly_model("iPhone17,1") == "iPhone 16 Pro"
    assert friendly_model("iPhone99,9") == "iPhone99,9"

    assert classify_watch_model("Watch8,3") == ("Apple Watch Series 12", True, True)
    assert classify_watch_model("Watch7,6") == ("Apple Watch SE 3", False, True)
    assert classify_watch_model("Watch6,14") == ("Apple Watch Series 8", False, False)

    sanitized = sanitize_log_message("user@example.com udid=DEADBEEF-0123456789ABCDEF   HK")
    assert "user@example.com" not in sanitized
    assert "DEADBEEF-0123456789ABCDEF" not in sanitized
    assert "<EMAIL>" in sanitized and "<UDID>" in sanitized


async def test_runner_closes_phone_and_auth_even_if_link_close_raises(monkeypatch, tmp_path) -> None:
    closed: list[str] = []

    class FakePhone:
        async def start_lockdown_service(self, _name: str):
            return object()

        async def close(self) -> None:
            closed.append("phone")

    class FakeLink:
        def __init__(self, _stream, _emit) -> None:
            pass

        async def handshake(self) -> None:
            raise RuntimeError("boom during handshake")

        async def close(self) -> None:
            closed.append("link")
            raise RuntimeError("boom during link close")

    class FakeAuth:
        def __init__(self, *_a, **_kw) -> None:
            pass

        async def close(self) -> None:
            closed.append("auth")

    async def fake_mux(**_kw):
        return FakePhone()

    monkeypatch.setattr("apple_watch_health.runner.create_using_usbmux", fake_mux)
    monkeypatch.setattr("apple_watch_health.runner.Link", FakeLink)
    monkeypatch.setattr("apple_watch_health.runner.AuthenticationProvider", FakeAuth)

    cfg = RunConfig(
        command="probe",
        cert_file=tmp_path / "c.p7b",
        key_file=tmp_path / "k.pk8",
        duration=5.0,
        sandbox=False,
    )
    with pytest.raises(RuntimeError, match="boom during link close"):
        await run_session(cfg)

    assert closed == ["link", "phone", "auth"]
