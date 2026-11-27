"""Unified cross-platform CLI (`awh`) for Apple Watch regional health feature testing."""

from __future__ import annotations

import argparse
import math
import shutil
import sys
import time
from pathlib import Path

from . import __version__, aio
from .catalog import render_feature_table
from .device import (
    check_windows_usbmux_ready,
    discover_usb_devices,
    platform_summary,
    stream_region_diagnostics,
)
from .dvt import (
    DvtSetupError,
    _hint_for,
    active_dvt_holder,
    dvt_clear_location,
    dvt_hold_location,
    dvt_play_gpx,
    read_dvt_state,
    spawn_detached_holder,
    wait_for_holder,
)
from .identity_fetch import (
    NOTICE_URL,
    IdentityImportError,
    download_full_apk_concurrent,
    extract_identity_from_apk_bytes,
    fetch_identity_via_range,
    import_local_pair,
    read_apk_file_bounded,
    write_identity_atomic,
)
from .mfi import ProviderError, inspect_identity_files
from .paths import (
    data_dir,
    identity_dir,
    legacy_identity_dirs,
    project_root,
    resolve_identity_files,
)
from .profile import PRESETS, convert_to_wgs84, load_gpx_route, resolve_place
from .runner import (
    RunConfig,
    emit,
    maybe_reexec_in_macos_sandbox,
    read_active_sender,
    run_session,
    stop_active_sender,
)


def _cmd_doctor(args: argparse.Namespace) -> int:
    print(f"apple-watch-health v{__version__} | 平台: {platform_summary()} | Python: {sys.version.split()[0]}")
    print(f"数据缓存目录: {data_dir()}")

    ok_mux, mux_msg = check_windows_usbmux_ready()
    print(f"[{'OK' if ok_mux else 'WARN'}] USB 服务: {mux_msg}")

    id_pair = resolve_identity_files()
    if id_pair:
        try:
            meta = inspect_identity_files(*id_pair)
            print(
                f"[OK] MFi 身份就绪: {meta['cert_path']} "
                f"(cert={meta['cert_bytes']}B, key={meta['key_bytes']}B, sha256={meta['cert_sha256']}...)"
            )
        except (ProviderError, OSError) as err:
            print(f"[FAIL] MFi 身份文件异常: {err}")
    else:
        print("[MISS] 未配置 MFi 身份文件，请运行: awh setup")

    active = read_active_sender()
    if active:
        print(f"[RUN] 检测到后台 iAP2 发送进程: PID={active['pid']} ({active.get('latitude')}, {active.get('longitude')})")
    else:
        print("[OK] 无残留后台 iAP2 发送进程")
    holder = active_dvt_holder()
    if holder:
        print(
            f"[RUN] DVT 定位保持中: PID={holder.get('pid')} {holder.get('label')} "
            f"({holder.get('latitude')}, {holder.get('longitude')})，结束请运行 awh restore"
        )

    if not ok_mux:
        print("[MISS] USB 服务不可用，跳过设备探测")
        return 1 if args.strict else 0
    try:
        devices = aio.run(discover_usb_devices(include_network=False))
    except Exception as err:
        print(f"[FAIL] 设备枚举失败: {type(err).__name__}: {err}")
        return 1
    if not devices:
        print("[MISS] 未检测到 USB 连接的 iPhone（请用数据线连接、解锁屏幕并点【信任】）")
        return 1 if args.strict else 0

    for dev in devices:
        tag = "OK" if dev.trusted else "LOCKED"
        compat = "支持 iOS 26/27 高血压通知" if dev.supports_hypertension_ios else "系统版本低于 iOS 26"
        print(
            f"[{tag}] iPhone: {dev.name} | {dev.model_name} ({dev.product_type}) | "
            f"iOS {dev.ios_version} ({dev.build_version}) | 区域 {dev.region_info} | "
            f"UDID {dev.masked_udid} | {compat}"
        )
        for watch in dev.watches:
            if watch.supports_hypertension is True:
                w_compat = "硬件+系统满足高血压通知 & 睡眠呼吸暂停"
            elif watch.supports_hypertension is False:
                w_compat = "不支持高血压通知（机型或 watchOS < 26）"
            else:
                w_compat = "未知机型，请人工核对 Apple 官方支持列表"
            print(
                f"      Watch: {watch.name} | {watch.model_name} ({watch.product_type}) | "
                f"watchOS {watch.watchos_version} ({watch.build_version}) | 区域 {watch.region_info} | {w_compat}"
            )
        if dev.developer_mode is not None:
            dm = "已开启（可用 DVT locate）" if dev.developer_mode else "未开启 → `awh devmode --reveal` 后去设置里打开"
            print(f"      开发者模式: {dm}")
        if not dev.watches and dev.watch_probe_note:
            print(f"      Watch: {dev.watch_probe_note}")
        if dev.error:
            print(f"      详情: {dev.error}")
    return 0


def _cmd_devices(args: argparse.Namespace) -> int:
    try:
        devices = aio.run(discover_usb_devices(include_network=args.all))
    except Exception as err:
        ok_mux, mux_msg = check_windows_usbmux_ready()
        print(f"设备枚举失败: {type(err).__name__}: {err}", file=sys.stderr)
        if not ok_mux:
            print(mux_msg, file=sys.stderr)
        return 1
    if not devices:
        print("未检测到已连接的 iPhone。请检查 USB 数据线、屏幕解锁状态及【信任此电脑】提示。", file=sys.stderr)
        return 1
    for dev in devices:
        print(
            f"{dev.connection_type:7s} | {dev.name} | {dev.model_name} ({dev.product_type}) | "
            f"iOS {dev.ios_version} ({dev.build_version}) | {dev.region_info} | "
            f"UDID={dev.udid if args.full_udid else dev.masked_udid}"
        )
        for watch in dev.watches:
            print(
                f"  └─ Watch | {watch.name} | {watch.model_name} ({watch.product_type}) | "
                f"watchOS {watch.watchos_version} ({watch.build_version}) | {watch.region_info}"
            )
        if not dev.watches and dev.watch_probe_note:
            print(f"  └─ Watch | {dev.watch_probe_note}")
        if dev.error:
            print(f"        状态异常: {dev.error}")
    return 0


def _cmd_setup(args: argparse.Namespace) -> int:
    if not (args.force or args.cert or args.key or args.apk or args.full_apk):
        existing = resolve_identity_files()
        if existing:
            try:
                meta = inspect_identity_files(*existing)
                print(f"MFi 身份文件已就绪且校验通过: {meta['cert_path']}")
                print("如需重新提取请加 --force。")
                return 0
            except (ProviderError, OSError):
                pass

    try:
        if args.cert or args.key:
            if not (args.cert and args.key):
                print("错误: 使用自定义身份时必须同时提供 --cert 和 --key", file=sys.stderr)
                return 2
            out = import_local_pair(Path(args.cert), Path(args.key))
            print(f"已校验并保存自定义 MFi 身份至: {out}")
            return 0

        if args.apk:
            print(f"==> 从本地 APK 提取并校验完整 SHA-256: {args.apk}")
            apk_bytes = read_apk_file_bounded(Path(args.apk))
            identity = extract_identity_from_apk_bytes(apk_bytes)
        elif args.full_apk:
            print("==> 全量多线程下载 DiPlay v0.2.7 APK 并校验完整 SHA-256 (31.3 MB)...")
            apk_bytes = download_full_apk_concurrent(
                on_progress=lambda done, total: print(
                    f"    进度: {done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB",
                    flush=True,
                )
            )
            identity = extract_identity_from_apk_bytes(apk_bytes)
        else:
            print("==> 极速分段提取 DiPlay v0.2.7 共享实验 MFi 身份 (~68 KB, 双文件 SHA-256 锁定)...")
            identity = fetch_identity_via_range()

        out = write_identity_atomic(identity, force=True)
        print(f"✓ 身份文件提取并校验成功: {out}")
        print(f"  来源声明: {NOTICE_URL}")
        print("  注意: 本地校验通过不代表 Apple 永久接受该共享凭证；仅限本人设备技术研究。")
        return 0
    except (IdentityImportError, ProviderError, OSError) as err:
        print(f"错误: {err}", file=sys.stderr)
        return 1


def _cmd_features(_args: argparse.Namespace) -> int:
    print(render_feature_table())
    return 0


def _cmd_presets(_args: argparse.Namespace) -> int:
    print("内置定位预设（使用 `awh send --preset <key>`）：")
    for p in PRESETS.values():
        print(f"  {p.key:7s} | {p.name:34s} | {p.latitude:>9.4f}, {p.longitude:>9.4f} | {p.note}")
    return 0


def _resolve_coords(args: argparse.Namespace) -> tuple[float, float, float, str]:
    coord_type = getattr(args, "coord_type", "wgs84") or "wgs84"
    if getattr(args, "place", None):
        lat, lon, alt, label = resolve_place(args.place)
        if coord_type != "wgs84":
            lat, lon = convert_to_wgs84(lat, lon, coord_type)
            label = f"{label} [{coord_type.upper()}→WGS84]"
        return lat, lon, alt, label
    if args.lat is not None or args.lon is not None:
        if args.lat is None or args.lon is None:
            raise ValueError("自定义坐标必须同时提供 --lat 和 --lon")
        lat, lon = convert_to_wgs84(args.lat, args.lon, coord_type)
        suffix = f" [{coord_type.upper()}→WGS84]" if coord_type != "wgs84" else ""
        return lat, lon, args.altitude, f"Custom ({lat:.4f}, {lon:.4f}){suffix}"
    preset = PRESETS[args.preset]
    return preset.latitude, preset.longitude, preset.altitude_m, preset.name


def _cmd_probe_or_send(args: argparse.Namespace, raw_argv: list[str]) -> int:
    if not math.isfinite(args.duration) or not 0 < args.duration <= 3600:
        print("错误: --duration 必须在 1..3600 秒之间", file=sys.stderr)
        return 2

    cert_override = Path(args.cert) if args.cert else None
    key_override = Path(args.key) if args.key else None
    try:
        pair = resolve_identity_files(cert_override, key_override)
    except ValueError as err:
        print(f"错误: {err}", file=sys.stderr)
        return 2
    if pair is None:
        print("错误: 未找到 MFi 身份文件，请先运行 `awh setup`", file=sys.stderr)
        return 1

    lat: float | None = None
    lon: float | None = None
    alt = 15.0
    effective_argv = list(raw_argv)
    is_send_cmd = args.command in ("send", "locate")
    if is_send_cmd:
        if not math.isfinite(args.interval) or args.interval < 1.0:
            print("错误: --interval 不能小于 1 秒", file=sys.stderr)
            return 2
        if not math.isfinite(args.altitude) or not -1000.0 <= args.altitude <= 50000.0:
            print("错误: --altitude 必须在 -1000..50000 米之间", file=sys.stderr)
            return 2
        try:
            lat, lon, alt, label = _resolve_coords(args)
        except ValueError as err:
            print(f"错误: {err}", file=sys.stderr)
            return 2
        # Resolve --place / --coord-type BEFORE entering macOS Seatbelt sandbox (no IP network).
        if getattr(args, "place", None) or getattr(args, "coord_type", "wgs84") != "wgs84":
            rebuilt = [
                "send",
                f"--lat={lat:.6f}",
                f"--lon={lon:.6f}",
                f"--altitude={alt:.1f}",
                f"--duration={args.duration}",
                f"--interval={args.interval}",
            ]
            if args.udid:
                rebuilt.append(f"--udid={args.udid}")
            if args.cert:
                rebuilt.append(f"--cert={args.cert}")
            if args.key:
                rebuilt.append(f"--key={args.key}")
            if args.auth_helper:
                rebuilt.append(f"--auth-helper={args.auth_helper}")
            if args.no_sandbox:
                rebuilt.append("--no-sandbox")
            effective_argv = rebuilt
            if getattr(args, "place", None):
                emit(event="place_resolved", query=args.place, label=label, latitude=lat, longitude=lon)

    # On macOS, re-exec strictly the Python process inside Seatbelt (IP network blocked).
    maybe_reexec_in_macos_sandbox(effective_argv, enabled=not args.no_sandbox)

    config = RunConfig(
        command="send" if is_send_cmd else "probe",
        cert_file=pair[0],
        key_file=pair[1],
        duration=args.duration,
        latitude=lat,
        longitude=lon,
        altitude_m=alt,
        interval=getattr(args, "interval", 1.0),
        serial=args.udid,
        auth_helper=args.auth_helper,
        sandbox=not args.no_sandbox,
    )
    try:
        # Default loop on purpose: the MFi helper uses asyncio subprocesses (Proactor on Windows).
        result = aio.run(run_session(config), selector=False)
    except KeyboardInterrupt:
        emit(event="interrupted")
        return 0
    except Exception as err:
        hint = _hint_for(err)
        emit(event="error", type=type(err).__name__, detail=str(err), hint=hint)
        print(f"提示: {hint}", file=sys.stderr)
        return 1

    if args.command == "probe":
        return 0 if (result.get("identified") and result.get("authenticated")) else 1
    return 0


def _cmd_monitor(args: argparse.Namespace) -> int:
    if not math.isfinite(args.duration) or not 0 < args.duration <= 86400:
        print("错误: --duration 必须在 1..86400 秒之间", file=sys.stderr)
        return 2
    print(
        f"==> 实时监听 iPhone 地区与 NMEA 筛查诊断事件（已脱敏，最多 {args.duration:.0f}s，Ctrl+C 退出）...",
        flush=True,
    )

    async def _run() -> None:
        async for item in stream_region_diagnostics(serial=args.udid, duration=args.duration):
            print(f"[{item['level']}] {item['process']}: {item['message']}", flush=True)

    try:
        aio.run(_run())
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as err:
        print(f"错误: 无法读取设备日志 ({type(err).__name__}: {err})", file=sys.stderr)
        return 1


def _cmd_stop(_args: argparse.Namespace) -> int:
    stopped, msg = stop_active_sender()
    print(msg)
    return 0 if stopped else 1


def _cmd_status(_args: argparse.Namespace) -> int:
    """Display active DVT/iAP2 simulation sessions and local MFi readiness without probing USB."""
    now = int(time.time())
    print(f"apple-watch-health v{__version__} | 平台: {platform_summary()} | 缓存目录: {data_dir()}")

    holder = active_dvt_holder()
    if holder:
        elapsed = max(0, now - int(holder.get("started_at") or now))
        mode = holder.get("mode", "static")
        extra = ""
        if mode == "gpx":
            extra = f" | 轨迹进度 {holder.get('point_index', 0)}/{holder.get('total_points', 0)}"
        elif holder.get("jitter_m"):
            extra = f" | 漂移 ±{holder.get('jitter_m')}m"
        print(
            f"[RUN] DVT 定位保持中: PID={holder.get('pid')} | {holder.get('device', 'iPhone')} | "
            f"{holder.get('label', '')} ({float(holder.get('latitude') or 0):.5f}, "
            f"{float(holder.get('longitude') or 0):.5f}) | 已运行 {elapsed}s{extra}"
        )
    else:
        print("[IDLE] DVT 定位保持: 未运行")

    sender = read_active_sender()
    if sender:
        elapsed = max(0, now - int(sender.get("started_at") or now))
        print(
            f"[RUN] iAP2 发送器运行中: PID={sender.get('pid')} | "
            f"({sender.get('latitude')}, {sender.get('longitude')}) | 已运行 {elapsed}s"
        )
    else:
        print("[IDLE] iAP2 发送器: 未运行")

    pair = resolve_identity_files()
    print(f"[{'OK' if pair else 'MISS'}] MFi 身份: {pair[0] if pair else '未配置（运行 awh setup 提取）'}")
    return 0


def _cmd_locate(args: argparse.Namespace, raw_argv: list[str]) -> int:
    """Dual-engine global locator: `dvt` (all apps incl. WeChat/Maps w/ Wi-Fi) or `iap2` (Health)."""
    if args.engine == "iap2":
        if args.gpx:
            print("错误: --gpx 轨迹回放仅支持默认 DVT 引擎（请去掉 --engine iap2）", file=sys.stderr)
            return 2
        return _cmd_probe_or_send(args, raw_argv)

    if not math.isfinite(args.jitter) or not 0.0 <= args.jitter <= 500.0:
        print("错误: --jitter 必须在 0..500 米之间", file=sys.stderr)
        return 2
    if not math.isfinite(args.speed) or not 0.05 <= args.speed <= 100.0:
        print("错误: --speed 必须在 0.05..100 倍之间", file=sys.stderr)
        return 2
    if not math.isfinite(args.hold) or args.hold < 0 or args.hold > 86400:
        print("错误: --hold 必须在 0..86400 秒之间（0 = 一直保持直到 Ctrl+C 或 awh restore）", file=sys.stderr)
        return 2

    gpx_points = None
    lat = 0.0
    lon = 0.0
    label = ""
    if args.gpx:
        try:
            gpx_path = Path(args.gpx).expanduser().resolve()
            gpx_points = load_gpx_route(gpx_path, coord_type=args.coord_type)
            label = args.label or f"GPX: {gpx_path.name} ({len(gpx_points)} 点)"
            lat, lon = gpx_points[0].latitude, gpx_points[0].longitude
        except (OSError, ValueError) as err:
            print(f"错误: {err}", file=sys.stderr)
            return 2
    else:
        try:
            lat, lon, _alt, label = _resolve_coords(args)
        except ValueError as err:
            print(f"错误: {err}", file=sys.stderr)
            return 2

    holder = active_dvt_holder()
    if holder and not args.holder_process:
        print(f"==> 检测到已有 DVT 定位保持进程 (PID {holder.get('pid')})，先停止它再切换新地点...")
        aio.run(dvt_clear_location(serial=args.udid))

    if args.detach and not args.holder_process:
        # Use `--flag=value` syntax so negative coordinates or leading-dash labels never trip argparse.
        child_argv = [
            "locate",
            "--engine=dvt",
            f"--hold={args.hold}",
            f"--jitter={args.jitter}",
            f"--label={label}",
            "--holder-process",
        ]
        if args.gpx:
            child_argv.extend(
                [
                    f"--gpx={Path(args.gpx).expanduser().resolve()}",
                    f"--coord-type={args.coord_type}",
                    f"--speed={args.speed}",
                ]
            )
            if args.loop:
                child_argv.append("--loop")
        else:
            child_argv.extend([f"--lat={lat:.7f}", f"--lon={lon:.7f}"])
        if args.udid:
            child_argv.append(f"--udid={args.udid}")
        proc, log_path = spawn_detached_holder(child_argv)
        print(f"==> 后台定位保持进程已启动 (PID {proc.pid})，正在连接 iPhone...", flush=True)
        ok, info = wait_for_holder(proc, log_path=log_path)
        if not ok:
            print(f"[FAIL] 后台 DVT 定位失败：\n{info}", file=sys.stderr)
            return 1
        print(
            f"[OK] 已将 {info} 全局定位至 {label} ({lat:.4f}, {lon:.4f})，后台持续保持中。\n"
            "     可关闭此窗口；保持 USB 连接。用 `awh status` 查看进度，`awh restore` 恢复真实 GPS。"
        )
        return 0

    if not args.holder_process:
        if gpx_points is not None:
            loop_txt = "（循环播放）" if args.loop else ""
            print(
                f"==> 正在通过官方 DVT 通道回放 GPX 轨迹 {label}，倍速 {args.speed}x{loop_txt}...",
                flush=True,
            )
        else:
            until = f"{args.hold:.0f} 秒后自动恢复" if args.hold > 0 else "按 Ctrl+C 或另开终端执行 awh restore 恢复"
            jitter_txt = f"，微幅漂移 ±{args.jitter:.0f}m" if args.jitter > 0 else ""
            print(
                f"==> 正在通过官方 DVT 通道定位到 {label} ({lat:.4f}, {lon:.4f}){jitter_txt}，{until}...",
                flush=True,
            )

    def _on_event(**ev: object) -> None:
        emit(**ev)
        if ev.get("event") == "location_applied" and not args.holder_process:
            print(
                f"[OK] {ev.get('device')} 已全局定位至 {label}！微信 / 苹果地图 / 高德等全部 App 生效"
                "（无需关 Wi-Fi/流量）。\n"
                "     注意：iOS 17+ 的模拟定位依赖本会话，请保持本窗口和 USB 连接；"
                "想关掉窗口请改用 --detach。",
                flush=True,
            )

    try:
        if gpx_points is not None:
            state = aio.run(
                dvt_play_gpx(
                    gpx_points,
                    label=args.label or label,
                    serial=args.udid,
                    speed=args.speed,
                    loop_route=args.loop,
                    jitter_m=args.jitter,
                    on_event=_on_event,
                )
            )
        else:
            state = aio.run(
                dvt_hold_location(
                    lat,
                    lon,
                    label=args.label or label,
                    serial=args.udid,
                    hold_seconds=args.hold,
                    jitter_m=args.jitter,
                    on_event=_on_event,
                )
            )
    except KeyboardInterrupt:
        emit(event="interrupted", engine="dvt")
        print("[OK] 已中断并恢复真实定位。")
        return 0
    except DvtSetupError as err:
        emit(event="error", engine="dvt", type="DvtSetupError", detail=str(err))
        print(f"[FAIL] {err}", file=sys.stderr)
        return 1
    except Exception as err:
        emit(event="error", engine="dvt", type=type(err).__name__, detail=str(err))
        if args.debug:
            raise
        print(f"[FAIL] DVT 定位中断: {type(err).__name__}: {err}（加 --debug 查看完整堆栈）", file=sys.stderr)
        return 1

    emit(event="finished", engine="dvt", cleared=bool(state.get("cleared")),
         acknowledged=bool(state.get("acknowledged")), reconnects=state.get("reconnects", 0))
    if not args.holder_process:
        print("[OK] DVT 会话已结束，已发送 stopLocationSimulation，手机恢复真实定位。")
    return 0


def _cmd_devmode(args: argparse.Namespace) -> int:
    """Show / reveal / enable iPhone Developer Mode (needed by the DVT engine on iOS 16+)."""
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.amfi import AmfiService

    async def _run() -> int:
        lockdown = await create_using_usbmux(serial=args.udid, connection_type="USB", autopair=True)
        amfi: AmfiService | None = None
        try:
            enabled = await lockdown.get_developer_mode_status()
            print(
                f"{lockdown.all_values.get('DeviceName', 'iPhone')} iOS {lockdown.product_version}："
                f"开发者模式{'已开启' if enabled else '未开启'}"
            )
            if enabled:
                return 0
            amfi = AmfiService(lockdown)
            if args.enable:
                print(
                    "==> 请求开启开发者模式，iPhone 将自动重启"
                    "（设置了锁屏密码的手机会被系统拒绝，请改用 --reveal）..."
                )
                await amfi.enable_developer_mode()
                print("[OK] 已开启开发者模式。")
                return 0
            if args.reveal:
                await amfi.reveal_developer_mode_option_in_ui()
                print("[OK] 已在 iPhone 显示开关：设置 → 隐私与安全性 → 开发者模式 → 打开 → 重启 → 解锁后点【打开】。")
                return 0
            print("提示: 加 --reveal 显示设置开关（推荐），或 --enable 直接开启（仅限未设锁屏密码的手机）。")
            return 1
        finally:
            # enable_developer_mode() may replace amfi._lockdown after reboot reconnect.
            clients = [lockdown]
            if amfi is not None and getattr(amfi, "_lockdown", None) not in (None, lockdown):
                clients.append(amfi._lockdown)
            for client in clients:
                try:
                    await client.close()
                except Exception:
                    pass

    try:
        return aio.run(_run())
    except Exception as err:
        print(f"错误: {type(err).__name__}: {err}\n  提示: {_hint_for(err)}", file=sys.stderr)
        return 1


def _cmd_restore(args: argparse.Namespace) -> int:
    """Stop any active iAP2 sender AND clear any DVT simulated location (zero side effects)."""
    stopped, msg = stop_active_sender()
    print(f"[{'OK' if stopped else 'INFO'}] iAP2 发送器: {msg}")

    dvt_prev = read_dvt_state()
    if dvt_prev or args.force_dvt:
        ok, dvt_msg = aio.run(dvt_clear_location(serial=args.udid))
        print(f"[{'OK' if ok else 'WARN'}] DVT 官方定位: {dvt_msg}")
    else:
        print("[INFO] DVT 官方定位: 未检测到本工具记录的 DVT 模拟（如需强制清除请加 --force-dvt）")

    print("[OK] 零系统副作用：不修改 Apple ID、系统时间、VPN 或描述文件。")
    print("提示: 如曾为开通健康功能开启飞行模式，现在关闭飞行模式并打开地图即可确认恢复真实定位。")
    return 0


def _cmd_clean(args: argparse.Namespace) -> int:
    stop_active_sender()
    if active_dvt_holder():
        try:
            aio.run(dvt_clear_location())
        except Exception as err:
            print(f"[WARN] 清理前停止 DVT 保持进程失败: {err}", file=sys.stderr)
    removed: list[str] = []
    skipped_locked: list[str] = []

    # Remove legacy bloated cache (e.g. old 32MB APK + nested uv + cloned repo).
    legacy_roots = {d.parent for d in legacy_identity_dirs()}
    for root in legacy_roots:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
            if not root.exists():
                removed.append(str(root))

    # Remove Python bytecode caches inside project sources/tests (never touch .venv unless --all).
    for sub_dir in ("src", "tests"):
        base = project_root() / sub_dir
        if base.exists():
            for pycache in base.rglob("__pycache__"):
                shutil.rmtree(pycache, ignore_errors=True)
                if not pycache.exists():
                    removed.append(str(pycache))
    for cache_dir in (".pytest_cache", ".ruff_cache"):
        p = project_root() / cache_dir
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            if not p.exists():
                removed.append(str(p))

    if args.all:
        dd = data_dir()
        if dd.exists():
            shutil.rmtree(dd, ignore_errors=True)
            (removed if not dd.exists() else skipped_locked).append(str(dd))
        venv = project_root() / ".venv"
        if venv.exists():
            shutil.rmtree(venv, ignore_errors=True)
            (removed if not venv.exists() else skipped_locked).append(str(venv))
    else:
        # Preserve current identity, remove any leftover staging dirs and runtime files.
        dd = data_dir()
        if dd.exists():
            for child in dd.iterdir():
                if child.name.startswith(".identity-") or child.suffix in (
                    ".apk",
                    ".log",
                    ".pid",
                    ".stop",
                    ".tmp",
                ) or child.name == "dvt_last_clear.json":
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                    if not child.exists():
                        removed.append(str(child))

    if removed:
        print("已清理:")
        for item in removed:
            print(f"  - {item}")
    else:
        print("无需清理，环境已保持干净。")
    if skipped_locked:
        print("提示: 以下目录因当前进程正在运行而无法立即删净（Windows 文件占用），退出后手动删除即可:")
        for item in skipped_locked:
            print(f"  - {item}")
    if not args.all:
        print(f"提示: 保留了当前 MFi 身份 ({identity_dir()})；如需彻底清空环境与身份请加 --all。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="awh",
        description=(
            "Apple Watch 地区健康功能本地 USB iAP2 工具链（开源免费 · 仅限本人设备技术研究）"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="一键检查系统环境、USB 驱动、MFi 身份与已连接 iPhone")
    p_doc.add_argument("--strict", action="store_true", help="未检测到 USB iPhone 时返回非零退出码")

    p_dev = sub.add_parser("devices", help="列出 USB/网络已连接的 iPhone 及兼容性")
    p_dev.add_argument("--all", action="store_true", help="同时包含 Wi-Fi 网络发现的设备")
    p_dev.add_argument("--full-udid", action="store_true", help="显示完整 UDID（默认脱敏）")

    p_setup = sub.add_parser("setup", help="极速提取并校验本地 MFi 身份文件（默认约 68 KB，1-2 秒）")
    p_setup.add_argument("--force", action="store_true", help="强制重新提取并覆盖已有身份文件")
    p_setup.add_argument("--full-apk", action="store_true", help="下载完整 31MB APK 并校验整包 SHA-256")
    p_setup.add_argument("--apk", help="从本地已下载的 DiPlay v0.2.7 APK 提取")
    p_setup.add_argument("--cert", help="导入自定义 DER PKCS#7 证书文件路径")
    p_setup.add_argument("--key", help="导入自定义 DER PKCS#8 P-256 私钥文件路径")

    sub.add_parser("features", help="查看 2026-11 watchOS 27 中国大陆 Apple Watch 功能可用性矩阵")
    sub.add_parser("presets", help="查看内置验证通过的地区坐标预设")

    for name, default_dur, help_text in (
        ("probe", 20.0, "仅执行 USB iAP2 识别与 MFi-v3 握手测试，不发送坐标"),
        ("send", 1200.0, "启动 USB iAP2 坐标推送（专用于开通 Apple Watch 健康功能，免开发者模式）"),
        ("locate", 1200.0, "将 iPhone 全局定位到任意地点（默认官方 DVT 引擎支持微信/地图全部 App；可切 --engine iap2）"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--udid", help="多设备连接时指定目标 iPhone UDID")
        p.add_argument("--duration", type=float, default=default_dur, help=f"iAP2 会话时长秒数 (1..3600，默认 {int(default_dur)})")
        p.add_argument("--cert", help="临时指定证书文件 (iap2)")
        p.add_argument("--key", help="临时指定私钥文件 (iap2)")
        p.add_argument("--auth-helper", help="自定义外部 JSONL 认证助手路径 (iap2)")
        p.add_argument("--no-sandbox", action="store_true", help="禁用 macOS sandbox-exec 断网沙箱 (iap2)")
        if name in ("send", "locate"):
            p.add_argument(
                "--place",
                help="任意地点名称或经纬度（如 '纽约'、'香港中环'、'东京'、'Apple Park'、'40.758,-73.9855'）",
            )
            p.add_argument(
                "--preset",
                choices=sorted(PRESETS),
                default="hk",
                help="内置地区坐标预设（默认 hk 香港中环）",
            )
            p.add_argument("--lat", type=float, help="自定义纬度 (-90..90)")
            p.add_argument("--lon", type=float, help="自定义经度 (-180..180)")
            p.add_argument(
                "--coord-type",
                choices=["wgs84", "gcj02", "bd09"],
                default="wgs84",
                help="输入坐标系：wgs84=标准GPS(默认)；gcj02=高德/腾讯/微信火星坐标自动纠偏；bd09=百度坐标自动纠偏",
            )
            p.add_argument("--altitude", type=float, default=15.0, help="自定义海拔米数（默认 15.0）")
            p.add_argument("--interval", type=float, default=1.0, help="iAP2 最小发送间隔秒数（>=1，默认 1）")
        if name == "locate":
            p.add_argument(
                "--engine",
                choices=["dvt", "iap2"],
                default="dvt",
                help="定位引擎：dvt=官方免Root全局改定位(微信/地图/Wi-Fi全生效，默认)；iap2=外置MFi配件(开健康功能用)",
            )
            p.add_argument(
                "--hold",
                type=float,
                default=0.0,
                help="DVT 保持秒数（默认 0=一直保持直到 Ctrl+C / awh restore；>0=到期自动恢复真实定位）",
            )
            p.add_argument(
                "--detach",
                action="store_true",
                help="DVT 在后台进程中保持定位/回放轨迹，命令立即返回，可关闭终端；用 awh restore 结束",
            )
            p.add_argument(
                "--jitter",
                type=float,
                default=0.0,
                help="自然 GPS 随机微幅漂移半径（米，0..500，默认 0=精确静止；建议 3..8 模拟真实户外信号）",
            )
            p.add_argument("--gpx", help="导入 GPX 轨迹文件按路线模拟移动（兼容爱思助手/标准 GPX 1.0/1.1）")
            p.add_argument("--speed", type=float, default=1.0, help="GPX 轨迹回放倍速（0.05..100，默认 1.0）")
            p.add_argument("--loop", action="store_true", help="循环回放 GPX 轨迹直到手动 restore/Ctrl+C")
            p.add_argument("--label", default="", help=argparse.SUPPRESS)
            p.add_argument("--holder-process", action="store_true", help=argparse.SUPPRESS)
            p.add_argument("--debug", action="store_true", help="失败时打印完整堆栈")

    sub.add_parser("status", help="快速查看当前 DVT 定位保持 / GPX 轨迹 / iAP2 发送器与 MFi 状态")
    p_dm = sub.add_parser("devmode", help="查看/显示/开启 iPhone 开发者模式（DVT 定位必需，Windows 首次使用必看）")
    p_dm.add_argument("--udid", help="指定目标 iPhone UDID")
    g = p_dm.add_mutually_exclusive_group()
    g.add_argument("--reveal", action="store_true", help="在 iPhone 设置中显示「开发者模式」开关（推荐）")
    g.add_argument("--enable", action="store_true", help="直接开启并自动重启（仅限未设置锁屏密码的手机）")

    p_mon = sub.add_parser("monitor", help="实时监听脱敏后的 iPhone 定位筛查与健康地区判定日志")
    p_mon.add_argument("--udid", help="指定目标 iPhone UDID")
    p_mon.add_argument("--duration", type=float, default=120.0, help="监听时长秒数（默认 120）")

    sub.add_parser("stop", help="安全停止运行中的后台 iAP2 坐标发送会话")
    p_res = sub.add_parser("restore", help="一键停止 iAP2 发送并清除 DVT 模拟定位，无副作用恢复真实 GPS")
    p_res.add_argument("--udid", help="指定目标 iPhone UDID")
    p_res.add_argument("--force-dvt", action="store_true", help="即使本地无记录也强制发送官方 DVT stopLocationSimulation")

    p_clean = sub.add_parser("clean", help="清理旧版冗余缓存与临时文件（加 --all 彻底清空环境与身份）")
    p_clean.add_argument("--all", action="store_true", help="同时删除本地 MFi 身份缓存与 .venv 虚拟环境")

    return parser


def _configure_stdio_utf8() -> None:
    """Force UTF-8 stdout/stderr on Windows (prevents cp1252/gbk UnicodeEncodeError)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio_utf8()
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw)

    dispatch = {
        "doctor": _cmd_doctor,
        "status": _cmd_status,
        "devices": _cmd_devices,
        "setup": _cmd_setup,
        "features": _cmd_features,
        "presets": _cmd_presets,
        "monitor": _cmd_monitor,
        "stop": _cmd_stop,
        "restore": _cmd_restore,
        "clean": _cmd_clean,
        "devmode": _cmd_devmode,
    }
    if args.command in ("probe", "send"):
        return _cmd_probe_or_send(args, raw)
    if args.command == "locate":
        return _cmd_locate(args, raw)
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
