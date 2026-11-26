"""Stop-and-wait iAP2 reliable link state machine over an async byte stream."""

from __future__ import annotations

import asyncio
import secrets
import struct
from collections import deque
from collections.abc import Callable
from typing import Any

from .wire import ACK, EAK, LINK_MARKER, RST, SYN, Message, MessageBuffer, Packet, ProtocolError


class Link:
    """Async iAP2 control-session link with partial-read preservation across timeouts."""

    def __init__(self, stream: Any, emit: Callable[..., None] | None = None) -> None:
        self.stream = stream
        self.emit = emit or (lambda **_event: None)
        self.sequence = secrets.randbelow(256)
        self.peer_sequence = 0
        self.session = 1
        self.maximum_packet = 4096
        self.retransmit_seconds = 2.0
        self.messages: deque[Message] = deque()
        self.assembler = MessageBuffer()
        self._reader_task: asyncio.Task[Packet] | None = None

    async def _read_packet(self) -> Packet:
        header = await self.stream.recvall(9)
        if len(header) != 9:
            raise EOFError("iAP2 stream closed")
        size = int.from_bytes(header[2:4], "big")
        if header[:2] != LINK_MARKER or (sum(header) & 0xFF) != 0 or not 9 <= size <= 4096:
            raise ProtocolError("Malformed or oversized incoming iAP2 packet")
        rest = await self.stream.recvall(size - 9) if size > 9 else b""
        return Packet.decode(header + rest)

    async def read(self, timeout: float) -> Packet:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_packet())
        packet = await asyncio.wait_for(asyncio.shield(self._reader_task), timeout)
        self._reader_task = None
        return packet

    async def write(self, packet: Packet) -> None:
        await self.stream.sendall(packet.encode())

    async def handshake(self) -> None:
        offer = struct.pack(">BBHHHBB", 1, 1, 4096, 2000, 22, 5, 1) + bytes([1, 0, 2])
        syn = Packet(SYN, self.sequence, 0, payload=offer)
        for _ in range(3):
            await self.write(syn)
            try:
                response = await self.read(2.0)
            except TimeoutError:
                continue
            if (response.flags & (SYN | ACK)) != (SYN | ACK):
                raise ProtocolError("Expected iAP2 SYN+ACK")
            if response.acknowledgement != self.sequence or len(response.payload) < 13:
                raise ProtocolError("Invalid iAP2 handshake parameters")
            version, window, size, retry, _, _, _ = struct.unpack(">BBHHHBB", response.payload[:10])
            sessions = response.payload[10:]
            if version != 1 or window < 1 or size < 10 or len(sessions) % 3 != 0:
                raise ProtocolError("Unsupported iAP2 link parameters")
            triplets = [sessions[i : i + 3] for i in range(0, len(sessions), 3)]
            if bytes([1, 0, 2]) not in triplets:
                raise ProtocolError("Peer rejected iAP2 control session (1, 0, 2)")
            self.maximum_packet = min(size, 4096)
            self.retransmit_seconds = max(retry / 1000.0, 0.1)
            self.peer_sequence = response.sequence
            await self.acknowledge()
            self.emit(event="link_ready", control_session=self.session)
            return
        raise TimeoutError("Timed out waiting for iAP2 SYN+ACK")

    async def acknowledge(self) -> None:
        await self.write(Packet(ACK, self.sequence, self.peer_sequence))

    async def accept(self, packet: Packet) -> None:
        if packet.flags & RST:
            raise ConnectionError("Peer reset the iAP2 link")
        if packet.flags & SYN:
            if packet.sequence != self.peer_sequence:
                raise ProtocolError("Unexpected SYN during established iAP2 link")
            await self.acknowledge()
            return
        if packet.payload and not (packet.flags & EAK):
            if packet.sequence == ((self.peer_sequence + 1) & 0xFF):
                self.peer_sequence = packet.sequence
                if packet.session != self.session:
                    raise ProtocolError("Data received on unnegotiated iAP2 session")
                self.messages.extend(self.assembler.feed(packet.payload))
            await self.acknowledge()

    async def send(self, message: Message) -> None:
        raw = message.encode()
        chunk_limit = self.maximum_packet - 10
        loop = asyncio.get_running_loop()
        for offset in range(0, len(raw), chunk_limit):
            self.sequence = (self.sequence + 1) & 0xFF
            payload = raw[offset : offset + chunk_limit]
            acknowledged = False
            for attempt in range(4):
                await self.write(
                    Packet(ACK, self.sequence, self.peer_sequence, self.session, payload)
                )
                deadline = loop.time() + self.retransmit_seconds
                while (remaining := deadline - loop.time()) > 0:
                    try:
                        packet = await self.read(remaining)
                    except TimeoutError:
                        break
                    await self.accept(packet)
                    if (packet.flags & ACK) and packet.acknowledgement == self.sequence:
                        acknowledged = True
                        break
                if acknowledged:
                    break
                self.emit(event="retransmit", attempt=attempt + 1)
            if not acknowledged:
                raise TimeoutError("Peer did not acknowledge iAP2 data packet")

    async def receive(self, timeout: float = 10.0) -> Message:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self.messages:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("No iAP2 control message before deadline")
            await self.accept(await self.read(remaining))
        return self.messages.popleft()

    async def close(self) -> None:
        try:
            if self._reader_task is not None:
                self._reader_task.cancel()
                await asyncio.gather(self._reader_task, return_exceptions=True)
                self._reader_task = None
        finally:
            await self.stream.close()
