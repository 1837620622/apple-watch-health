"""iAP2 accessory identity profile, verified region presets, and NMEA 0183 generator."""

from __future__ import annotations

import math
import struct
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
