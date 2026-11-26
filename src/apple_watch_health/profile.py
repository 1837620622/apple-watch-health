"""iAP2 accessory identity profile, verified region presets, and NMEA 0183 generator."""

from __future__ import annotations

import json
import math
import re
import struct
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from .wire import Message, encode_parameter, encode_text_parameter


@dataclass(frozen=True, slots=True)
class LocationPreset:
    key: str
    name: str
    region: str
    latitude: float
    longitude: float
    altitude_m: float = 15.0
    note: str = ""


# Coordinates verified against Apple watchOS 27 Feature Availability (2026-11).
PRESETS: dict[str, LocationPreset] = {
    "hk": LocationPreset(
        key="hk",
        name="香港 · 中环 (Hong Kong Central)",
        region="HK",
        latitude=22.2819,
        longitude=114.1589,
        altitude_m=18.0,
        note="推荐默认；iOS 27.2.1 + Apple Watch S12 实测可用，支持高血压通知与全部健康功能",
    ),
    "hk-tst": LocationPreset(
        key="hk-tst",
        name="香港 · 尖沙咀 (Hong Kong TST)",
        region="HK",
        latitude=22.2969,
        longitude=114.1722,
        altitude_m=12.0,
        note="香港备用坐标",
    ),
    "mo": LocationPreset(
        key="mo",
        name="中国澳门 (Macao)",
        region="MO",
        latitude=22.1987,
        longitude=113.5439,
        altitude_m=16.0,
        note="支持高血压通知与睡眠呼吸暂停",
    ),
    "us-sf": LocationPreset(
        key="us-sf",
        name="美国 · 旧金山 (San Francisco)",
        region="US",
        latitude=37.7749,
        longitude=-122.4194,
        altitude_m=16.0,
        note="美国支持全部 watchOS 健康与区域特性",
    ),
    "us-ny": LocationPreset(
        key="us-ny",
        name="美国 · 纽约 (New York)",
        region="US",
        latitude=40.7580,
        longitude=-73.9855,
        altitude_m=15.0,
        note="美国东海岸备用坐标",
    ),
    "jp": LocationPreset(
        key="jp",
        name="日本 · 东京 (Tokyo)",
        region="JP",
        latitude=35.6595,
        longitude=139.7004,
        altitude_m=30.0,
        note="日本支持高血压通知与全部健康功能",
    ),
    "sg": LocationPreset(
        key="sg",
        name="新加坡 (Singapore)",
        region="SG",
        latitude=1.2834,
        longitude=103.8607,
        altitude_m=10.0,
        note="新加坡支持高血压通知与全部健康功能",
    ),
    "tw": LocationPreset(
        key="tw",
        name="中国台湾 · 台北 (Taipei)",
        region="TW",
        latitude=25.0330,
        longitude=121.5654,
        altitude_m=12.0,
        note="注：Apple 脚注提示 S12/Ultra 4 在台湾可能仍需额外审批，S12 优先用 hk/us-sf",
    ),
}

# Offline instant lookup for popular global & Chinese cities / aliases (zero network needed).
PLACE_ALIASES: dict[str, tuple[str, float, float, float]] = {
    "香港": ("香港 · 中环", 22.2819, 114.1589, 18.0),
    "香港中环": ("香港 · 中环", 22.2819, 114.1589, 18.0),
    "hong kong": ("Hong Kong Central", 22.2819, 114.1589, 18.0),
    "尖沙咀": ("香港 · 尖沙咀", 22.2969, 114.1722, 12.0),
    "澳门": ("中国澳门", 22.1987, 113.5439, 16.0),
    "macao": ("Macao", 22.1987, 113.5439, 16.0),
    "macau": ("Macao", 22.1987, 113.5439, 16.0),
    "台北": ("中国台湾 · 台北", 25.0330, 121.5654, 12.0),
    "台湾": ("中国台湾 · 台北", 25.0330, 121.5654, 12.0),
    "taipei": ("Taipei", 25.0330, 121.5654, 12.0),
    "旧金山": ("美国 · 旧金山", 37.7749, -122.4194, 16.0),
    "san francisco": ("San Francisco", 37.7749, -122.4194, 16.0),
    "apple park": ("Apple Park, Cupertino", 37.3346, -122.0090, 72.0),
    "库比蒂诺": ("Apple Park, Cupertino", 37.3346, -122.0090, 72.0),
    "纽约": ("美国 · 纽约时代广场", 40.7580, -73.9855, 15.0),
    "new york": ("New York Times Square", 40.7580, -73.9855, 15.0),
    "洛杉矶": ("美国 · 洛杉矶", 34.0522, -118.2437, 89.0),
    "los angeles": ("Los Angeles", 34.0522, -118.2437, 89.0),
    "西雅图": ("美国 · 西雅图", 47.6062, -122.3321, 56.0),
    "东京": ("日本 · 东京涩谷", 35.6595, 139.7004, 30.0),
    "tokyo": ("Tokyo Shibuya", 35.6595, 139.7004, 30.0),
    "大阪": ("日本 · 大阪梅田", 34.7025, 135.4959, 12.0),
    "osaka": ("Osaka", 34.7025, 135.4959, 12.0),
    "首尔": ("韩国 · 首尔明洞", 37.5636, 126.9869, 38.0),
    "seoul": ("Seoul", 37.5636, 126.9869, 38.0),
    "新加坡": ("新加坡 · 滨海湾", 1.2834, 103.8607, 10.0),
    "singapore": ("Singapore", 1.2834, 103.8607, 10.0),
    "伦敦": ("英国 · 伦敦", 51.5074, -0.1278, 15.0),
    "london": ("London", 51.5074, -0.1278, 15.0),
    "巴黎": ("法国 · 巴黎", 48.8566, 2.3522, 35.0),
    "paris": ("Paris", 48.8566, 2.3522, 35.0),
    "柏林": ("德国 · 柏林", 52.5200, 13.4050, 34.0),
    "berlin": ("Berlin", 52.5200, 13.4050, 34.0),
    "悉尼": ("澳大利亚 · 悉尼", -33.8688, 151.2093, 20.0),
    "sydney": ("Sydney", -33.8688, 151.2093, 20.0),
    "温哥华": ("加拿大 · 温哥华", 49.2827, -123.1207, 25.0),
    "多伦多": ("加拿大 · 多伦多", 43.6532, -79.3832, 76.0),
    "迪拜": ("阿联酋 · 迪拜", 25.2048, 55.2708, 10.0),
    "曼谷": ("泰国 · 曼谷", 13.7563, 100.5018, 5.0),
    "北京": ("中国 · 北京", 39.9042, 116.4074, 45.0),
    "上海": ("中国 · 上海", 31.2304, 121.4737, 10.0),
    "深圳": ("中国 · 深圳", 22.5431, 114.0579, 18.0),
    "广州": ("中国 · 广州", 23.1291, 113.2644, 15.0),
    "杭州": ("中国 · 杭州", 30.2741, 120.1551, 12.0),
    "成都": ("中国 · 成都", 30.5728, 104.0668, 500.0),
}

_COORD_PAIR_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*[,，\s]\s*([+-]?\d+(?:\.\d+)?)\s*$")


def resolve_place(query: str, *, allow_online: bool = True) -> tuple[float, float, float, str]:
    """Resolve preset key, `lat,lon` pair, offline city alias, or online OSM Nominatim place."""
    raw = query.strip()
    if not raw:
        raise ValueError("地点名称不能为空")

    lower = raw.lower()
    if lower in PRESETS:
        p = PRESETS[lower]
        return p.latitude, p.longitude, p.altitude_m, p.name

    match = _COORD_PAIR_RE.match(raw)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        format_nmea_coordinate(lat, is_latitude=True)
        format_nmea_coordinate(lon, is_latitude=False)
        return lat, lon, 15.0, f"坐标 ({lat:.4f}, {lon:.4f})"

    if lower in PLACE_ALIASES:
        label, lat, lon, alt = PLACE_ALIASES[lower]
        return lat, lon, alt, label
    if raw in PLACE_ALIASES:
        label, lat, lon, alt = PLACE_ALIASES[raw]
        return lat, lon, alt, label

    if not allow_online:
        raise ValueError(f"离线词库未命中地点 {raw!r}，请直接传入经纬度 --lat/--lon")

    encoded = urllib.parse.urlencode({"q": raw, "format": "json", "limit": "1"})
    url = f"https://nominatim.openstreetmap.org/search?{encoded}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "apple-watch-health/2.0 (https://github.com/1837620622/apple-watch-health)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12.0) as resp:
            items = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"在线解析地点 {raw!r} 失败（{exc}），请直接用 --lat/--lon 指定坐标") from exc

    if not items:
        raise ValueError(f"未找到地点 {raw!r}，请换更具体的地名或直接传入 --lat/--lon")
    top = items[0]
    lat, lon = float(top["lat"]), float(top["lon"])
    format_nmea_coordinate(lat, is_latitude=True)
    format_nmea_coordinate(lon, is_latitude=False)
    label = str(top.get("display_name", raw))[:80]
    return lat, lon, 15.0, label


def build_identity_message() -> Message:
    """Build iAP2 IdentificationInformation (0x1D01) for USB GPS accessory."""
    fields = [
        encode_text_parameter(idx, text)
        for idx, text in enumerate(
            ("Accessory Location Lab", "AL-1", "Local Research", "AL-LOCAL-001", "0.2", "1")
        )
    ]
    fields += [
        encode_parameter(6, struct.pack(">H", 0xFFFB)),
        encode_parameter(7, struct.pack(">HH", 0xFFFA, 0xFFFC)),
        encode_parameter(8, b"\x00"),
        encode_parameter(9, b"\x00\x00"),
        encode_text_parameter(12, "en"),
        encode_text_parameter(13, "en"),
    ]
    usb_component = (
        encode_parameter(0, b"\x00\x00")
        + encode_text_parameter(1, "Location Transport")
        + encode_parameter(2)
    )
    fields.append(encode_parameter(16, usb_component))

    location_component = encode_parameter(0, b"\x00\x00") + encode_text_parameter(1, "GPS")
    fields.append(
        encode_parameter(22, location_component + encode_parameter(17) + encode_parameter(18))
    )
    return Message(0x1D01, b"".join(fields))


def format_nmea_coordinate(value: float, *, is_latitude: bool) -> tuple[str, str]:
    """Convert decimal degrees to NMEA DDMM.MMMM / DDDMM.MMMM without 60.0000 wrap bugs."""
    limit = 90.0 if is_latitude else 180.0
    if not math.isfinite(value) or not -limit <= value <= limit:
        raise ValueError(f"Coordinate out of range ({-limit}..{limit}): {value}")
    units = round(abs(value) * 60 * 10000)
    degrees, minute_units = divmod(units, 600000)
    minutes = minute_units / 10000.0
    if is_latitude:
        hemisphere = "S" if value < 0 else "N"
        width = 2
    else:
        hemisphere = "W" if value < 0 else "E"
        width = 3
    return f"{degrees:0{width}d}{minutes:07.4f}", hemisphere


def wrap_nmea_sentence(body: str) -> str:
    """Attach NMEA `$` prefix, XOR checksum, and CRLF terminator."""
    checksum = 0
    for byte in body.encode("ascii"):
        checksum ^= byte
    return f"${body}*{checksum:02X}\r\n"


def build_location_sentences(
    latitude: float,
    longitude: float,
    *,
    altitude_m: float = 15.0,
    when: datetime | None = None,
) -> dict[int, str]:
    """Generate stationary GPGGA (1) and GPRMC (2) NMEA 0183 sentences."""
    now = (when or datetime.now(UTC)).astimezone(UTC)
    lat_str, ns = format_nmea_coordinate(latitude, is_latitude=True)
    lon_str, ew = format_nmea_coordinate(longitude, is_latitude=False)
    clock = now.strftime("%H%M%S") + ".00"
    date = now.strftime("%d%m%y")
    alt = f"{altitude_m:.1f}"
    return {
        1: wrap_nmea_sentence(f"GPGGA,{clock},{lat_str},{ns},{lon_str},{ew},1,09,0.8,{alt},M,0.0,M,,"),
        2: wrap_nmea_sentence(f"GPRMC,{clock},A,{lat_str},{ns},{lon_str},{ew},0.0,0.0,{date},,,A"),
    }


def build_location_message(sentences: list[str]) -> Message:
    """Encode iAP2 LocationInformation (0xFFFB) carrying NMEA sentences."""
    return Message(0xFFFB, b"".join(encode_text_parameter(0, s) for s in sentences))
