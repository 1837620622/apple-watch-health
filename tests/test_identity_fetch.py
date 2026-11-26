"""Tests for Remote-ZIP HTTP Range identity extraction, SHA-256 pinning, and atomic writes."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import zipfile

import pytest

from apple_watch_health.identity_fetch import (
    ASSETS,
    IdentityImportError,
    extract_identity_from_apk_bytes,
    fetch_identity_via_range,
    safe_destination,
    write_identity_atomic,
)
from apple_watch_health.paths import CERT_FILENAME, KEY_FILENAME, project_root

from .test_mfi_and_auth import make_synthetic_mfi_identity


def build_mock_apk(cert: bytes, key: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n")
        zf.writestr("padding.bin", os.urandom(150_000))
        zf.writestr(ASSETS[CERT_FILENAME], cert)
        zf.writestr(ASSETS[KEY_FILENAME], key)
        zf.writestr("classes.dex", b"dex\n035\0" + b"\x00" * 4096)
    return buf.getvalue()


def test_range_zip_extract_fetches_minimal_bytes_and_verifies_sha256() -> None:
    cert, key, _ = make_synthetic_mfi_identity()
    apk = build_mock_apk(cert, key)
    bytes_fetched = 0

    def fake_range_get(_url: str, start: int, end: int) -> tuple[bytes, int]:
        nonlocal bytes_fetched
        chunk = apk[start : end + 1]
        bytes_fetched += len(chunk)
        return chunk, len(apk)

    pinned = {
        CERT_FILENAME: hashlib.sha256(cert).hexdigest(),
        KEY_FILENAME: hashlib.sha256(key).hexdigest(),
    }
    extracted = fetch_identity_via_range(
        url="https://example.invalid/mock.apk",
        range_getter=fake_range_get,
        expected_size=len(apk),
        expected_hashes=pinned,
    )
    assert extracted[CERT_FILENAME] == cert
    assert extracted[KEY_FILENAME] == key
    assert bytes_fetched < len(apk)  # Did NOT download the entire archive


def test_range_zip_rejects_sha256_tampering() -> None:
    cert, key, _ = make_synthetic_mfi_identity()
    apk = build_mock_apk(cert, key)

    def fake_range_get(_url: str, start: int, end: int) -> tuple[bytes, int]:
        return apk[start : end + 1], len(apk)

    bad_hashes = {
        CERT_FILENAME: "0" * 64,
        KEY_FILENAME: hashlib.sha256(key).hexdigest(),
    }
    with pytest.raises(IdentityImportError, match="SHA-256"):
        fetch_identity_via_range(
            url="https://example.invalid/mock.apk",
            range_getter=fake_range_get,
            expected_size=len(apk),
            expected_hashes=bad_hashes,
        )


def test_full_apk_extract_and_atomic_private_write(tmp_path) -> None:
    cert, key, _ = make_synthetic_mfi_identity()
    apk = build_mock_apk(cert, key)
    apk_sha = hashlib.sha256(apk).hexdigest()

    extracted = extract_identity_from_apk_bytes(apk, expected_apk_sha256=apk_sha)
    dest = tmp_path / "identity"
    written = write_identity_atomic(extracted, destination=dest, force=True)
    assert written == dest.resolve()
    assert (dest / CERT_FILENAME).read_bytes() == cert
    assert (dest / KEY_FILENAME).read_bytes() == key

    if os.name != "nt":
        assert stat.S_IMODE(dest.stat().st_mode) == 0o700
        assert stat.S_IMODE((dest / KEY_FILENAME).stat().st_mode) == 0o600


def test_refuses_saving_credentials_inside_repo_or_symlink(tmp_path) -> None:
    with pytest.raises(IdentityImportError, match="仓库"):
        safe_destination(project_root() / "leaked-identity")

    link = tmp_path / "sym"
    try:
        link.symlink_to(tmp_path / "target", target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not permitted in this environment")
    with pytest.raises(IdentityImportError, match="符号链接"):
        safe_destination(link)
