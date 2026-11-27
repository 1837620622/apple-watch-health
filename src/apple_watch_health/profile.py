"""iAP2 accessory identity profile, verified region presets, and NMEA 0183 generator."""

from __future__ import annotations

import json
import math
import random
import re
import struct
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
    "北京": ("中国 · 北京天安门", 39.9087, 116.3975, 45.0),
    "beijing": ("Beijing", 39.9087, 116.3975, 45.0),
    "上海": ("中国 · 上海外滩", 31.2397, 121.4998, 10.0),
    "shanghai": ("Shanghai", 31.2397, 121.4998, 10.0),
    "深圳": ("中国 · 深圳福田", 22.5431, 114.0579, 18.0),
    "shenzhen": ("Shenzhen", 22.5431, 114.0579, 18.0),
    "广州": ("中国 · 广州珠江新城", 23.1291, 113.2644, 15.0),
    "guangzhou": ("Guangzhou", 23.1291, 113.2644, 15.0),
    "杭州": ("中国 · 杭州西湖", 30.2590, 120.1486, 12.0),
    "hangzhou": ("Hangzhou", 30.2590, 120.1486, 12.0),
    "成都": ("中国 · 成都春熙路", 30.6570, 104.0815, 500.0),
    "chengdu": ("Chengdu", 30.6570, 104.0815, 500.0),
    "重庆": ("中国 · 重庆解放碑", 29.5572, 106.5770, 260.0),
    "南京": ("中国 · 南京新街口", 32.0418, 118.7841, 20.0),
    "武汉": ("中国 · 武汉江汉路", 30.5810, 114.2916, 25.0),
    "西安": ("中国 · 西安钟楼", 34.2594, 108.9470, 410.0),
    "天津": ("中国 · 天津之眼", 39.1536, 117.1867, 8.0),
    "苏州": ("中国 · 苏州金鸡湖", 31.3167, 120.7069, 6.0),
    "长沙": ("中国 · 长沙五一广场", 28.1941, 112.9762, 50.0),
    "郑州": ("中国 · 郑州二七广场", 34.7525, 113.6660, 110.0),
    "青岛": ("中国 · 青岛五四广场", 36.0638, 120.3826, 15.0),
    "厦门": ("中国 · 厦门鼓浪屿", 24.4478, 118.0672, 10.0),
    "大连": ("中国 · 大连星海广场", 38.8825, 121.5892, 15.0),
    "昆明": ("中国 · 昆明滇池", 24.9746, 102.6606, 1890.0),
    "哈尔滨": ("中国 · 哈尔滨中央大街", 45.7732, 126.6203, 130.0),
    "沈阳": ("中国 · 沈阳中街", 41.8016, 123.4614, 55.0),
    "合肥": ("中国 · 合肥天鹅湖", 31.8195, 117.2202, 35.0),
    "福州": ("中国 · 福州三坊七巷", 26.0825, 119.2965, 15.0),
    "济南": ("中国 · 济南趵突泉", 36.6612, 117.0156, 35.0),
    "珠海": ("中国 · 珠海情侣路", 22.2707, 113.5767, 10.0),
    "三亚": ("中国 · 三亚亚龙湾", 18.2036, 109.6393, 8.0),
    "拉萨": ("中国 · 拉萨布达拉宫", 29.6555, 91.1186, 3650.0),
    "芝加哥": ("美国 · 芝加哥", 41.8781, -87.6298, 180.0),
    "波士顿": ("美国 · 波士顿", 42.3601, -71.0589, 14.0),
    "硅谷": ("美国 · 硅谷帕洛阿尔托", 37.4419, -122.1430, 15.0),
    "墨尔本": ("澳大利亚 · 墨尔本", -37.8136, 144.9631, 31.0),
    "吉隆坡": ("马来西亚 · 吉隆坡双子塔", 3.1579, 101.7116, 66.0),
    "釜山": ("韩国 · 釜山海云台", 35.1587, 129.1604, 12.0),
    "京都": ("日本 · 京都清水寺", 34.9949, 135.7850, 90.0),
    "苏黎世": ("瑞士 · 苏黎世", 47.3769, 8.5417, 408.0),
    "罗马": ("意大利 · 罗马斗兽场", 41.8902, 12.4922, 22.0),
    "阿姆斯特丹": ("荷兰 · 阿姆斯特丹", 52.3676, 4.9041, 2.0),
    "莫斯科": ("俄罗斯 · 莫斯科红场", 55.7539, 37.6208, 156.0),
}

_COORD_PAIR_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*[,，\s]\s*([+-]?\d+(?:\.\d+)?)\s*$")
_MAX_GEOCODE_BYTES = 256 * 1024

# --------------------------------------------------------------------------------------------
# Coordinate datum conversion (GCJ-02 / BD-09 -> WGS-84, matching i4Tools/Aisi China offset)
# --------------------------------------------------------------------------------------------

_EARTH_A = 6378245.0
_EARTH_EE = 0.00669342162296594323
_X_PI = math.pi * 3000.0 / 180.0


def _out_of_china_mainland(lat: float, lon: float) -> bool:
    """Approximate China mainland bounding check (GCJ-02 applies only inside mainland China)."""
    if not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271):
        return True
    # Exclude Hong Kong / Macao / Taiwan core boxes so HK/MO/TW coordinates are never shifted.
    if 22.10 <= lat <= 22.56 and 113.45 <= lon <= 114.45:
        return True
    if 21.80 <= lat <= 25.40 and 119.30 <= lon <= 122.10:
        return True
    return False


def _gcj_delta(lat: float, lon: float) -> tuple[float, float]:
    x = lon - 105.0
    y = lat - 35.0
    dlat = (
        -100.0
        + 2.0 * x
        + 3.0 * y
        + 0.2 * y * y
        + 0.1 * x * y
        + 0.2 * math.sqrt(abs(x))
        + (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        + (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
        + (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    )
    dlon = (
        300.0
        + x
        + 2.0 * y
        + 0.1 * x * x
        + 0.1 * x * y
        + 0.1 * math.sqrt(abs(x))
        + (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        + (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
        + (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    )
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1.0 - _EARTH_EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_EARTH_A * (1.0 - _EARTH_EE)) / (magic * sqrt_magic) * math.pi)
    dlon = (dlon * 180.0) / (_EARTH_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return dlat, dlon


def wgs84_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    """Convert WGS-84 coordinate to GCJ-02 (Mars coordinate used by AMap/WeChat in mainland China)."""
    if _out_of_china_mainland(lat, lon):
        return lat, lon
    dlat, dlon = _gcj_delta(lat, lon)
    return lat + dlat, lon + dlon


def gcj02_to_wgs84(lat: float, lon: float) -> tuple[float, float]:
    """Convert GCJ-02 (AMap/Tencent/WeChat) coordinate to WGS-84 via fixed-point iteration (<1cm)."""
    if _out_of_china_mainland(lat, lon):
        return lat, lon
    w_lat, w_lon = lat, lon
    for _ in range(10):
        g_lat, g_lon = wgs84_to_gcj02(w_lat, w_lon)
        d_lat = g_lat - lat
        d_lon = g_lon - lon
        w_lat -= d_lat
        w_lon -= d_lon
        if max(abs(d_lat), abs(d_lon)) < 1e-8:
            break
    return w_lat, w_lon


def bd09_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    """Convert BD-09 (Baidu Maps) coordinate to GCJ-02."""
    x = lon - 0.0065
    y = lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * _X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _X_PI)
    return z * math.sin(theta), z * math.cos(theta)


def convert_to_wgs84(lat: float, lon: float, coord_type: str = "wgs84") -> tuple[float, float]:
    """Convert user-provided coordinates (wgs84/gcj02/bd09) into iOS CoreLocation WGS-84."""
    format_nmea_coordinate(lat, is_latitude=True)
    format_nmea_coordinate(lon, is_latitude=False)
    kind = coord_type.strip().lower()
    if kind == "wgs84":
        return lat, lon
    if kind == "gcj02":
        w_lat, w_lon = gcj02_to_wgs84(lat, lon)
    elif kind == "bd09":
        g_lat, g_lon = bd09_to_gcj02(lat, lon)
        w_lat, w_lon = gcj02_to_wgs84(g_lat, g_lon)
    else:
        raise ValueError(f"不支持的坐标系类型: {coord_type!r}（仅支持 wgs84 / gcj02 / bd09）")
    format_nmea_coordinate(w_lat, is_latitude=True)
    format_nmea_coordinate(w_lon, is_latitude=False)
    return w_lat, w_lon


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
        first, second = float(match.group(1)), float(match.group(2))
        # Auto-detect AMap/Baidu picker `lon,lat` order (e.g. `116.3974,39.9092`) when |first| > 90.
        if abs(first) > 90.0 and abs(first) <= 180.0 and abs(second) <= 90.0:
            lat, lon = second, first
            note = " [自动识别经度,纬度]"
        else:
            lat, lon = first, second
            note = ""
        format_nmea_coordinate(lat, is_latitude=True)
        format_nmea_coordinate(lon, is_latitude=False)
        return lat, lon, 15.0, f"坐标 ({lat:.4f}, {lon:.4f}){note}"

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
        headers={"User-Agent": "apple-watch-health/2.3 (https://github.com/1837620622/apple-watch-health)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12.0) as resp:
            if not resp.geturl().startswith("https://"):
                raise ValueError("地名解析服务重定向到非 HTTPS 地址")
            body = resp.read(_MAX_GEOCODE_BYTES + 1)
            if len(body) > _MAX_GEOCODE_BYTES:
                raise ValueError("地名解析响应体积异常")
            items = json.loads(body.decode("utf-8"))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"在线解析地点 {raw!r} 失败（{exc}），请直接用 --lat/--lon 指定坐标") from exc

    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise ValueError(f"未找到地点 {raw!r}，请换更具体的地名或直接传入 --lat/--lon")
    top = items[0]
    try:
        lat, lon = float(top["lat"]), float(top["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"在线地名服务返回坐标格式异常: {exc}") from exc
    format_nmea_coordinate(lat, is_latitude=True)
    format_nmea_coordinate(lon, is_latitude=False)
    label = str(top.get("display_name", raw))[:80]
    return lat, lon, 15.0, label


# --------------------------------------------------------------------------------------------
# Natural GPS jitter & GPX route loader (matching i4Tools route/anti-static capabilities)
# --------------------------------------------------------------------------------------------

_MAX_GPX_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RoutePoint:
    latitude: float
    longitude: float
    delay_seconds: float | None = None


def apply_gps_jitter(latitude: float, longitude: float, radius_m: float) -> tuple[float, float]:
    """Apply realistic random GPS drift within *radius_m* meters around (latitude, longitude)."""
    if not math.isfinite(radius_m) or radius_m <= 0:
        return latitude, longitude
    # Uniform sampling inside disk of radius_m.
    dist = radius_m * math.sqrt(random.random())
    bearing = random.uniform(0.0, 2.0 * math.pi)
    d_lat = (dist * math.cos(bearing)) / 111_320.0
    cos_lat = max(0.05, math.cos(math.radians(latitude)))
    d_lon = (dist * math.sin(bearing)) / (111_320.0 * cos_lat)
    j_lat = max(-89.9999, min(89.9999, latitude + d_lat))
    j_lon = ((longitude + d_lon + 180.0) % 360.0) - 180.0
    return j_lat, j_lon


def _parse_iso_timestamp(text: str | None) -> datetime | None:
    if not text:
        return None
    cleaned = text.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def load_gpx_route(path: Path, *, coord_type: str = "wgs84") -> list[RoutePoint]:
    """Safely parse GPX 1.0/1.1 `<trkpt>`/`<rtept>`/`<wpt>` points using defusedxml (XXE-safe)."""
    from defusedxml import ElementTree as DefusedET

    expanded = path.expanduser()
    with expanded.open("rb") as fh:
        raw = fh.read(_MAX_GPX_BYTES + 1)
    if not raw or len(raw) > _MAX_GPX_BYTES:
        raise ValueError(f"GPX 文件大小必须在 1..{_MAX_GPX_BYTES} 字节之间")

    try:
        root = DefusedET.fromstring(raw)
    except Exception as exc:
        raise ValueError(f"GPX XML 解析失败: {exc}") from exc

    def _local(tag: str) -> str:
        return tag.rpartition("}")[2].lower()

    elements = [el for el in root.iter() if _local(el.tag) in ("trkpt", "rtept")]
    if not elements:
        elements = [el for el in root.iter() if _local(el.tag) == "wpt"]
    if not elements:
        raise ValueError("GPX 文件中未找到轨迹点（<trkpt> / <rtept> / <wpt>）")

    points: list[RoutePoint] = []
    prev_ts: datetime | None = None
    for el in elements:
        try:
            lat = float(el.attrib["lat"])
            lon = float(el.attrib["lon"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"GPX 轨迹点缺少有效 lat/lon 属性: {el.attrib}") from exc
        lat, lon = convert_to_wgs84(lat, lon, coord_type)
        ts_text = next((child.text for child in el if _local(child.tag) == "time"), None)
        ts = _parse_iso_timestamp(ts_text)
        delay: float | None = None
        if prev_ts is not None and ts is not None:
            delta = (ts - prev_ts).total_seconds()
            if delta >= 0:
                delay = delta
        prev_ts = ts
        points.append(RoutePoint(latitude=lat, longitude=lon, delay_seconds=delay))
    return points


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
