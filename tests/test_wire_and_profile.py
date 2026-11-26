"""Tests for iAP2 wire framing, control messages, and NMEA 0183 coordinate generation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apple_watch_health.profile import (
    PRESETS,
    build_identity_message,
    build_location_message,
    build_location_sentences,
    format_nmea_coordinate,
    wrap_nmea_sentence,
)
from apple_watch_health.wire import (
    ACK,
    SYN,
    Message,
    MessageBuffer,
    Packet,
    ProtocolError,
    decode_parameters,
    encode_parameter,
    encode_text_parameter,
)


def test_packet_roundtrip_and_checksum_validation() -> None:
    pkt = Packet(flags=ACK, sequence=7, acknowledgement=3, session=1, payload=b"hello")
    raw = pkt.encode()
    assert Packet.decode(raw) == pkt

    # Corrupt payload checksum -> ProtocolError
    bad = bytearray(raw)
    bad[-1] ^= 0x01
    with pytest.raises(ProtocolError):
        Packet.decode(bytes(bad))

    # Empty packet (SYN without payload)
    syn = Packet(flags=SYN, sequence=1, acknowledgement=0)
    assert Packet.decode(syn.encode()) == syn


def test_message_buffer_reassembles_fragmented_and_coalesced_messages() -> None:
    m1 = Message(0x1D00, encode_parameter(0, b"\x01\x02"))
    m2 = Message(0xAA05, b"")
    combined = m1.encode() + m2.encode()

    buf = MessageBuffer()
    assert buf.feed(combined[:4]) == []
    out = buf.feed(combined[4:])
    assert out == [m1, m2]


def test_tlv_parameters_and_identity_message() -> None:
    msg = build_identity_message()
    assert msg.identifier == 0x1D01
    fields = dict(decode_parameters(msg.body))
    assert 16 in fields  # USB transport component
    assert 22 in fields  # Location component (GGA + RMC)

    with pytest.raises(ValueError):
        encode_text_parameter(0, "bad\x00nul")


def test_nmea_coordinate_rounding_never_produces_60_minutes() -> None:
    # 22.9999999 deg must round to 2300.0000, NEVER 2260.0000
    coord, hemi = format_nmea_coordinate(22.9999999, is_latitude=True)
    assert coord == "2300.0000"
    assert hemi == "N"

    lon_coord, lon_hemi = format_nmea_coordinate(-122.4194, is_latitude=False)
    assert lon_coord.startswith("122")
    assert lon_hemi == "W"

    with pytest.raises(ValueError):
        format_nmea_coordinate(91.0, is_latitude=True)
    with pytest.raises(ValueError):
        format_nmea_coordinate(float("nan"), is_latitude=False)


def test_location_sentences_and_presets() -> None:
    when = datetime(2026, 11, 27, 1, 2, 3, tzinfo=UTC)
    hk = PRESETS["hk"]
    sentences = build_location_sentences(hk.latitude, hk.longitude, altitude_m=hk.altitude_m, when=when)
    assert sentences[1].startswith("$GPGGA,010203.00,")
    assert sentences[2].startswith("$GPRMC,010203.00,A,")
    assert sentences[1].endswith("\r\n")
    assert wrap_nmea_sentence("GPGGA,test").startswith("$GPGGA,test*")

    loc_msg = build_location_message([sentences[1], sentences[2]])
    assert loc_msg.identifier == 0xFFFB
    params = decode_parameters(loc_msg.body)
    assert len(params) == 2
