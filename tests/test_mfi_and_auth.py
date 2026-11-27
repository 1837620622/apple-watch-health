"""Tests for synthetic MFi-v3 identity validation, signing, and cross-platform helper execution."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

from apple_watch_health.auth import AuthenticationProvider
from apple_watch_health.mfi import (
    LocalMfiV3Provider,
    ProviderError,
    is_der_pkcs8,
    validate_identity,
)


def make_synthetic_mfi_identity() -> tuple[bytes, bytes, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic Test Accessory")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_p7b = pkcs7.serialize_certificates([cert], serialization.Encoding.DER)
    key_pk8 = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_p7b, key_pk8, key


def test_validate_identity_accepts_valid_pair_and_rejects_sec1() -> None:
    cert_p7b, key_pk8, key = make_synthetic_mfi_identity()
    assert is_der_pkcs8(key_pk8)
    loaded = validate_identity(cert_p7b, key_pk8)
    assert loaded.public_key().public_numbers() == key.public_key().public_numbers()

    sec1 = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    assert not is_der_pkcs8(sec1)
    with pytest.raises(ProviderError):
        validate_identity(cert_p7b, sec1)


def test_local_mfi_provider_signs_32byte_prehashed_challenge(monkeypatch, tmp_path) -> None:
    cert_p7b, key_pk8, key = make_synthetic_mfi_identity()
    cert_file = tmp_path / "certificate.p7b"
    key_file = tmp_path / "identity.pk8"
    cert_file.write_bytes(cert_p7b)
    key_file.write_bytes(key_pk8)
    monkeypatch.setenv("AWH_MFI_CERT_FILE", str(cert_file))
    monkeypatch.setenv("AWH_MFI_KEY_FILE", str(key_file))

    provider = LocalMfiV3Provider()
    cert_resp = provider.handle({"op": "certificate"})
    assert cert_resp["type"] == "mfi"
    assert base64.b64decode(cert_resp["certificate"]) == cert_p7b

    digest = hashlib.sha256(b"challenge-payload").digest()
    sign_resp = provider.handle({"op": "sign", "challenge": base64.b64encode(digest).decode()})
    raw_sig = base64.b64decode(sign_resp["signature"])
    assert len(raw_sig) == 64

    r = int.from_bytes(raw_sig[:32], "big")
    s = int.from_bytes(raw_sig[32:], "big")
    der_sig = utils.encode_dss_signature(r, s)
    key.public_key().verify(der_sig, digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))


async def test_auth_provider_runs_python_helper_in_path_with_spaces(tmp_path) -> None:
    spaced = tmp_path / "dir with spaces"
    spaced.mkdir()
    script = spaced / "mock helper.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    print(json.dumps({'ok': req['op']}), flush=True)\n",
        encoding="utf-8",
    )
    auth = AuthenticationProvider(str(script))
    try:
        assert await auth.request("certificate") == {"ok": "certificate"}
        assert await auth.request("sign") == {"ok": "sign"}
    finally:
        await auth.close()


async def test_auth_provider_rejects_malformed_helper_output(tmp_path) -> None:
    from apple_watch_health.wire import ProtocolError

    bad_script = tmp_path / "bad_helper.py"
    bad_script.write_text("print('not-json', flush=True)\n", encoding="utf-8")
    auth = AuthenticationProvider(str(bad_script))
    try:
        with pytest.raises(ProtocolError, match="JSON"):
            await auth.request("certificate")
    finally:
        await auth.close()
