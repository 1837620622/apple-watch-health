"""Cross-platform cache/identity/runtime path management with strict local permissions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_NAME = "apple-watch-health"
CERT_FILENAME = "certificate.p7b"
KEY_FILENAME = "identity.pk8"


def project_root() -> Path:
    """Return repository/package root directory."""
    return Path(__file__).resolve().parents[2]


def sandbox_profile_path() -> Path:
    """Return path to macOS Seatbelt profile blocking IP network access."""
    return project_root() / "sandboxes" / "local-only.sb"


def data_dir() -> Path:
    """Return OS-appropriate local cache/data directory outside the repository."""
    override = os.environ.get("AWH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_NAME
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else (Path.home() / "AppData" / "Local")
        return root / APP_NAME
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg) if xdg else (Path.home() / ".cache")
    return root / APP_NAME


def identity_dir() -> Path:
    return data_dir() / "identity"


def pid_file() -> Path:
    return data_dir() / "sender.pid"


def legacy_identity_dirs() -> list[Path]:
    """Return known legacy identity directories for seamless read-only detection."""
    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.extend(
            [
                Path.home() / "Library" / "Caches" / "accessory-location" / "identity",
                Path.home() / "Library" / "Application Support" / "accessory-location" / "identity",
            ]
        )
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        candidates.append(base / "iap2-location-bridge-windows" / "identity")
    return candidates


def resolve_identity_files(
    cert_override: Path | None = None,
    key_override: Path | None = None,
) -> tuple[Path, Path] | None:
    """Locate configured or cached MFi certificate and key files."""
    if cert_override or key_override:
        if not (cert_override and key_override):
            raise ValueError("必须同时指定 --cert 和 --key")
        c, k = cert_override.expanduser().resolve(), key_override.expanduser().resolve()
        return (c, k) if c.is_file() and k.is_file() else None

    env_cert = os.environ.get("AWH_MFI_CERT_FILE") or os.environ.get("ACCESSORY_MFI_CERT_FILE")
    env_key = os.environ.get("AWH_MFI_KEY_FILE") or os.environ.get("ACCESSORY_MFI_KEY_FILE")
    if env_cert and env_key:
        c, k = Path(env_cert).expanduser().resolve(), Path(env_key).expanduser().resolve()
        if c.is_file() and k.is_file():
            return c, k

    for directory in [identity_dir(), *legacy_identity_dirs()]:
        c, k = directory / CERT_FILENAME, directory / KEY_FILENAME
        if c.is_file() and k.is_file():
            return c, k
    return None


def _windows_current_sid() -> str | None:
    """Query current Windows user SID via `whoami /user` (locale-independent)."""
    try:
        proc = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = proc.stdout.strip().splitlines()
    if not line:
        return None
    parts = [part.strip().strip('"') for part in line[0].split(",")]
    if len(parts) >= 2 and parts[1].startswith("S-1-"):
        return parts[1]
    return None


def restrict_permissions(path: Path, is_dir: bool) -> None:
    """Restrict file or directory access strictly to the current OS user."""
    if os.name == "nt":
        sid = _windows_current_sid()
        principal = f"*{sid}" if sid else os.environ.get("USERNAME", "")
        if not principal:
            return
        perm = "(OI)(CI)F" if is_dir else "F"
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:{perm}"],
            capture_output=True,
            check=False,
            timeout=15,
        )
    else:
        os.chmod(path, 0o700 if is_dir else 0o600)


def ensure_private_dir(path: Path) -> Path:
    """Create directory with owner-only permissions if absent."""
    path.mkdir(parents=True, exist_ok=True)
    restrict_permissions(path, is_dir=True)
    return path
