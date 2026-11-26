"""No-root cross-platform (macOS & Windows) DVT LocationSimulation engine (iOS 17.4–27.x).

Uses Apple's official Developer LocationSimulation channel over pymobiledevice3's
in-process UserspaceRsdTunnel (no sudo/Admin required):
- Overrides CoreLocation globally for ALL apps (Apple Maps, WeChat, Gaode, etc.) even with Wi-Fi & 5G ON.
- Sends acknowledged `stopLocationSimulation` (`expects_reply=True`) on restore for guaranteed
  zero-side-effect recovery to real GPS.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import AsyncExitStack
from pathlib import Path

from pymobiledevice3.exceptions import AlreadyMountedError
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.services.mobile_image_mounter import auto_mount

from .paths import data_dir, ensure_private_dir


def dvt_state_file() -> Path:
    return data_dir() / "dvt_state.json"


def _save_dvt_state(state: dict[str, object]) -> None:
    sf = dvt_state_file()
    ensure_private_dir(sf.parent)
    sf.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def read_dvt_state() -> dict[str, object] | None:
    sf = dvt_state_file()
    if not sf.is_file():
        return None
    try:
        return json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _clear_dvt_state() -> None:
    dvt_state_file().unlink(missing_ok=True)


async def _open_location_channel(
    stack: AsyncExitStack,
    *,
    serial: str | None = None,
) -> tuple[LocationSimulation, str]:
    """Open no-root UserspaceRsdTunnel + auto-mount DDI if needed + LocationSimulation."""
    rsd = await stack.enter_async_context(UserspaceRsdTunnel(serial=serial))
    device_desc = f"{rsd.product_type} iOS {rsd.product_version}"
    try:
        dvt = await stack.enter_async_context(DvtProvider(rsd))
    except Exception as first_err:
        try:
            await auto_mount(rsd)
        except AlreadyMountedError:
            raise first_err from None
        dvt = await stack.enter_async_context(DvtProvider(rsd))
    loc = await stack.enter_async_context(LocationSimulation(dvt))
    return loc, device_desc


async def _clear_with_ack(loc: LocationSimulation) -> None:
    """Invoke stopLocationSimulation and wait for the iPhone DTX reply to confirm landing."""
    channel = getattr(getattr(loc, "service", None), "_channel", None)
    if channel is None:
        await loc.clear()
        await asyncio.sleep(1.0)
    else:
        await asyncio.wait_for(channel.invoke("stopLocationSimulation", expects_reply=True), 5.0)


async def dvt_set_location(
    latitude: float,
    longitude: float,
    *,
    label: str = "",
    serial: str | None = None,
    hold_seconds: float = 0.0,
) -> dict[str, object]:
    """Set global iPhone GPS via DVT LocationSimulation (works with Wi-Fi/5G & all apps)."""
    async with AsyncExitStack() as stack:
        loc, device_desc = await _open_location_channel(stack, serial=serial)
        await loc.set(latitude, longitude)
        # Brief pause so the one-way DTX frame flushes reliably before closing tunnel.
        await asyncio.sleep(0.5)
        state: dict[str, object] = {
            "active": True,
            "engine": "dvt",
            "device": device_desc,
            "serial": serial,
            "label": label,
            "latitude": latitude,
            "longitude": longitude,
            "updated_at": int(time.time()),
        }
        _save_dvt_state(state)

        if hold_seconds > 0:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + hold_seconds
            try:
                while loop.time() < deadline:
                    await asyncio.sleep(min(2.0, max(0.1, deadline - loop.time())))
                    await loc.set(latitude, longitude)
            finally:
                await _clear_with_ack(loc)
                _clear_dvt_state()
                state["active"] = False
                state["cleared"] = True
        return state


async def dvt_clear_location(*, serial: str | None = None) -> tuple[bool, str]:
    """Send acknowledged stopLocationSimulation over UserspaceRsdTunnel to restore real GPS."""
    prev = read_dvt_state()
    target_serial = serial or (str(prev["serial"]) if prev and prev.get("serial") else None)
    try:
        async with AsyncExitStack() as stack:
            loc, device_desc = await _open_location_channel(stack, serial=target_serial)
            await _clear_with_ack(loc)
        _clear_dvt_state()
        return True, f"已向 {device_desc} 发送官方 stopLocationSimulation 并获手机确认，真实定位已立即恢复。"
    except Exception as exc:
        return False, f"DVT 清理未完成 ({type(exc).__name__}: {exc})；请确认手机已解锁并连接 USB 后重试，或重启手机即可恢复。"
