"""iAP2 link-layer packet framing and control-session TLV message codec."""

from __future__ import annotations

import struct
from dataclasses import dataclass

SYN = 0x80
ACK = 0x40
EAK = 0x20
RST = 0x10
LINK_MARKER = b"\xff\x5a"
CONTROL_MARKER = b"@@"
DETECT_SEQUENCE = bytes.fromhex("ff550200ee10")


class ProtocolError(ValueError):
    """Raised on malformed iAP2 frames, checksums, or control messages."""


@dataclass(frozen=True, slots=True)
class Packet:
    """iAP2 link packet with additive header and optional payload checksums."""

    flags: int
    sequence: int
    acknowledgement: int
    session: int = 0
    payload: bytes = b""

    def encode(self) -> bytes:
        total_size = 9 + len(self.payload) + (1 if self.payload else 0)
        header = struct.pack(
            ">2sHBBBB",
            LINK_MARKER,
            total_size,
            self.flags & 0xFF,
            self.sequence & 0xFF,
            self.acknowledgement & 0xFF,
            self.session & 0xFF,
        )
        frame = header + bytes([(-sum(header)) & 0xFF])
        if self.payload:
            frame += self.payload + bytes([(-sum(self.payload)) & 0xFF])
        return frame

    @classmethod
    def decode(cls, raw: bytes) -> Packet:
        if len(raw) < 9 or raw[:2] != LINK_MARKER:
            raise ProtocolError("Bad iAP2 link marker or truncated header")
        declared_size = int.from_bytes(raw[2:4], "big")
        if declared_size != len(raw) or (sum(raw[:9]) & 0xFF) != 0:
            raise ProtocolError("Bad iAP2 link length or header checksum")
        if len(raw) == 10 or (len(raw) > 9 and (sum(raw[9:]) & 0xFF) != 0):
            raise ProtocolError("Bad iAP2 link payload or payload checksum")
        return cls(
            flags=raw[4],
            sequence=raw[5],
            acknowledgement=raw[6],
            session=raw[7],
            payload=raw[9:-1] if len(raw) > 9 else b"",
        )


def encode_parameter(identifier: int, data: bytes = b"") -> bytes:
    """Encode an iAP2 TLV parameter: 2B length (inclusive) + 2B ID + data."""
    return struct.pack(">HH", len(data) + 4, identifier) + data


def decode_parameters(raw: bytes) -> list[tuple[int, bytes]]:
    """Decode sequential iAP2 TLV parameters."""
    items: list[tuple[int, bytes]] = []
    offset = 0
    total = len(raw)
    while offset < total:
        if total - offset < 4:
            raise ProtocolError("Truncated iAP2 parameter header")
        size, identifier = struct.unpack_from(">HH", raw, offset)
        if size < 4 or offset + size > total:
            raise ProtocolError("Invalid iAP2 parameter length")
        items.append((identifier, raw[offset + 4 : offset + size]))
        offset += size
    return items


def encode_text_parameter(identifier: int, value: str) -> bytes:
    """Encode a UTF-8 NUL-terminated iAP2 text parameter."""
    if "\0" in value:
        raise ValueError("Embedded NUL in iAP2 text parameter")
    return encode_parameter(identifier, value.encode("utf-8") + b"\0")


@dataclass(frozen=True, slots=True)
class Message:
    """iAP2 control-session message (`@@` + 2B length + 2B ID + TLV body)."""

    identifier: int
    body: bytes = b""

    def encode(self) -> bytes:
        return struct.pack(">2sHH", CONTROL_MARKER, len(self.body) + 6, self.identifier) + self.body


class MessageBuffer:
    """Reassembles control messages split across or coalesced inside link packets."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Message]:
        self._buf.extend(data)
        messages: list[Message] = []
        while len(self._buf) >= 6:
            marker, size, identifier = struct.unpack_from(">2sHH", self._buf)
            if marker != CONTROL_MARKER or size < 6:
                raise ProtocolError("Invalid iAP2 control message header")
            if len(self._buf) < size:
                break
            messages.append(Message(identifier, bytes(self._buf[6:size])))
            del self._buf[:size]
        return messages
