"""Unified cross-platform CLI (`awh`) for Apple Watch regional health feature testing."""

from __future__ import annotations

import argparse
import asyncio
import math
import shutil
import sys
from pathlib import Path

from . import __version__
from .catalog import render_feature_table
from .device import (
    check_windows_usbmux_ready,
    discover_usb_devices,
    platform_summary,
    stream_region_diagnostics,
)
from .identity_fetch import (
    NOTICE_URL,
    IdentityImportError,
    download_full_apk_concurrent,
    extract_identity_from_apk_bytes,
    fetch_identity_via_range,
    import_local_pair,
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
from .profile import PRESETS, format_nmea_coordinate
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
        print(f"[RUN] 检测到后台发送进程: PID={active['pid']} ({active.get('latitude')}, {active.get('longitude')})")
    else:
        print("[OK] 无残留后台发送进程")

    devices = asyncio.run(discover_usb_devices(include_network=False))
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
        if not dev.watches and dev.watch_probe_note:
            print(f"      Watch: {dev.watch_probe_note}")
        if dev.error:
            print(f"      详情: {dev.error}")
    return 0


def _cmd_devices(args: argparse.Namespace) -> int:
    devices = asyncio.run(discover_usb_devices(include_network=args.all))
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
    if not args.force and not args.cert and not args.apk:
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
            apk_bytes = Path(args.apk).expanduser().read_bytes()
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
    if args.lat is not None or args.lon is not None:
        if args.lat is None or args.lon is None:
            raise ValueError("自定义坐标必须同时提供 --lat 和 --lon")
        format_nmea_coordinate(args.lat, is_latitude=True)
        format_nmea_coordinate(args.lon, is_latitude=False)
        return args.lat, args.lon, args.altitude, f"Custom ({args.lat:.4f}, {args.lon:.4f})"
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
    if args.command == "send":
        if not math.isfinite(args.interval) or args.interval < 1.0:
            print("错误: --interval 不能小于 1 秒", file=sys.stderr)
            return 2
        try:
            lat, lon, alt, _label = _resolve_coords(args)
        except ValueError as err:
            print(f"错误: {err}", file=sys.stderr)
            return 2

    # On macOS, re-exec strictly the Python process inside Seatbelt (IP network blocked).
    maybe_reexec_in_macos_sandbox(raw_argv, enabled=not args.no_sandbox)

    config = RunConfig(
        command=args.command,
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
        result = asyncio.run(run_session(config))
    except KeyboardInterrupt:
        emit(event="interrupted")
        return 0
    except Exception as err:
        emit(event="error", type=type(err).__name__, detail=str(err))
        return 1

    if args.command == "probe":
        return 0 if (result.get("identified") and result.get("authenticated")) else 1
    return 0


def _cmd_monitor(args: argparse.Namespace) -> int:
    print(
        f"==> 实时监听 iPhone 地区与 NMEA 筛查诊断事件（已脱敏，最多 {args.duration:.0f}s，Ctrl+C 退出）...",
        flush=True,
    )

    async def _run() -> None:
        async for item in stream_region_diagnostics(serial=args.udid, duration=args.duration):
            print(f"[{item['level']}] {item['process']}: {item['message']}", flush=True)

    try:
        asyncio.run(_run())
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


def _cmd_clean(args: argparse.Namespace) -> int:
    stop_active_sender()
    removed: list[str] = []

    # Remove legacy bloated cache (e.g. old 32MB APK + nested uv + cloned repo).
    legacy_roots = {d.parent for d in legacy_identity_dirs()}
    for root in legacy_roots:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
            removed.append(str(root))

    # Remove Python bytecode caches inside project sources/tests (never touch .venv unless --all).
    for sub_dir in ("src", "tests"):
        base = project_root() / sub_dir
        if base.exists():
            for pycache in base.rglob("__pycache__"):
                shutil.rmtree(pycache, ignore_errors=True)
                removed.append(str(pycache))
    for cache_dir in (".pytest_cache", ".ruff_cache"):
        p = project_root() / cache_dir
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(str(p))

    if args.all:
        dd = data_dir()
        if dd.exists():
            shutil.rmtree(dd, ignore_errors=True)
            removed.append(str(dd))
        venv = project_root() / ".venv"
        if venv.exists():
            shutil.rmtree(venv, ignore_errors=True)
            removed.append(str(venv))
    else:
        # Preserve current identity, remove any leftover staging dirs.
        dd = data_dir()
        if dd.exists():
            for child in dd.iterdir():
                if child.name.startswith(".identity-") or child.suffix in (".apk", ".log", ".pid"):
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                    removed.append(str(child))

    if removed:
        print("已清理:")
        for item in removed:
            print(f"  - {item}")
    else:
        print("无需清理，环境已保持干净。")
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
        ("send", 1200.0, "启动有时限的 USB iAP2 坐标推送会话（默认香港中环，macOS 默认沙箱断网）"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--udid", help="多设备连接时指定目标 iPhone UDID")
        p.add_argument("--duration", type=float, default=default_dur, help=f"会话时长秒数 (1..3600，默认 {int(default_dur)})")
        p.add_argument("--cert", help="临时指定证书文件")
        p.add_argument("--key", help="临时指定私钥文件")
        p.add_argument("--auth-helper", help="自定义外部 JSONL 认证助手路径")
        p.add_argument("--no-sandbox", action="store_true", help="禁用 macOS sandbox-exec 断网沙箱")
        if name == "send":
            p.add_argument(
                "--preset",
                choices=sorted(PRESETS),
                default="hk",
                help="地区坐标预设（默认 hk 香港中环）",
            )
            p.add_argument("--lat", type=float, help="自定义纬度 (-90..90)")
            p.add_argument("--lon", type=float, help="自定义经度 (-180..180)")
            p.add_argument("--altitude", type=float, default=15.0, help="自定义海拔米数（默认 15.0）")
            p.add_argument("--interval", type=float, default=1.0, help="最小发送间隔秒数（>=1，默认 1）")

    p_mon = sub.add_parser("monitor", help="实时监听脱敏后的 iPhone 定位筛查与健康地区判定日志")
    p_mon.add_argument("--udid", help="指定目标 iPhone UDID")
    p_mon.add_argument("--duration", type=float, default=120.0, help="监听时长秒数（默认 120）")

    sub.add_parser("stop", help="安全停止运行中的后台坐标发送会话")

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
        "devices": _cmd_devices,
        "setup": _cmd_setup,
        "features": _cmd_features,
        "presets": _cmd_presets,
        "monitor": _cmd_monitor,
        "stop": _cmd_stop,
        "clean": _cmd_clean,
    }
    if args.command in ("probe", "send"):
        return _cmd_probe_or_send(args, raw)
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
