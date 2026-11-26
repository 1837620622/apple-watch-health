"""Core async USB iAP2 probe/send runner with Seatbelt sandbox, PID lock, and safe teardown."""

from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pymobiledevice3.lockdown import create_using_usbmux

from .auth import AuthenticationProvider
from .link import Link
from .paths import ensure_private_dir, pid_file, sandbox_profile_path
from .profile import build_identity_message, build_location_message, build_location_sentences
from .wire import decode_parameters

SANDBOX_ENV_FLAG = "AWH_SANDBOXED"


@dataclass(frozen=True, slots=True)
class RunConfig:
    command: str  # "probe" or "send"
    cert_file: Path
    key_file: Path
    duration: float
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float = 15.0
    interval: float = 1.0
    serial: str | None = None
    auth_helper: str | None = None
    sandbox: bool = True


def emit(**event: object) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_active_sender() -> dict[str, object] | None:
    pf = pid_file()
    if not pf.is_file():
        return None
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0))
    except (OSError, ValueError, TypeError):
        pf.unlink(missing_ok=True)
        return None
    if is_pid_alive(pid):
        return data
    pf.unlink(missing_ok=True)
    return None


def stop_active_sender(wait_seconds: float = 4.0) -> tuple[bool, str]:
    info = read_active_sender()
    if info is None:
        return False, "当前没有运行中的定位发送进程。"
    pid = int(info["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        pid_file().unlink(missing_ok=True)
        return False, f"停止进程 {pid} 失败: {exc}"

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            pid_file().unlink(missing_ok=True)
            return True, f"已停止定位发送进程 (PID {pid})。"
        time.sleep(0.15)
    return False, f"已发送停止信号给 PID {pid}，但进程仍在退出中，请稍后复核。"


def _acquire_pid_lock(config: RunConfig) -> Path:
    existing = read_active_sender()
    if existing is not None and int(existing["pid"]) != os.getpid():
        raise RuntimeError(
            f"已有另一个发送会话在运行 (PID {existing['pid']})，请先执行 `awh stop` 停止它。"
        )
    pf = pid_file()
    ensure_private_dir(pf.parent)
    meta = {
        "pid": os.getpid(),
        "command": config.command,
        "latitude": config.latitude,
        "longitude": config.longitude,
        "started_at": int(time.time()),
    }
    pf.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return pf


def _release_pid_lock(pf: Path | None) -> None:
    if pf is None or not pf.is_file():
        return
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
        if int(data.get("pid", 0)) == os.getpid():
            pf.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass


def maybe_reexec_in_macos_sandbox(argv: list[str], *, enabled: bool) -> None:
    """Re-exec current Python process inside macOS Seatbelt blocking all IP networking."""
    if not enabled or sys.platform != "darwin" or os.environ.get(SANDBOX_ENV_FLAG) == "1":
        return
    sandbox_exec = "/usr/bin/sandbox-exec"
    profile = sandbox_profile_path()
    if not (os.path.exists(sandbox_exec) and profile.is_file()):
        return
    env = dict(os.environ)
    env[SANDBOX_ENV_FLAG] = "1"
    os.execve(
        sandbox_exec,
        [sandbox_exec, "-f", str(profile), sys.executable, "-m", "apple_watch_health", *argv],
        env,
    )


async def run_session(config: RunConfig) -> dict[str, object]:
    """Execute USB iAP2 probe or location send session with nested cleanup and SIGTERM support."""
    phone = None
    link: Link | None = None
    lock_path: Path | None = None

    helper_env = dict(os.environ)
    helper_env["AWH_MFI_CERT_FILE"] = str(config.cert_file)
    helper_env["AWH_MFI_KEY_FILE"] = str(config.key_file)
    helper_env["ACCESSORY_MFI_CERT_FILE"] = str(config.cert_file)
    helper_env["ACCESSORY_MFI_KEY_FILE"] = str(config.key_file)
    auth = AuthenticationProvider(config.auth_helper, env=helper_env)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[int] = []

    def _request_stop() -> None:
        stop_event.set()

    if os.name != "nt":
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop)
                installed_signals.append(sig)
            except (NotImplementedError, RuntimeError):
                pass
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_a: loop.call_soon_threadsafe(_request_stop))
            except (ValueError, OSError):
                pass

    if config.command == "send":
        lock_path = _acquire_pid_lock(config)

    deadline = loop.time() + config.duration
    identified = False
    authenticated = False
    sent_count = 0
    reconnect_count = 0

    try:
        while not stop_event.is_set() and loop.time() < deadline:
            phone = None
            link = None
            will_reconnect = False
            try:
                phone = await create_using_usbmux(
                    serial=config.serial,
                    connection_type="USB",
                    autopair=False,
                )
                emit(
                    event="device_connected",
                    transport="usb",
                    sandboxed=(os.environ.get(SANDBOX_ENV_FLAG) == "1"),
                    reconnect=reconnect_count,
                )
                stream = await phone.start_lockdown_service("com.apple.carkit.service")
                link = Link(stream, emit)
                await link.handshake()

                active_sentences: set[int] = set()
                last_sent: dict[int, float] = {}
                peer_intervals: dict[int, float] = {}
                identified = False
                authenticated = False

                while not stop_event.is_set() and (remaining := deadline - loop.time()) > 0:
                    try:
                        message = await link.receive(min(0.25, remaining))
                    except TimeoutError:
                        message = None

                    if message is not None:
                        fields = decode_parameters(message.body)
                        emit(
                            event="message",
                            identifier=f"0x{message.identifier:04x}",
                            parameters=[{"id": i, "bytes": len(b)} for i, b in fields],
                        )
                        if message.identifier == 0x1D00:
                            await link.send(build_identity_message())
                        elif message.identifier == 0x1D02:
                            identified = True
                            emit(event="identification_accepted")
                        elif message.identifier == 0x1D03:
                            raise RuntimeError(
                                f"iPhone rejected accessory identification: {[i for i, _ in fields]}"
                            )
                        elif message.identifier == 0xAA00:
                            await link.send(await auth.certificate())
                        elif message.identifier == 0xAA02:
                            challenges = [b for i, b in fields if i == 0]
                            if len(challenges) != 1:
                                raise RuntimeError("Invalid MFi authentication challenge parameters")
                            await link.send(await auth.sign(challenges[0]))
                        elif message.identifier == 0xAA05:
                            authenticated = True
                            emit(event="authentication_succeeded")
                        elif message.identifier == 0xAA04:
                            raise RuntimeError(
                                "MFi 认证被设备拒绝 (0xAA04)：证书可能已被吊销或当前系统版本策略变更"
                            )
                        elif message.identifier == 0xFFFA:
                            active_sentences = {i for i, b in fields if i in (1, 2) and not b}
                            peer_intervals = {
                                i - 0x8000: max(1.0, int.from_bytes(b, "big") / 1000.0)
                                for i, b in fields
                                if i in (0x8001, 0x8002) and len(b) == 4
                            }
                            last_sent.clear()
                            emit(event="location_requested", sentences=sorted(active_sentences))
                        elif message.identifier == 0xFFFC:
                            active_sentences.clear()
                            emit(event="location_stopped")

                    if (
                        config.command == "send"
                        and identified
                        and authenticated
                        and active_sentences
                        and config.latitude is not None
                        and config.longitude is not None
                    ):
                        now = loop.time()
                        due = sorted(
                            sid
                            for sid in active_sentences
                            if now - last_sent.get(sid, -math.inf)
                            >= max(config.interval, peer_intervals.get(sid, 1.0))
                        )
                        if due:
                            sentences = build_location_sentences(
                                config.latitude,
                                config.longitude,
                                altitude_m=config.altitude_m,
                            )
                            await link.send(build_location_message([sentences[sid] for sid in due]))
                            for sid in due:
                                last_sent[sid] = now
                            sent_count += 1
                            if sent_count == 1 or sent_count % 10 == 0:
                                emit(event="location_link_acknowledged", count=sent_count)
                break
            except (ConnectionError, EOFError, OSError) as exc:
                if config.command != "send" or stop_event.is_set() or loop.time() >= deadline:
                    raise
                will_reconnect = True
                reconnect_count += 1
                emit(
                    event="reconnecting",
                    attempt=reconnect_count,
                    reason=type(exc).__name__,
                )
            except Exception as exc:
                # pymobiledevice3 raises ConnectionTerminatedError / MuxException on transient USB/carkit resets
                if (
                    config.command == "send"
                    and not stop_event.is_set()
                    and loop.time() < deadline
                    and type(exc).__name__
                    in (
                        "ConnectionTerminatedError",
                        "MuxException",
                        "NoDeviceConnectedError",
                        "PyMobileDevice3Exception",
                    )
                ):
                    will_reconnect = True
                    reconnect_count += 1
                    emit(
                        event="reconnecting",
                        attempt=reconnect_count,
                        reason=type(exc).__name__,
                    )
                else:
                    raise
            finally:
                try:
                    if link is not None:
                        try:
                            await link.close()
                        except Exception:
                            if not will_reconnect:
                                raise
                finally:
                    if phone is not None:
                        try:
                            await phone.close()
                        except Exception:
                            if not will_reconnect:
                                raise

            if not stop_event.is_set() and loop.time() < deadline:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=min(2.0, max(0.1, deadline - loop.time()))
                    )
                except TimeoutError:
                    pass

        summary: dict[str, object] = {
            "event": "finished",
            "interrupted": stop_event.is_set(),
            "identified": identified,
            "authenticated": authenticated,
            "reconnects": reconnect_count,
            "location_packets_acknowledged": sent_count,
            "phone_location_verified": False,
        }
        emit(**summary)
        return summary
    finally:
        for sig in installed_signals:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError):
                pass
        try:
            await auth.close()
        finally:
            _release_pid_lock(lock_path)
