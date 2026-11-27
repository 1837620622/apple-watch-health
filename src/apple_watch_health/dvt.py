"""No-root cross-platform (macOS & Windows) DVT / DtSimulateLocation engine.

Apple's official Developer location simulation channels (matching i4Tools / Xcode behavior):

* iOS 17.4+  : in-process ``UserspaceRsdTunnel`` (CoreDeviceProxy over USB, no sudo/Admin)
               + DVT ``LocationSimulation`` (with automatic DDI mount & RSD refresh).
* iOS 17.0-3 : the same tunnel falls back to RemotePairing over bonjour (same Wi-Fi needed).
* iOS <= 16  : classic lockdown ``DtSimulateLocation`` (``com.apple.dt.simulatelocation``)
               with automatic Developer Disk Image mounting.

Key behaviour: on iOS 17+ the simulated location only lives as long as the DVT session that
set it. Closing the tunnel right after ``set`` makes the phone snap back to its real GPS within
seconds. The engine therefore *holds* the session: in the foreground until Ctrl+C / ``--hold``
expiry, or in a detached background holder process (``--detach``) that ``awh restore`` stops
cleanly. While holding it re-applies the coordinate periodically and transparently reconnects
after USB/tunnel hiccups.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any

from .paths import atomic_write_text, data_dir, ensure_private_dir
from .profile import RoutePoint, apply_gps_jitter

# Re-apply the simulated coordinate this often while holding (keeps CoreLocation fresh and
# doubles as a liveness probe of the tunnel).
REFRESH_SECONDS = 10.0
# Back-off between reconnect attempts after the tunnel drops.
RECONNECT_DELAYS = (1.0, 2.0, 3.0, 5.0, 8.0)
ACK_TIMEOUT = 5.0


class DvtSetupError(RuntimeError):
    """Actionable, user-facing DVT failure (message already localized)."""


# --------------------------------------------------------------------------------------------
# State / control files
# --------------------------------------------------------------------------------------------


def dvt_state_file() -> Path:
    return data_dir() / "dvt_state.json"


def dvt_stop_file() -> Path:
    return data_dir() / "dvt.stop"


def dvt_last_clear_file() -> Path:
    return data_dir() / "dvt_last_clear.json"


def dvt_log_file() -> Path:
    return data_dir() / "dvt.log"


def _save_dvt_state(state: dict[str, object]) -> None:
    atomic_write_text(dvt_state_file(), json.dumps(state, ensure_ascii=False))


def _record_last_clear(pid: int, *, acknowledged: bool) -> None:
    payload = {"pid": pid, "cleared_at": time.time(), "acknowledged": acknowledged}
    with suppress(OSError):
        atomic_write_text(dvt_last_clear_file(), json.dumps(payload, ensure_ascii=False))


def _read_last_clear() -> dict[str, object] | None:
    path = dvt_last_clear_file()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read_dvt_state() -> dict[str, object] | None:
    sf = dvt_state_file()
    if not sf.is_file():
        return None
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _clear_dvt_state(*, only_pid: int | None = None) -> None:
    if only_pid is not None:
        cur = read_dvt_state()
        if cur and int(cur.get("pid") or 0) not in (0, only_pid):
            return  # another holder took over; don't delete its state
    dvt_state_file().unlink(missing_ok=True)


def _write_stop_signal(target_pid: int | None = None) -> None:
    marker = "*" if target_pid is None else str(target_pid)
    atomic_write_text(dvt_stop_file(), marker)


def _stop_file_targets_pid(pid: int) -> bool:
    sf = dvt_stop_file()
    if not sf.exists():
        return False
    try:
        content = sf.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return True
    if not content or content == "*":
        return True
    if content.isdigit():
        return int(content) == pid
    # Backward compatibility with legacy timestamp stop files (e.g. "1730000000.12" or "x").
    return True


def _clear_stop_file(*, only_pid: int | None = None) -> None:
    if only_pid is not None and not _stop_file_targets_pid(only_pid):
        return
    dvt_stop_file().unlink(missing_ok=True)


def active_dvt_holder() -> dict[str, object] | None:
    """Return the state of a live DVT holder process, cleaning up stale state files."""
    from .runner import is_pid_alive  # local import: runner imports pymobiledevice3 lazily

    state = read_dvt_state()
    if not state:
        return None
    pid = int(state.get("pid") or 0)
    if pid and is_pid_alive(pid):
        return state
    # Clean stale state from crashed/terminated holders while leaving active=False restore
    # hints only if no PID was recorded.
    if pid:
        _clear_dvt_state(only_pid=pid)
    return None


# --------------------------------------------------------------------------------------------
# Channel setup (iOS 17+ UserspaceRsdTunnel+DVT & iOS <=16 DtSimulateLocation)
# --------------------------------------------------------------------------------------------


def _parse_ios_major(version: object) -> int:
    text = str(version or "0").strip()
    head = text.split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        return 0


def _hint_for(exc: BaseException) -> str:
    name = type(exc).__name__
    text = str(exc)
    if name in ("DeveloperModeIsNotEnabledError", "DeveloperModeError") or "Developer Mode" in text:
        return "iPhone 未开启开发者模式：运行 `awh devmode --reveal` 后到「设置 → 隐私与安全性 → 开发者模式」打开并重启"
    if name in ("NoDeviceConnectedError", "DeviceNotFoundError", "ConnectionFailedToUsbmuxdError"):
        return "未检测到 USB iPhone：换数据线、解锁屏幕并点【信任】；Windows 需安装微软商店「Apple 设备」或官方版 iTunes 并保持服务运行"
    if name in ("PasswordRequiredError", "PairingDialogResponsePendingError", "NotPairedError"):
        return "iPhone 尚未信任此电脑：解锁手机，在弹窗中点【信任】并输入锁屏密码后重试"
    if name == "UserspaceTunnelUnavailableError":
        return "该 iOS 版本无法建立免 Root 隧道（iOS 17.0–17.3 需手机与电脑同一 Wi-Fi；建议升级到 iOS 17.4+）"
    if name in ("InvalidServiceError", "StartServiceError"):
        return "开发者服务不可用：确认开发者模式已打开且电脑能联网自动下载并挂载 Developer Disk Image (DDI)"
    return "确认 iPhone 已解锁、已信任此电脑并开启开发者模式；仍失败可加 --debug 查看详细堆栈"


async def _lockdown(serial: str | None):
    from pymobiledevice3.lockdown import create_using_usbmux

    # Prefer USB so a Wi-Fi-synced iPhone on the same LAN does not shadow the cabled device.
    return await create_using_usbmux(serial=serial, connection_type="USB", autopair=True)


async def _precheck(serial: str | None) -> tuple[str | None, int, str]:
    """Fail fast with a clear message instead of a deep tunnel/DTX stack trace.

    Returns (resolved_udid, ios_major, device_desc).
    """
    lockdown = await _lockdown(serial)
    try:
        version = str(lockdown.product_version or "0")
        major = _parse_ios_major(version)
        desc = f"{lockdown.all_values.get('ProductType', 'iPhone')} iOS {version}"
        if major >= 16:
            try:
                enabled = await lockdown.get_developer_mode_status()
            except Exception:
                enabled = True  # unknown -> let the real attempt decide
            if not enabled:
                raise DvtSetupError(
                    f"{desc} 未开启开发者模式（DVT 定位必需）。\n"
                    "  1) 运行 `awh devmode --reveal`（Windows 没装过 Xcode 时该开关默认隐藏）\n"
                    "  2) iPhone「设置 → 隐私与安全性 → 开发者模式」打开 → 重启 → 解锁后点【打开】\n"
                    "  或：不需要微信/地图全局生效时，改用 `--engine iap2`（免开发者模式）"
                )
        return lockdown.udid, major, desc
    finally:
        with suppress(Exception):
            await lockdown.close()


class _LegacyDtLocationChannel:
    """iOS <= 16 adapter over `com.apple.dt.simulatelocation` (matching i4Tools) with auto DDI."""

    def __init__(self, lockdown: Any) -> None:
        self._lockdown = lockdown
        self._mounted_tried = False

    async def _run_with_ddi(self, action: str, *args: float) -> None:
        from pymobiledevice3.exceptions import AlreadyMountedError
        from pymobiledevice3.services.mobile_image_mounter import auto_mount
        from pymobiledevice3.services.simulate_location import DtSimulateLocation

        svc = DtSimulateLocation(self._lockdown)
        try:
            if action == "set":
                await svc.set(args[0], args[1])
            else:
                await svc.clear()
        except Exception as first_err:
            if (
                self._mounted_tried
                or type(first_err).__name__ not in ("InvalidServiceError", "StartServiceError")
            ):
                raise
            self._mounted_tried = True
            try:
                await auto_mount(self._lockdown)
            except AlreadyMountedError:
                raise first_err from None
            if action == "set":
                await svc.set(args[0], args[1])
            else:
                await svc.clear()

    async def set(self, latitude: float, longitude: float) -> None:
        await self._run_with_ddi("set", latitude, longitude)

    async def clear(self) -> None:
        await self._run_with_ddi("clear")


async def _open_rsd_dvt_channel(
    stack: AsyncExitStack,
    *,
    serial: str | None,
    ios_major: int = 17,
) -> Any:
    """Open iOS 17+ DVT LocationSimulation, auto-mounting DDI and refreshing RSD peer_info."""
    from pymobiledevice3.exceptions import AlreadyMountedError
    from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
    from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
    from pymobiledevice3.services.mobile_image_mounter import auto_mount

    inner = AsyncExitStack()
    try:
        rsd = await inner.enter_async_context(UserspaceRsdTunnel(serial=serial))
        try:
            dvt = await inner.enter_async_context(DvtProvider(rsd))
            loc = await inner.enter_async_context(LocationSimulation(dvt))
        except Exception as first_err:
            # Only a missing developer service is fixed by mounting the DDI; anything else
            # (not trusted, developer mode off, cable) would just waste a 20 MB download.
            if type(first_err).__name__ not in ("InvalidServiceError", "StartServiceError"):
                raise
            try:
                await auto_mount(rsd)
            except AlreadyMountedError:
                raise first_err from None
            except Exception:
                # On iOS 17..26 PersonalizedDMG can also be mounted directly over USB lockdown,
                # which is faster and avoids userspace-tunnel shim failures on Windows.
                if ios_major >= 27:
                    raise
                await inner.aclose()
                inner = AsyncExitStack()
                usb_lockdown = await _lockdown(serial)
                try:
                    with suppress(AlreadyMountedError):
                        await auto_mount(usb_lockdown)
                finally:
                    with suppress(Exception):
                        await usb_lockdown.close()
            else:
                # Crucial: RSD caches `peer_info` from its initial handshake, so after mounting DDI
                # we must close this tunnel (only one userspace tunnel per process is allowed) and
                # establish a fresh one so `com.apple.instruments.dtservicehub` appears in `peer_info`.
                await inner.aclose()
                inner = AsyncExitStack()
            rsd = await inner.enter_async_context(UserspaceRsdTunnel(serial=serial))
            dvt = await inner.enter_async_context(DvtProvider(rsd))
            loc = await inner.enter_async_context(LocationSimulation(dvt))
    except BaseException:
        await inner.aclose()
        raise
    stack.push_async_callback(inner.aclose)
    return loc


async def _open_location_channel(stack: AsyncExitStack, *, serial: str | None) -> tuple[Any, str]:
    """Open location simulation over the right transport for the device's iOS version."""
    udid, major, desc = await _precheck(serial)
    target = udid or serial

    if major >= 17:
        loc = await _open_rsd_dvt_channel(stack, serial=target, ios_major=major)
        return loc, desc

    provider = await _lockdown(target)
    stack.push_async_callback(provider.close)
    return _LegacyDtLocationChannel(provider), desc


async def _clear_with_ack(loc: Any) -> bool:
    """Send stopLocationSimulation; return True if the phone acknowledged it."""
    if isinstance(loc, _LegacyDtLocationChannel):
        await loc.clear()
        return True
    channel = getattr(getattr(loc, "service", None), "_channel", None)
    if channel is not None:
        try:
            await asyncio.wait_for(
                channel.invoke("stopLocationSimulation", expects_reply=True), ACK_TIMEOUT
            )
            return True
        except TimeoutError:
            # Some iOS builds never answer this selector; the one-way frame was still sent.
            return False
    await loc.clear()
    await asyncio.sleep(0.5)  # let the one-way DTX frame flush before the tunnel closes
    return False


async def _clear_uninterruptible(loc: Any) -> tuple[bool, bool]:
    """Ensure stopLocationSimulation finishes before the tunnel closes, even under cancellation.

    Returns (cleared, acknowledged).
    """
    task = asyncio.create_task(_clear_with_ack(loc))
    pending_cancel = False
    try:
        while not task.done():
            try:
                acked = await asyncio.shield(task)
                return True, bool(acked)
            except asyncio.CancelledError:
                pending_cancel = True
                # Give the in-flight clear task up to ACK_TIMEOUT to flush before closing tunnel.
                try:
                    acked = await asyncio.wait_for(asyncio.shield(task), timeout=ACK_TIMEOUT)
                    return True, bool(acked)
                except (TimeoutError, asyncio.CancelledError, Exception):
                    task.cancel()
                    with suppress(BaseException):
                        await task
                    return False, False
            except Exception:
                return False, False
        return True, bool(task.result())
    finally:
        if pending_cancel:
            with suppress(Exception):
                task.cancel()


# --------------------------------------------------------------------------------------------
# Holding a simulated location
# --------------------------------------------------------------------------------------------


def _install_stop_signals(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> list:
    restore: list = []
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
            restore.append(("loop", sig))
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                prev = signal.signal(sig, lambda *_a: loop.call_soon_threadsafe(stop.set))
                restore.append(("sig", sig, prev))
            except (ValueError, OSError):
                pass
    return restore


def _remove_stop_signals(loop: asyncio.AbstractEventLoop, restore: list) -> None:
    for item in restore:
        with suppress(Exception):
            if item[0] == "loop":
                loop.remove_signal_handler(item[1])
            else:
                signal.signal(item[1], item[2])


async def dvt_hold_location(
    latitude: float,
    longitude: float,
    *,
    label: str = "",
    serial: str | None = None,
    hold_seconds: float = 0.0,
    jitter_m: float = 0.0,
    on_event=None,
) -> dict[str, object]:
    """Apply a simulated location and keep it alive until stopped.

    ``hold_seconds`` <= 0 means "until Ctrl+C, SIGTERM or `awh restore`".
    Optional ``jitter_m`` adds realistic GPS drift (in meters) on each refresh.
    Always restores the real GPS on the way out.
    """
    emit = on_event or (lambda **_k: None)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    restore_sig = _install_stop_signals(loop, stop)
    pid = os.getpid()
    _clear_stop_file(only_pid=pid)
    deadline = loop.time() + hold_seconds if hold_seconds > 0 else None

    state: dict[str, object] = {
        "active": False,
        "engine": "dvt",
        "mode": "static",
        "pid": pid,
        "serial": serial,
        "label": label,
        "latitude": latitude,
        "longitude": longitude,
        "jitter_m": jitter_m,
        "started_at": int(time.time()),
    }
    applied_once = False
    reconnects = 0
    acked = False

    def should_stop() -> bool:
        if stop.is_set():
            return True
        if _stop_file_targets_pid(pid):
            stop.set()
            return True
        return deadline is not None and loop.time() >= deadline

    try:
        while not should_stop():
            try:
                async with AsyncExitStack() as stack:
                    loc, desc = await _open_location_channel(stack, serial=serial)
                    cur_lat, cur_lon = apply_gps_jitter(latitude, longitude, jitter_m)
                    await loc.set(cur_lat, cur_lon)
                    state.update(
                        active=True,
                        device=desc,
                        latitude=cur_lat,
                        longitude=cur_lon,
                        updated_at=int(time.time()),
                    )
                    _save_dvt_state(state)
                    if not applied_once:
                        emit(
                            event="location_applied",
                            engine="dvt",
                            device=desc,
                            label=label,
                            latitude=cur_lat,
                            longitude=cur_lon,
                            jitter_m=jitter_m,
                        )
                    else:
                        emit(event="reconnected", engine="dvt", attempt=reconnects)
                    applied_once = True
                    next_refresh = loop.time() + REFRESH_SECONDS
                    try:
                        while not should_stop():
                            with suppress(TimeoutError):
                                await asyncio.wait_for(stop.wait(), 0.25)
                            if loop.time() >= next_refresh and not should_stop():
                                cur_lat, cur_lon = apply_gps_jitter(latitude, longitude, jitter_m)
                                await loc.set(cur_lat, cur_lon)
                                state.update(
                                    latitude=cur_lat,
                                    longitude=cur_lon,
                                    updated_at=int(time.time()),
                                )
                                _save_dvt_state(state)
                                next_refresh = loop.time() + REFRESH_SECONDS
                    finally:
                        # Restore real GPS on this same live session before AsyncExitStack tears
                        # down the tunnel, including when cancelled by Ctrl+C / SIGTERM.
                        exc_type = sys.exc_info()[0]
                        interrupted = exc_type is not None and issubclass(
                            exc_type, (asyncio.CancelledError, KeyboardInterrupt)
                        )
                        if interrupted:
                            stop.set()
                        if should_stop():
                            cleared_ok, acked = await _clear_uninterruptible(loc)
                            if cleared_ok:
                                state["cleared"] = True
                                _record_last_clear(pid, acknowledged=acked)
                break
            except DvtSetupError:
                raise
            except (asyncio.CancelledError, KeyboardInterrupt):
                stop.set()
                raise
            except Exception as exc:
                if not applied_once:
                    # First connection failed: surface an actionable error immediately.
                    raise DvtSetupError(
                        f"{type(exc).__name__}: {exc}\n  提示: {_hint_for(exc)}"
                    ) from exc
                if reconnects >= 1000:
                    raise
                delay = RECONNECT_DELAYS[min(reconnects, len(RECONNECT_DELAYS) - 1)]
                reconnects += 1
                state.update(active=False)
                _save_dvt_state(state)
                emit(
                    event="reconnecting",
                    engine="dvt",
                    attempt=reconnects,
                    reason=type(exc).__name__,
                    retry_in=delay,
                )
                # Poll stop file during backoff so `awh restore` wakes immediately.
                wait_until = loop.time() + delay
                while not should_stop() and (rem := wait_until - loop.time()) > 0:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), min(0.25, rem))
    finally:
        _remove_stop_signals(loop, restore_sig)
        _clear_stop_file(only_pid=pid)
        _clear_dvt_state(only_pid=pid)

    state.update(active=False, reconnects=reconnects, acknowledged=acked)
    return state


async def dvt_play_gpx(
    points: list[RoutePoint],
    *,
    label: str = "",
    serial: str | None = None,
    speed: float = 1.0,
    loop_route: bool = False,
    jitter_m: float = 0.0,
    default_step_seconds: float = 1.0,
    on_event=None,
) -> dict[str, object]:
    """Replay a GPX route over DVT/DtSimulateLocation with reconnect & clean GPS restore."""
    if not points:
        raise ValueError("GPX 轨迹点列表不能为空")
    emit = on_event or (lambda **_k: None)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    restore_sig = _install_stop_signals(loop, stop)
    pid = os.getpid()
    _clear_stop_file(only_pid=pid)

    state: dict[str, object] = {
        "active": False,
        "engine": "dvt",
        "mode": "gpx",
        "pid": pid,
        "serial": serial,
        "label": label,
        "latitude": points[0].latitude,
        "longitude": points[0].longitude,
        "point_index": 0,
        "total_points": len(points),
        "speed": speed,
        "loop": loop_route,
        "started_at": int(time.time()),
    }
    applied_once = False
    reconnects = 0
    acked = False
    idx = 0
    sent_points = 0

    def should_stop() -> bool:
        if stop.is_set():
            return True
        if _stop_file_targets_pid(pid):
            stop.set()
            return True
        return False

    try:
        while not should_stop():
            try:
                async with AsyncExitStack() as stack:
                    loc, desc = await _open_location_channel(stack, serial=serial)
                    try:
                        while not should_stop():
                            pt = points[idx]
                            cur_lat, cur_lon = apply_gps_jitter(pt.latitude, pt.longitude, jitter_m)
                            await loc.set(cur_lat, cur_lon)
                            sent_points += 1
                            state.update(
                                active=True,
                                device=desc,
                                latitude=cur_lat,
                                longitude=cur_lon,
                                point_index=idx + 1,
                                updated_at=int(time.time()),
                            )
                            _save_dvt_state(state)
                            if not applied_once:
                                emit(
                                    event="location_applied",
                                    engine="dvt",
                                    mode="gpx",
                                    device=desc,
                                    label=label,
                                    latitude=cur_lat,
                                    longitude=cur_lon,
                                    total_points=len(points),
                                )
                                applied_once = True
                            elif idx == 0 and sent_points > 1:
                                emit(event="route_loop", engine="dvt", total_points=len(points))

                            idx += 1
                            if idx >= len(points):
                                if loop_route:
                                    idx = 0
                                else:
                                    stop.set()
                                    break

                            raw_wait = (
                                points[idx].delay_seconds
                                if points[idx].delay_seconds is not None
                                else default_step_seconds
                            )
                            wait_s = max(0.05, raw_wait / max(0.05, speed))
                            wait_until = loop.time() + wait_s
                            while not should_stop() and (rem := wait_until - loop.time()) > 0:
                                with suppress(TimeoutError):
                                    await asyncio.wait_for(stop.wait(), min(0.25, rem))
                    finally:
                        exc_type = sys.exc_info()[0]
                        interrupted = exc_type is not None and issubclass(
                            exc_type, (asyncio.CancelledError, KeyboardInterrupt)
                        )
                        if interrupted:
                            stop.set()
                        if should_stop():
                            cleared_ok, acked = await _clear_uninterruptible(loc)
                            if cleared_ok:
                                state["cleared"] = True
                                _record_last_clear(pid, acknowledged=acked)
                break
            except DvtSetupError:
                raise
            except (asyncio.CancelledError, KeyboardInterrupt):
                stop.set()
                raise
            except Exception as exc:
                if not applied_once:
                    raise DvtSetupError(
                        f"{type(exc).__name__}: {exc}\n  提示: {_hint_for(exc)}"
                    ) from exc
                if reconnects >= 1000:
                    raise
                delay = RECONNECT_DELAYS[min(reconnects, len(RECONNECT_DELAYS) - 1)]
                reconnects += 1
                state.update(active=False)
                _save_dvt_state(state)
                emit(
                    event="reconnecting",
                    engine="dvt",
                    attempt=reconnects,
                    reason=type(exc).__name__,
                    retry_in=delay,
                )
                wait_until = loop.time() + delay
                while not should_stop() and (rem := wait_until - loop.time()) > 0:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), min(0.25, rem))
    finally:
        _remove_stop_signals(loop, restore_sig)
        _clear_stop_file(only_pid=pid)
        _clear_dvt_state(only_pid=pid)

    state.update(
        active=False,
        reconnects=reconnects,
        points_sent=sent_points,
        acknowledged=acked,
    )
    return state


async def dvt_clear_location(*, serial: str | None = None) -> tuple[bool, str]:
    """Stop a live holder (confirming it restored GPS), else send stopLocationSimulation directly."""
    from .runner import is_pid_alive

    holder = active_dvt_holder()
    if holder:
        pid = int(holder.get("pid") or 0)
        serial = serial or (str(holder["serial"]) if holder.get("serial") else None)
        requested_at = time.time()
        _write_stop_signal(pid)
        exited = False
        for _ in range(60):  # up to ~12 s for a graceful stop + ack
            if not is_pid_alive(pid):
                exited = True
                break
            await asyncio.sleep(0.2)
        if not exited:
            _terminate_pid(pid)
        _clear_stop_file(only_pid=pid)
        _clear_dvt_state(only_pid=pid)

        # Only skip direct device clear when the holder explicitly confirmed it cleared GPS.
        last_clear = _read_last_clear()
        if (
            exited
            and last_clear
            and int(last_clear.get("pid") or 0) == pid
            and float(last_clear.get("cleared_at") or 0.0) >= requested_at - 1.0
        ):
            dvt_last_clear_file().unlink(missing_ok=True)
            suffix = "（获手机确认）" if last_clear.get("acknowledged") else ""
            return True, f"已停止后台定位保持进程 (PID {pid})，手机已恢复真实定位{suffix}。"

    try:
        async with AsyncExitStack() as stack:
            loc, desc = await _open_location_channel(stack, serial=serial)
            acked = await _clear_with_ack(loc)
        _clear_dvt_state()
        dvt_last_clear_file().unlink(missing_ok=True)
        suffix = "并获手机确认" if acked else "（该系统未回执，已发送）"
        return True, f"已向 {desc} 发送官方 stopLocationSimulation{suffix}，真实定位已恢复。"
    except Exception as exc:
        return False, (
            f"DVT 清理未完成 ({type(exc).__name__}: {exc})；{_hint_for(exc)}。"
            "兜底：重启 iPhone 一定会恢复真实定位。"
        )


def _terminate_pid(pid: int) -> None:
    from .runner import is_pid_alive

    with suppress(OSError):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        else:
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and is_pid_alive(pid):
                time.sleep(0.1)
            if is_pid_alive(pid):
                os.kill(pid, signal.SIGKILL)


# --------------------------------------------------------------------------------------------
# Detached background holder
# --------------------------------------------------------------------------------------------


def _open_holder_log() -> tuple[Any, Path]:
    """Open a fresh log file without following symlinks or failing on Windows file locks."""
    ensure_private_dir(data_dir())
    primary = dvt_log_file()
    candidates = [primary, data_dir() / f"dvt-{os.getpid()}-{int(time.time())}.log"]
    last_err: OSError | None = None
    for path in candidates:
        if path.is_symlink():
            continue
        try:
            return open(path, "wb"), path  # noqa: SIM115
        except OSError as err:
            last_err = err
    raise OSError(f"无法打开后台定位日志文件: {last_err}")


def spawn_detached_holder(argv: list[str]) -> tuple[subprocess.Popen, Path]:
    """Start ``python -m apple_watch_health <argv>`` fully detached; return (process, log_path)."""
    log, log_path = _open_holder_log()
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "cwd": str(Path.home()),
        "env": {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    }
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
        kwargs["creationflags"] = flags
        # pythonw would lose the console encoding; python.exe with CREATE_NO_WINDOW is fine.
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen([sys.executable, "-m", "apple_watch_health", *argv], **kwargs)
    finally:
        log.close()
    return proc, log_path


def wait_for_holder(
    proc: subprocess.Popen,
    timeout: float = 60.0,
    *,
    log_path: Path | None = None,
) -> tuple[bool, str]:
    """Wait until the detached holder reports the location applied (or exits)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = read_dvt_state()
        if st and int(st.get("pid") or 0) == proc.pid and st.get("active"):
            return True, str(st.get("device") or "iPhone")
        # poll() also reaps the child, so a crashed holder is detected immediately
        # (a bare PID probe would see the un-reaped zombie as "alive" on macOS/Linux).
        if proc.poll() is not None:
            return False, _tail_log(log_path=log_path)
        time.sleep(0.3)
    _terminate_pid(proc.pid)
    return False, "后台进程在超时内未完成连接（已终止）：\n" + _tail_log(log_path=log_path)


def _tail_log(lines: int = 15, *, log_path: Path | None = None) -> str:
    try:
        target = log_path or dvt_log_file()
        text = target.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(text[-lines:])
    except OSError:
        return "(无日志)"
