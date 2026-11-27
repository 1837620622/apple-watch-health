"""Local MFi-v3 identity validation and isolated JSONL signing helper.

Private keys stay strictly inside this process/helper boundary. Validates:
- DER PKCS#7 certificate container with exactly one non-CA P-256 leaf matching key
- Unencrypted DER PKCS#8 P-256 private key (rejects SEC1 or other curves)
- 32-byte prehashed SHA-256 challenge -> 64-byte raw `r || s` ECDSA signature
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.serialization import pkcs7

MAX_IDENTITY_BYTES = 16 * 1024
MAX_REQUEST_BYTES = 128 * 1024


class ProviderError(Exception):
    """Raised on invalid local MFi credentials or malformed helper requests."""


def is_der_pkcs8(data: bytes) -> bool:
    """Check ASN.1 structure for unencrypted DER PKCS#8 PrivateKeyInfo."""

    def read_tlv(offset: int) -> tuple[int, int, int]:
        if offset + 2 > len(data):
            raise ValueError("Truncated ASN.1 TLV")
        tag = data[offset]
        length = data[offset + 1]
        offset += 2
        if length & 0x80:
            octets = length & 0x7F
            if not 1 <= octets <= 4 or offset + octets > len(data):
                raise ValueError("Invalid ASN.1 length")
            length = int.from_bytes(data[offset : offset + octets], "big")
            offset += octets
        end = offset + length
        if end > len(data):
            raise ValueError("ASN.1 element exceeds buffer")
        return tag, offset, end

    try:
        seq_tag, start, end = read_tlv(0)
        if seq_tag != 0x30 or end != len(data):
            return False
        ver_tag, _, pos = read_tlv(start)
        alg_tag, _, pos = read_tlv(pos)
        key_tag, _, _ = read_tlv(pos)
        return ver_tag == 0x02 and alg_tag == 0x30 and key_tag == 0x04
    except (ValueError, IndexError):
        return False


def validate_identity(certificate: bytes, encoded_key: bytes) -> ec.EllipticCurvePrivateKey:
    """Validate DER PKCS#7 certificate + DER PKCS#8 P-256 private key pair."""
    for blob in (certificate, encoded_key):
        if not blob or len(blob) > MAX_IDENTITY_BYTES:
            raise ProviderError(f"Identity files must be 1..{MAX_IDENTITY_BYTES} bytes")

    try:
        certs = pkcs7.load_der_pkcs7_certificates(certificate)
    except (ValueError, TypeError) as exc:
        raise ProviderError("Certificate must be a valid DER PKCS#7 container") from exc
    if not certs:
        raise ProviderError("PKCS#7 container contains no certificates")

    if not is_der_pkcs8(encoded_key):
        raise ProviderError("Private key must be unencrypted DER PKCS#8")
    try:
        key = serialization.load_der_private_key(encoded_key, password=None)
    except (ValueError, TypeError) as exc:
        raise ProviderError("Private key must be unencrypted DER PKCS#8") from exc

    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ProviderError("Private key must use EC P-256 (SECP256R1)")

    expected_pub = key.public_key().public_numbers()
    matching_leaves = 0
    for cert in certs:
        try:
            if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                continue
        except x509.ExtensionNotFound:
            pass
        pub = cert.public_key()
        if (
            isinstance(pub, ec.EllipticCurvePublicKey)
            and isinstance(pub.curve, ec.SECP256R1)
            and pub.public_numbers() == expected_pub
        ):
            matching_leaves += 1

    if matching_leaves != 1:
        raise ProviderError("Private key must match exactly one non-CA P-256 leaf certificate")
    return key


def _read_bounded_file(path: Path, limit: int = MAX_IDENTITY_BYTES) -> bytes:
    with path.open("rb") as fh:
        data = fh.read(limit + 1)
    if not data or len(data) > limit:
        raise ProviderError(f"Identity files must be 1..{limit} bytes")
    return data


def inspect_identity_files(cert_path: Path, key_path: Path) -> dict[str, str | int | bool]:
    """Validate local identity files and return non-sensitive summary metadata."""
    cert_bytes = _read_bounded_file(cert_path)
    key_bytes = _read_bounded_file(key_path)
    validate_identity(cert_bytes, key_bytes)
    return {
        "valid": True,
        "cert_path": str(cert_path),
        "key_path": str(key_path),
        "cert_bytes": len(cert_bytes),
        "key_bytes": len(key_bytes),
        "cert_sha256": hashlib.sha256(cert_bytes).hexdigest()[:16],
    }


def _read_env_file(env_names: tuple[str, ...]) -> bytes:
    path_str = next((os.environ.get(name) for name in env_names if os.environ.get(name)), None)
    if not path_str:
        raise ProviderError(f"Set {env_names[0]} to a local identity file")
    try:
        with Path(path_str).open("rb") as fh:
            data = fh.read(MAX_IDENTITY_BYTES + 1)
    except OSError as exc:
        raise ProviderError(f"Cannot read {env_names[0]}") from exc
    if not data or len(data) > MAX_IDENTITY_BYTES:
        raise ProviderError(f"{env_names[0]} must contain 1..{MAX_IDENTITY_BYTES} bytes")
    return data


class LocalMfiV3Provider:
    """Stateful JSONL handler keeping key material strictly inside the helper process."""

    def __init__(self) -> None:
        self._key: ec.EllipticCurvePrivateKey | None = None
        self._certificate: bytes | None = None

    def _load(self) -> None:
        if self._key is not None:
            return
        cert = _read_env_file(("AWH_MFI_CERT_FILE", "ACCESSORY_MFI_CERT_FILE"))
        key_bytes = _read_env_file(("AWH_MFI_KEY_FILE", "ACCESSORY_MFI_KEY_FILE"))
        self._key = validate_identity(cert, key_bytes)
        self._certificate = cert

    def handle(self, request: object) -> dict[str, str]:
        if not isinstance(request, dict):
            raise ProviderError("Request must be a JSON object")
        op = request.get("op")
        if op == "certificate":
            self._load()
            assert self._certificate is not None
            return {
                "type": "mfi",
                "certificate": base64.b64encode(self._certificate).decode("ascii"),
            }
        if op != "sign":
            raise ProviderError("Operation must be 'certificate' or 'sign'")
        if self._key is None:
            raise ProviderError("Certificate must be requested before signing")

        encoded = request.get("challenge")
        if not isinstance(encoded, str):
            raise ProviderError("Challenge must be base64 text")
        try:
            digest = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ProviderError("Challenge must be valid base64") from exc
        if len(digest) != 32:
            raise ProviderError("MFi-v3 challenge must be exactly 32 bytes")

        der_sig = self._key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        r, s = utils.decode_dss_signature(der_sig)
        raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return {"signature": base64.b64encode(raw_sig).decode("ascii")}


def run_helper_stdio() -> int:
    """Run JSONL MFi-v3 signing helper over stdin/stdout (zero network access)."""
    provider = LocalMfiV3Provider()
    while True:
        line = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            break
        oversized = len(line) > MAX_REQUEST_BYTES
        try:
            if oversized:
                raise ProviderError("Request exceeds size limit")
            try:
                payload = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise ProviderError("Request must be valid JSON") from exc
            reply = provider.handle(payload)
        except ProviderError as err:
            reply = {"error": str(err)}
        except Exception:
            reply = {"error": "Local MFi signing operation failed"}
        print(json.dumps(reply, separators=(",", ":")), flush=True)
        if oversized:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(run_helper_stdio())
