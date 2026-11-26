"""Async pymobiledevice3 v9+ USB device discovery, compatibility check, and sanitized monitor."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import socket
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.companion import CompanionProxyService
from pymobiledevice3.services.os_trace import OsTraceService
from pymobiledevice3.usbmux import list_devices

# Friendly Apple Watch ProductType mapping (S8 through S12 / Ultra 1-4 / SE 2-3).
WATCH_MODELS: dict[str, tuple[str, bool, bool]] = {
    # product_type: (friendly_name, supports_hypertension, supports_sleep_apnea)
    "Watch6,10": ("Apple Watch SE (2nd gen)", False, False),
    "Watch6,11": ("Apple Watch SE (2nd gen)", False, False),
    "Watch6,12": ("Apple Watch SE (2nd gen)", False, False),
    "Watch6,13": ("Apple Watch SE (2nd gen)", False, False),
    "Watch6,14": ("Apple Watch Series 8", False, False),
    "Watch6,15": ("Apple Watch Series 8", False, False),
    "Watch6,16": ("Apple Watch Series 8", False, False),
    "Watch6,17": ("Apple Watch Series 8", False, False),
    "Watch6,18": ("Apple Watch Ultra", False, False),
    "Watch7,1": ("Apple Watch Series 9", True, True),
    "Watch7,2": ("Apple Watch Series 9", True, True),
    "Watch7,3": ("Apple Watch Series 9", True, True),
    "Watch7,4": ("Apple Watch Series 9", True, True),
    "Watch7,5": ("Apple Watch Ultra 2", True, True),
    "Watch7,6": ("Apple Watch SE 3", False, True),
    "Watch7,7": ("Apple Watch SE 3", False, True),
    "Watch7,8": ("Apple Watch Series 10", True, True),
    "Watch7,9": ("Apple Watch Series 10", True, True),
    "Watch7,10": ("Apple Watch Series 10", True, True),
    "Watch7,11": ("Apple Watch Series 10", True, True),
    "Watch7,12": ("Apple Watch Ultra 3", True, True),
    "Watch7,13": ("Apple Watch Series 11", True, True),
    "Watch7,14": ("Apple Watch Series 11", True, True),
    "Watch7,15": ("Apple Watch Series 11", True, True),
    "Watch7,16": ("Apple Watch Series 11", True, True),
    "Watch8,1": ("Apple Watch Series 12", True, True),
    "Watch8,2": ("Apple Watch Series 12", True, True),
    "Watch8,3": ("Apple Watch Series 12", True, True),
    "Watch8,4": ("Apple Watch Series 12", True, True),
    "Watch8,5": ("Apple Watch Ultra 4", True, True),
    "Watch8,6": ("Apple Watch Ultra 4", True, True),
}

# Friendly Apple iPhone ProductType mapping (covers iPhone 11 through 17/18 generations).
IPHONE_MODELS: dict[str, str] = {
    "iPhone12,1": "iPhone 11",
    "iPhone12,3": "iPhone 11 Pro",
    "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone12,8": "iPhone SE (2nd gen)",
    "iPhone13,1": "iPhone 12 mini",
    "iPhone13,2": "iPhone 12",
    "iPhone13,3": "iPhone 12 Pro",
    "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone14,2": "iPhone 13 Pro",
    "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13",
    "iPhone14,6": "iPhone SE (3rd gen)",
    "iPhone14,7": "iPhone 14",
    "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,1": "iPhone 16 Pro",
    "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone17,3": "iPhone 16",
    "iPhone17,4": "iPhone 16 Plus",
    "iPhone17,5": "iPhone 16e",
    "iPhone18,1": "iPhone 17 Pro",
    "iPhone18,2": "iPhone 17 Pro Max",
    "iPhone18,3": "iPhone 17",
    "iPhone18,4": "iPhone 17 Air",
}

# Only emit high-signal, non-PII diagnostic keywords during `awh monitor`.
MONITOR_PATTERNS = re.compile(
    r"(GPRMC\s+NMEA\s+Screening|GPGGA|CurrentCountryIsSupported|"
    r"Hypertension|HKCountry|countryCode|countryd|EffectiveCountry|"
    r"RegionAvailability|Enabled\s*=\s*[01]|isProducedByAccessory|isSimulatedBySoftware)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class WatchSummary:
    udid: str
    name: str
    product_type: str
    model_name: str
    watchos_version: str
    build_version: str
    region_info: str
    supports_hypertension_hw: bool | None
    supports_sleep_apnea_hw: bool | None

    @property
    def watchos_major(self) -> int:
        try:
            return int(self.watchos_version.split(".")[0])
        except (ValueError, IndexError):
            return 0

    @property
    def supports_hypertension(self) -> bool | None:
        if self.supports_hypertension_hw is False:
            return False
        if self.watchos_major and self.watchos_major < 26:
            return False
        return self.supports_hypertension_hw


@dataclass(frozen=True, slots=True)
class DeviceSummary:
    udid: str
    connection_type: str
    name: str
    product_type: str
    model_name: str
    ios_version: str
    build_version: str
    region_info: str
    timezone: str
    trusted: bool
    watches: tuple[WatchSummary, ...] = ()
    watch_probe_note: str | None = None
    error: str | None = None

    @property
    def ios_major(self) -> int:
        try:
            return int(self.ios_version.split(".")[0])
        except (ValueError, IndexError):
            return 0

    @property
    def supports_hypertension_ios(self) -> bool:
        return self.ios_major >= 26

    @property
    def masked_udid(self) -> str:
        if len(self.udid) <= 10:
            return self.udid
        return f"{self.udid[:6]}...{self.udid[-4:]}"


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def friendly_model(product_type: str) -> str:
    return IPHONE_MODELS.get(product_type, product_type or "Unknown")


def classify_watch_model(product_type: str) -> tuple[str, bool | None, bool | None]:
    """Return (friendly_name, supports_hypertension_hw, supports_sleep_apnea_hw)."""
    if product_type in WATCH_MODELS:
        return WATCH_MODELS[product_type]
    if product_type.startswith("Watch"):
        try:
            major = int(product_type[5:].split(",")[0])
            if major >= 8:
                return (f"Apple Watch ({product_type})", True, True)
            if major <= 6:
                return (f"Apple Watch ({product_type})", False, False)
        except ValueError:
            pass
    return (product_type or "Unknown", None, None)


def _extract_cp_val(raw: Any, key: str) -> str:
    if isinstance(raw, dict):
        return str(raw.get(key, "Unknown"))
    return str(raw) if raw is not None else "Unknown"


async def _probe_paired_watches(lockdown_client: Any) -> tuple[tuple[WatchSummary, ...], str | None]:
    """Query paired Apple Watch model & watchOS version via CompanionProxyService."""
    cp = None
    try:
        cp = CompanionProxyService(lockdown_client)
        if hasattr(cp, "connect"):
            await _maybe_await(cp.connect())
        udids = await asyncio.wait_for(_maybe_await(cp.list()), timeout=4.0)
        if not udids:
            return (), "未探测到在线配对的 Apple Watch（若手表已关机/蓝牙断开，请手动告知型号与 watchOS 版本）"
        watches: list[WatchSummary] = []
        offline_count = 0
        for w_udid in udids:
            vals: dict[str, str] = {}
            reachable = True
            for idx, key in enumerate(
                ("ProductType", "ProductVersion", "DeviceName", "BuildVersion", "RegionInfo")
            ):
                try:
                    raw = await asyncio.wait_for(_maybe_await(cp.get_value(w_udid, key)), timeout=3.0)
                    vals[key] = _extract_cp_val(raw, key)
                except Exception:
                    if idx == 0:
                        reachable = False
                        offline_count += 1
                        break
                    vals[key] = "Unknown"
            if not reachable:
                continue
            ptype = vals.get("ProductType", "Unknown")
            w_name, hyp_hw, apnea_hw = classify_watch_model(ptype)
            watches.append(
                WatchSummary(
                    udid=w_udid,
                    name=vals.get("DeviceName", "Unknown"),
                    product_type=ptype,
                    model_name=w_name,
                    watchos_version=vals.get("ProductVersion", "Unknown"),
                    build_version=vals.get("BuildVersion", "Unknown"),
                    region_info=vals.get("RegionInfo", "Unknown"),
                    supports_hypertension_hw=hyp_hw,
                    supports_sleep_apnea_hw=apnea_hw,
                )
            )
        if not watches and offline_count:
            return (), "已检测到配对 Apple Watch 但当前离线（关机或蓝牙断开），请手动告知手表型号与 watchOS 版本"
        return tuple(watches), None
    except Exception as exc:
        return (), f"未能自动探测手表（{type(exc).__name__}，可能手表已关机或蓝牙断开，请手动告知型号与系统版本）"
    finally:
        if cp is not None and hasattr(cp, "close"):
            try:
                await _maybe_await(cp.close())
            except Exception:
                pass


def check_windows_usbmux_ready() -> tuple[bool, str]:
    """Check whether Windows Apple Mobile Device Service (usbmuxd on port 27015) is reachable."""
    if os.name != "nt":
        return True, "macOS/Linux native usbmuxd"
    try:
        with socket.create_connection(("127.0.0.1", 27015), timeout=1.5):
            return True, "Apple Mobile Device Service (127.0.0.1:27015) 运行中"
    except OSError:
        return (
            False,
            "未检测到 Apple USB 驱动服务 (127.0.0.1:27015)。请安装 Microsoft Store 版「Apple 设备」："
            "winget install --id 9NP83LWLPZ9K",
        )


async def discover_usb_devices(*, include_network: bool = False) -> list[DeviceSummary]:
    """Discover connected iOS devices using async pymobiledevice3 v9+ APIs."""
    mux_devices = await _maybe_await(list_devices())
    seen_usb: set[str] = set()
    targets = []
    for dev in mux_devices:
        conn = getattr(dev, "connection_type", "")
        serial = getattr(dev, "serial", "")
        if conn == "USB":
            seen_usb.add(serial)
            targets.append((serial, conn))
        elif include_network and serial not in seen_usb:
            targets.append((serial, conn))

    summaries: list[DeviceSummary] = []
    for serial, conn in targets:
        client = None
        try:
            client = await _maybe_await(
                create_using_usbmux(serial=serial, connection_type=conn, autopair=False)
            )
            vals = client.all_values or {}
            product_type = str(vals.get("ProductType", "Unknown"))
            watches, watch_note = await _probe_paired_watches(client)
            summaries.append(
                DeviceSummary(
                    udid=serial,
                    connection_type=conn,
                    name=str(vals.get("DeviceName", "Unknown")),
                    product_type=product_type,
                    model_name=friendly_model(product_type),
                    ios_version=str(vals.get("ProductVersion", "Unknown")),
                    build_version=str(vals.get("BuildVersion", "Unknown")),
                    region_info=str(vals.get("RegionInfo", "Unknown")),
                    timezone=str(vals.get("TimeZone", "Unknown")),
                    trusted=bool(vals.get("ProductVersion")),
                    watches=watches,
                    watch_probe_note=watch_note,
                )
            )
        except Exception as exc:
            summaries.append(
                DeviceSummary(
                    udid=serial,
                    connection_type=conn,
                    name="(未信任或已锁屏)",
                    product_type="Unknown",
                    model_name="Unknown",
                    ios_version="Unknown",
                    build_version="Unknown",
                    region_info="Unknown",
                    timezone="Unknown",
                    trusted=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            if client is not None:
                try:
                    await _maybe_await(client.close())
                except Exception:
                    pass
    return summaries


def sanitize_log_message(text: str) -> str:
    """Strip coordinates, emails, and long hex identifiers before displaying diagnostic lines."""
    text = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{16}", "<UDID>", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "<EMAIL>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:260]


async def stream_region_diagnostics(
    *,
    serial: str | None = None,
    duration: float = 120.0,
) -> AsyncIterator[dict[str, str]]:
    """Stream filtered, sanitized locationd/healthd/countryd events over USB."""
    client = await _maybe_await(
        create_using_usbmux(serial=serial, connection_type="USB", autopair=False)
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    try:
        trace = OsTraceService(lockdown=client)
        stream = trace.syslog()
        while (remaining := deadline - loop.time()) > 0:
            try:
                entry = await asyncio.wait_for(stream.__anext__(), timeout=min(1.0, remaining))
            except TimeoutError:
                continue
            except StopAsyncIteration:
                break
            image = str(getattr(entry, "image_name", "") or "")
            message = str(getattr(entry, "message", "") or "")
            if "shouldNotDoWork" in message:
                continue
            if not MONITOR_PATTERNS.search(message):
                continue
            yield {
                "process": os.path.basename(image) or "unknown",
                "level": str(getattr(getattr(entry, "level", None), "name", "INFO")),
                "message": sanitize_log_message(message),
            }
    finally:
        try:
            await _maybe_await(client.close())
        except Exception:
            pass


def platform_summary() -> str:
    if sys.platform == "darwin":
        return "macOS"
    if os.name == "nt":
        return "Windows"
    return sys.platform
