"""Cross-platform JSONL authentication subprocess boundary (macOS & Windows safe)."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .wire import Message, ProtocolError, encode_parameter


def build_helper_command(helper: str | Path | Sequence[str] | None = None) -> list[str]:
    """Build cross-platform command list for the local MFi helper subprocess."""
    if helper is None:
        return [sys.executable, "-m", "apple_watch_health.mfi"]
    if isinstance(helper, (list, tuple)):
        return [str(part) for part in helper]
    helper_str = str(helper)
    if Path(helper_str).suffix.lower() == ".py":
        return [sys.executable, helper_str]
    return [helper_str]


class AuthenticationProvider:
    """Async JSON-lines subprocess client for iAP2 certificate & challenge signing."""

    def __init__(
        self,
        helper: str | Path | Sequence[str] | None = None,
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = build_helper_command(helper)
        self.env = env
        self.process: asyncio.subprocess.Process | None = None

    async def request(self, operation: str, **fields: str) -> dict[str, str]:
        if self.process is not None and self.process.returncode is not None:
            self.process = None
        if self.process is None:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                limit=128 * 1024,
                env=self.env,
            )
        assert self.process.stdin is not None and self.process.stdout is not None
        payload = json.dumps({"op": operation, **fields}).encode("utf-8") + b"\n"
        self.process.stdin.write(payload)
        await self.process.stdin.drain()

        line = await asyncio.wait_for(self.process.stdout.readline(), 40.0)
        if not line:
            raise RuntimeError("MFi authentication helper exited without a response")
        try:
            result = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise ProtocolError("MFi authentication helper returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise ProtocolError("MFi authentication helper reply must be a JSON object")
        if result.get("error"):
            raise RuntimeError(f"MFi authentication helper: {result['error']}")
        return result

    async def certificate(self) -> Message:
        result = await self.request("certificate")
        raw_b64 = result.get("certificate")
        if not isinstance(raw_b64, str):
            raise ProtocolError("Missing MFi authentication certificate")
        try:
            cert_raw = base64.b64decode(raw_b64, validate=True)
        except ValueError as exc:
            raise ProtocolError("Invalid base64 MFi authentication certificate") from exc
        if not cert_raw:
            raise ProtocolError("Empty MFi authentication certificate")
        body = encode_parameter(0, cert_raw)
        auth_type = result.get("type")
        if auth_type == "baa":
            inter_b64 = result.get("intermediate")
            if not isinstance(inter_b64, str):
                raise ProtocolError("Missing BAA intermediate certificate")
            try:
                intermediate = base64.b64decode(inter_b64, validate=True)
            except ValueError as exc:
                raise ProtocolError("Invalid base64 BAA intermediate certificate") from exc
            if not intermediate:
                raise ProtocolError("Empty BAA intermediate certificate")
            body += encode_parameter(1, b"\x01") + encode_parameter(2, intermediate)
        elif auth_type != "mfi":
            raise ProtocolError("Authentication helper type must be 'mfi' or 'baa'")
        return Message(0xAA01, body)

    async def sign(self, challenge: bytes) -> Message:
        if not 1 <= len(challenge) <= 4096:
            raise ProtocolError("Invalid MFi challenge length")
        encoded = base64.b64encode(challenge).decode("ascii")
        result = await self.request("sign", challenge=encoded)
        sig_b64 = result.get("signature")
        if not isinstance(sig_b64, str):
            raise ProtocolError("Missing MFi signature")
        try:
            signature = base64.b64decode(sig_b64, validate=True)
        except ValueError as exc:
            raise ProtocolError("Invalid base64 MFi signature") from exc
        if not 1 <= len(signature) <= 4096:
            raise ProtocolError("Invalid MFi signature length")
        return Message(0xAA03, encode_parameter(0, signature))

    async def close(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.stdin and not self.process.stdin.is_closing():
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 2.0)
            except TimeoutError:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 3.0)
                except TimeoutError:
                    self.process.kill()
                    await asyncio.wait_for(self.process.wait(), 2.0)
        except (ProcessLookupError, TimeoutError):
            pass
        finally:
            self.process = None
