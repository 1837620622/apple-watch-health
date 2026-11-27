"""DVT hold/restore engine tests with a fake LocationSimulation channel (no device needed)."""

from __future__ import annotations

import asyncio
import os

import pytest

from apple_watch_health import aio, dvt
from apple_watch_health.cli import build_parser


class FakeChannel:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def invoke(self, method: str, *args, expects_reply: bool = True):
        self.log.append(method)
        return None


class FakeService:
    def __init__(self, log: list[str]) -> None:
        self._channel = FakeChannel(log)


class FakeLoc:
    def __init__(self, log: list[str], fail_after_sets: int | None = None) -> None:
        self.log = log
        self.service = FakeService(log)
        self.sets = 0
        self.fail_after_sets = fail_after_sets

    async def set(self, lat: float, lon: float) -> None:
        self.sets += 1
        if self.fail_after_sets is not None and self.sets > self.fail_after_sets:
            raise ConnectionResetError("tunnel dropped")
        self.log.append(f"set {lat:.2f},{lon:.2f}")

    async def clear(self) -> None:
        self.log.append("clear")


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AWH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(dvt, "REFRESH_SECONDS", 0.3)
    monkeypatch.setattr(dvt, "RECONNECT_DELAYS", (0.05,))
    yield


def _patch_channel(monkeypatch, factory):
    async def fake_open(stack, *, serial=None):
        return factory(), "iPhone17,1 iOS 27.2.1"

    monkeypatch.setattr(dvt, "_open_location_channel", fake_open)


def test_hold_keeps_session_alive_then_restores(monkeypatch) -> None:
    log: list[str] = []
    _patch_channel(monkeypatch, lambda: FakeLoc(log))
    seen_active: list[bool] = []

    async def scenario():
        task = asyncio.create_task(dvt.dvt_hold_location(22.28, 114.16, hold_seconds=1.0))
        await asyncio.sleep(0.4)
        st = dvt.read_dvt_state()
        seen_active.append(bool(st and st.get("active") and st.get("pid") == os.getpid()))
        return await task

    state = aio.run(scenario())
    assert seen_active == [True], "location must stay active while the session is held"
    assert log[0] == "set 22.28,114.16"
    assert log.count("set 22.28,114.16") >= 2, "coordinate is refreshed while holding"
    assert log[-1] == "stopLocationSimulation"
    assert state["cleared"] is True and state["acknowledged"] is True
    assert dvt.read_dvt_state() is None


def test_stop_file_triggers_graceful_restore(monkeypatch) -> None:
    log: list[str] = []
    _patch_channel(monkeypatch, lambda: FakeLoc(log))

    async def scenario():
        task = asyncio.create_task(dvt.dvt_hold_location(1.0, 2.0, hold_seconds=0))
        await asyncio.sleep(0.3)
        dvt.dvt_stop_file().write_text("x", encoding="utf-8")
        return await asyncio.wait_for(task, 5)

    state = aio.run(scenario())
    assert state["cleared"] is True
    assert log[-1] == "stopLocationSimulation"
    assert not dvt.dvt_stop_file().exists()


def test_reconnects_after_tunnel_drop(monkeypatch) -> None:
    log: list[str] = []
    attempts = {"n": 0}

    def factory():
        attempts["n"] += 1
        return FakeLoc(log, fail_after_sets=1 if attempts["n"] == 1 else None)

    _patch_channel(monkeypatch, factory)
    events: list[str] = []
    state = aio.run(
        dvt.dvt_hold_location(
            1.0, 2.0, hold_seconds=1.2, on_event=lambda **e: events.append(str(e["event"]))
        )
    )
    assert attempts["n"] >= 2
    assert "reconnecting" in events and "reconnected" in events
    assert state["reconnects"] >= 1 and state["cleared"] is True


def test_first_connection_failure_is_actionable(monkeypatch) -> None:

    async def boom(stack, *, serial=None):
        raise ConnectionRefusedError("usbmuxd not running")

    monkeypatch.setattr(dvt, "_open_location_channel", boom)
    with pytest.raises(dvt.DvtSetupError) as info:
        aio.run(dvt.dvt_hold_location(1.0, 2.0, hold_seconds=1))
    assert "提示" in str(info.value)
    assert dvt.read_dvt_state() is None


def test_stale_state_is_not_an_active_holder(tmp_path) -> None:
    dvt._save_dvt_state({"active": True, "pid": 999_999_999, "label": "x"})
    assert dvt.active_dvt_holder() is None
    assert dvt.read_dvt_state() is None, "stale state file must be cleaned up"


def test_locate_parser_flags() -> None:
    ns = build_parser().parse_args(
        ["locate", "--place", "东京", "--detach", "--coord-type", "gcj02"]
    )
    assert ns.detach and ns.hold == 0.0 and ns.engine == "dvt" and ns.coord_type == "gcj02"
    # Negative coordinates & leading-dash label passed via `--flag=value` (used by --detach).
    ns_neg = build_parser().parse_args(
        [
            "locate",
            "--engine=dvt",
            "--lat=-33.8688000",
            "--lon=-73.9855000",
            "--hold=0.0",
            "--label=-33.8688, -73.9855",
            "--holder-process",
        ]
    )
    assert ns_neg.lat == pytest.approx(-33.8688)
    assert ns_neg.lon == pytest.approx(-73.9855)
    assert ns_neg.label == "-33.8688, -73.9855"
    ns2 = build_parser().parse_args(["devmode", "--reveal"])
    assert ns2.reveal and not ns2.enable


def test_aio_run_returns_value() -> None:
    async def f():
        return 42

    assert aio.run(f()) == 42
    assert aio.run(f(), selector=False) == 42


def test_cancellation_still_sends_stop_location_simulation(monkeypatch) -> None:
    log: list[str] = []
    _patch_channel(monkeypatch, lambda: FakeLoc(log))

    async def scenario():
        task = asyncio.create_task(dvt.dvt_hold_location(22.28, 114.16, hold_seconds=0))
        await asyncio.sleep(0.25)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    aio.run(scenario())
    assert "stopLocationSimulation" in log
    assert dvt.read_dvt_state() is None


def test_stop_file_targets_matching_pid_only(monkeypatch) -> None:
    dvt._write_stop_signal(12345)
    assert dvt._stop_file_targets_pid(12345) is True
    assert dvt._stop_file_targets_pid(99999) is False
    dvt._clear_stop_file(only_pid=99999)
    assert dvt.dvt_stop_file().exists()
    dvt._clear_stop_file(only_pid=12345)
    assert not dvt.dvt_stop_file().exists()


def test_clear_falls_back_to_device_when_holder_exited_without_clearing(monkeypatch) -> None:
    log: list[str] = []
    _patch_channel(monkeypatch, lambda: FakeLoc(log))
    # Simulate a holder PID that is alive on check #1, then dies without writing last_clear.
    dvt._save_dvt_state({"active": False, "pid": 424242, "serial": "UDID-1"})
    checks = {"n": 0}

    def fake_alive(pid: int) -> bool:
        checks["n"] += 1
        return checks["n"] == 1

    monkeypatch.setattr("apple_watch_health.runner.is_pid_alive", fake_alive)
    ok, msg = aio.run(dvt.dvt_clear_location())
    assert ok is True
    assert "stopLocationSimulation" in log
    assert "已发送" in msg or "获手机确认" in msg


def test_ios17_auto_mount_reconnects_fresh_rsd_tunnel(monkeypatch) -> None:
    """Regression: after auto_mount(rsd), a new UserspaceRsdTunnel must be opened to refresh peer_info."""
    events: list[str] = []

    class InvalidServiceError(Exception):
        pass

    class FakeTunnel:
        def __init__(self, serial=None) -> None:
            self.serial = serial
            self.idx = 0

        async def __aenter__(self):
            events.append("tunnel_open")
            self.idx = events.count("tunnel_open")
            return f"rsd-{self.idx}"

        async def __aexit__(self, *a):
            events.append(f"tunnel_close_{self.idx}")

    class FakeDvtProvider:
        def __init__(self, rsd) -> None:
            self.rsd = rsd

        async def __aenter__(self):
            events.append(f"dvt_open_{self.rsd}")
            if self.rsd == "rsd-1":
                raise InvalidServiceError("dtservicehub missing before DDI mount")
            return f"dvt-{self.rsd}"

        async def __aexit__(self, *a):
            events.append(f"dvt_close_{self.rsd}")

    class FakeLocationSimulation:
        def __init__(self, dvt_inst) -> None:
            self.dvt_inst = dvt_inst

        async def __aenter__(self):
            events.append(f"loc_open_{self.dvt_inst}")
            return self

        async def __aexit__(self, *a):
            events.append(f"loc_close_{self.dvt_inst}")

    async def fake_auto_mount(rsd) -> None:
        events.append(f"auto_mount_{rsd}")

    import pymobiledevice3.remote.userspace_tunnel as ut_mod
    import pymobiledevice3.services.dvt.instruments.dvt_provider as dp_mod
    import pymobiledevice3.services.dvt.instruments.location_simulation as ls_mod
    import pymobiledevice3.services.mobile_image_mounter as mim_mod

    monkeypatch.setattr(ut_mod, "UserspaceRsdTunnel", FakeTunnel)
    monkeypatch.setattr(dp_mod, "DvtProvider", FakeDvtProvider)
    monkeypatch.setattr(ls_mod, "LocationSimulation", FakeLocationSimulation)
    monkeypatch.setattr(mim_mod, "auto_mount", fake_auto_mount)

    async def scenario():
        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            loc = await dvt._open_rsd_dvt_channel(stack, serial="UDID-1")
            assert loc.dvt_inst == "dvt-rsd-2"

    aio.run(scenario())
    assert events == [
        "tunnel_open",
        "dvt_open_rsd-1",
        "auto_mount_rsd-1",
        "tunnel_close_1",
        "tunnel_open",
        "dvt_open_rsd-2",
        "loc_open_dvt-rsd-2",
        "loc_close_dvt-rsd-2",
        "dvt_close_rsd-2",
        "tunnel_close_2",
    ]


def test_ios16_uses_legacy_dt_simulate_location_with_auto_mount(monkeypatch) -> None:
    calls: list[str] = []

    class InvalidServiceError(Exception):
        pass

    class FakeDtSimulateLocation:
        def __init__(self, lockdown) -> None:
            self.lockdown = lockdown

        async def set(self, lat: float, lon: float) -> None:
            calls.append(f"dt_set:{lat:.2f},{lon:.2f}")
            if calls.count(f"dt_set:{lat:.2f},{lon:.2f}") == 1:
                raise InvalidServiceError("need DDI")

        async def clear(self) -> None:
            calls.append("dt_clear")

    async def fake_auto_mount(lockdown) -> None:
        calls.append("auto_mount_legacy")

    import pymobiledevice3.services.mobile_image_mounter as mim_mod
    import pymobiledevice3.services.simulate_location as sim_mod

    monkeypatch.setattr(sim_mod, "DtSimulateLocation", FakeDtSimulateLocation)
    monkeypatch.setattr(mim_mod, "auto_mount", fake_auto_mount)

    async def scenario():
        ch = dvt._LegacyDtLocationChannel(object())
        await ch.set(39.90, 116.40)
        await ch.clear()

    aio.run(scenario())
    assert calls == ["dt_set:39.90,116.40", "auto_mount_legacy", "dt_set:39.90,116.40", "dt_clear"]


def test_ios17_auto_mount_falls_back_to_usb_lockdown_when_rsd_mount_fails(monkeypatch) -> None:
    events: list[str] = []

    class InvalidServiceError(Exception):
        pass

    class FakeTunnel:
        def __init__(self, serial=None) -> None:
            self.idx = 0

        async def __aenter__(self):
            events.append("tunnel_open")
            self.idx = events.count("tunnel_open")
            return f"rsd-{self.idx}"

        async def __aexit__(self, *a):
            events.append(f"tunnel_close_{self.idx}")

    class FakeDvtProvider:
        def __init__(self, rsd) -> None:
            self.rsd = rsd

        async def __aenter__(self):
            events.append(f"dvt_open_{self.rsd}")
            if self.rsd == "rsd-1":
                raise InvalidServiceError("missing dtservicehub")
            return f"dvt-{self.rsd}"

        async def __aexit__(self, *a):
            events.append(f"dvt_close_{self.rsd}")

    class FakeLocationSimulation:
        def __init__(self, dvt_inst) -> None:
            self.dvt_inst = dvt_inst

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    class FakeUsbLockdown:
        async def close(self):
            events.append("usb_lockdown_close")

    async def fake_lockdown(serial):
        events.append("usb_lockdown_open")
        return FakeUsbLockdown()

    async def fake_auto_mount(provider) -> None:
        if isinstance(provider, str):
            events.append(f"rsd_mount_fail_{provider}")
            raise RuntimeError("shim mount failed")
        events.append("usb_mount_ok")

    import pymobiledevice3.remote.userspace_tunnel as ut_mod
    import pymobiledevice3.services.dvt.instruments.dvt_provider as dp_mod
    import pymobiledevice3.services.dvt.instruments.location_simulation as ls_mod
    import pymobiledevice3.services.mobile_image_mounter as mim_mod

    monkeypatch.setattr(ut_mod, "UserspaceRsdTunnel", FakeTunnel)
    monkeypatch.setattr(dp_mod, "DvtProvider", FakeDvtProvider)
    monkeypatch.setattr(ls_mod, "LocationSimulation", FakeLocationSimulation)
    monkeypatch.setattr(mim_mod, "auto_mount", fake_auto_mount)
    monkeypatch.setattr(dvt, "_lockdown", fake_lockdown)

    async def scenario():
        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            loc = await dvt._open_rsd_dvt_channel(stack, serial="UDID-1", ios_major=18)
            assert loc.dvt_inst == "dvt-rsd-2"

    aio.run(scenario())
    assert "rsd_mount_fail_rsd-1" in events
    assert "usb_lockdown_open" in events
    assert "usb_mount_ok" in events
    assert "usb_lockdown_close" in events
    assert "dvt_open_rsd-2" in events


def test_gpx_route_playback_and_jitter(monkeypatch) -> None:
    from apple_watch_health.profile import RoutePoint

    log: list[str] = []
    _patch_channel(monkeypatch, lambda: FakeLoc(log))
    route = [
        RoutePoint(39.9087, 116.3975, delay_seconds=None),
        RoutePoint(39.9100, 116.4000, delay_seconds=0.05),
        RoutePoint(39.9120, 116.4020, delay_seconds=0.05),
    ]
    state = aio.run(
        dvt.dvt_play_gpx(route, label="test.gpx", speed=2.0, loop_route=False, jitter_m=0.0)
    )
    assert state["points_sent"] == 3
    assert state["cleared"] is True
    assert log[:3] == ["set 39.91,116.40", "set 39.91,116.40", "set 39.91,116.40"]
    assert log[-1] == "stopLocationSimulation"
    assert dvt.read_dvt_state() is None
