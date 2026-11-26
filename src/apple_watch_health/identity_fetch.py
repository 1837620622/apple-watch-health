"""Fast, cryptographically pinned local MFi identity importer (macOS & Windows).

Features:
- Default fast mode: HTTP Range Remote-ZIP fetch (~68 KB, ~1-2s even on slow links!)
  with exact pinned SHA-256 verification on BOTH extracted files plus P-256 key match.
- Optional `--full-apk` mode: true 8-thread concurrent download with HTTP status validation
  and full APK SHA-256 verification.
- Offline `--apk <path>` and custom `--cert <path> --key <path>` modes.
- Never bundles or commits credential files into the repository; writes atomically
  with owner-only permissions (0700/0600 on POSIX, SID-based icacls ACL on Windows).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import struct
import tempfile
import urllib.error
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .mfi import MAX_IDENTITY_BYTES, validate_identity
from .paths import (
    CERT_FILENAME,
    KEY_FILENAME,
    ensure_private_dir,
    identity_dir,
    project_root,
    restrict_permissions,
)

# Pinned DiPlay v0.2.7 release metadata (never installed or executed).
DIPLAY_ASSET_API = "https://api.github.com/repos/shihabal3amri/DiPlay/releases/assets/598772337"
DIPLAY_DIRECT_URL = (
    "https://github.com/shihabal3amri/DiPlay/releases/download/v0.2.7/DiPlay-0.2.7.apk"
)
DIPLAY_APK_SIZE = 32_817_385
DIPLAY_APK_SHA256 = "27b434462da7e78b1ff0987d129ed0a4b0a0e56beceaf8ec221d47150d4675bd"
MAX_APK_BYTES = 64 * 1024 * 1024

ASSETS: dict[str, str] = {
    CERT_FILENAME: "assets/offline-mfi/certificate.p7b",
    KEY_FILENAME: "assets/offline-mfi/identity.pk8",
}

PINNED_ASSET_SHA256: dict[str, str] = {
    CERT_FILENAME: "634a93dd6c4338080524025411752597ecb828591839cd062a8292fd7e9e844e",
    KEY_FILENAME: "bd50eda2d8dd95a8464440f1aebca7068dcab46a2461ecaf826c7f98f5621a75",
}

NOTICE_URL = (
    "https://github.com/shihabal3amri/DiPlay/blob/main/"
    "docs/THIRD_PARTY_NOTICES.md#experimental-authentication-data"
)

_TAIL_PROBE_BYTES = 65_557  # Max EOCD (22 + 65535 comment)
_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+)$", re.IGNORECASE)


class IdentityImportError(Exception):
    """Raised when downloading, verifying, or writing identity material fails."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True, slots=True)
class ZipEntryMeta:
    filename: str
    compress_type: int
    crc32: int
    compress_size: int
    file_size: int
    header_offset: int


def _github_headers(accept: str | None = None) -> dict[str, str]:
    headers = {"User-Agent": "apple-watch-health/2.0"}
    if accept:
        headers["Accept"] = accept
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def resolve_download_url(timeout: float = 20.0) -> str:
    """Resolve signed HTTPS CDN URL via GitHub API (China-friendly) or direct release URL."""
    opener = urllib.request.build_opener(_NoRedirect)
    candidates = [
        (DIPLAY_ASSET_API, "application/octet-stream"),
        (DIPLAY_DIRECT_URL, None),
    ]
    errors: list[str] = []
    for url, accept in candidates:
        req = urllib.request.Request(url, headers=_github_headers(accept))
        try:
            with opener.open(req, timeout=timeout) as resp:
                final_url = resp.geturl()
                if final_url.startswith("https://"):
                    return final_url
        except urllib.error.HTTPError as err:
            if err.code in (301, 302, 303, 307, 308):
                loc = err.headers.get("Location", "")
                if loc.startswith("https://"):
                    return loc
                errors.append(f"{url}: non-HTTPS redirect")
            else:
                errors.append(f"{url}: HTTP {err.code}")
        except (OSError, urllib.error.URLError) as err:
            errors.append(f"{url}: {err}")
    raise IdentityImportError(f"无法获取 DiPlay 发布包下载地址 ({'; '.join(errors)})")


def _http_range_get(url: str, start: int, end: int, timeout: float = 30.0) -> tuple[bytes, int]:
    """Fetch inclusive byte range [start, end] over HTTPS and return (data, total_size)."""
    if not url.startswith("https://"):
        raise IdentityImportError("Refused non-HTTPS download URL")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "apple-watch-health/2.0",
            "Range": f"bytes={start}-{end}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if not resp.geturl().startswith("https://"):
            raise IdentityImportError("Download redirected outside HTTPS")
        if resp.status != 206:
            raise IdentityImportError(f"服务器不支持 Range 分段请求 (HTTP {resp.status})")
        cr = resp.headers.get("Content-Range", "")
        match = _CONTENT_RANGE_RE.match(cr.strip())
        if not match:
            raise IdentityImportError(f"无效的 Content-Range 响应头: {cr!r}")
        total = int(match.group(3))
        data = resp.read()
        expected = end - start + 1
        if len(data) != expected:
            raise IdentityImportError(f"Range 数据长度不符: 期望 {expected}，实收 {len(data)}")
        return data, total


def parse_eocd(tail: bytes, tail_start_offset: int) -> tuple[int, int]:
    """Locate ZIP End-of-Central-Directory record and return (cd_offset, cd_size)."""
    pos = tail.rfind(b"PK\x05\x06")
    if pos == -1 or pos + 22 > len(tail):
        raise IdentityImportError("未找到有效的 ZIP EOCD 记录")
    _, _, _, _, cd_size, cd_offset, comment_len = struct.unpack_from("<HHHHIIH", tail, pos + 4)
    if pos + 22 + comment_len > len(tail):
        raise IdentityImportError("ZIP EOCD 注释长度异常")
    if cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF:
        raise IdentityImportError("不支持 ZIP64 偏移")
    if cd_offset + cd_size > tail_start_offset + pos:
        raise IdentityImportError("ZIP 中央目录边界异常")
    return cd_offset, cd_size


def parse_central_directory(cd_bytes: bytes, wanted: set[str]) -> dict[str, ZipEntryMeta]:
    """Parse ZIP Central Directory entries matching `wanted` paths."""
    found: dict[str, ZipEntryMeta] = {}
    counts: dict[str, int] = {}
    offset = 0
    total = len(cd_bytes)
    while offset < total:
        if offset + 46 > total or cd_bytes[offset : offset + 4] != b"PK\x01\x02":
            raise IdentityImportError("ZIP 中央目录格式损坏")
        (
            compress_type,
            crc32,
            compress_size,
            file_size,
            name_len,
            extra_len,
            comment_len,
            header_offset,
        ) = struct.unpack_from("<HxxxxIIIHHHxxxxxxxxI", cd_bytes, offset + 10)
        name_start = offset + 46
        name_end = name_start + name_len
        if name_end > total:
            raise IdentityImportError("ZIP 条目名截断")
        name = cd_bytes[name_start:name_end].decode("utf-8", "replace")
        offset = name_end + extra_len + comment_len
        if name in wanted:
            counts[name] = counts.get(name, 0) + 1
            if counts[name] > 1:
                raise IdentityImportError(f"APK 存在重复条目: {name}")
            if not 1 <= file_size <= MAX_IDENTITY_BYTES or compress_size > MAX_IDENTITY_BYTES * 2:
                raise IdentityImportError(f"身份文件大小超出限制: {name}")
            found[name] = ZipEntryMeta(
                filename=name,
                compress_type=compress_type,
                crc32=crc32,
                compress_size=compress_size,
                file_size=file_size,
                header_offset=header_offset,
            )
    return found


def extract_entry_from_local_bytes(buf: bytes, meta: ZipEntryMeta) -> bytes:
    """Parse a ZIP Local File Header and decompress + CRC-verify the payload."""
    if len(buf) < 30 or buf[:4] != b"PK\x03\x04":
        raise IdentityImportError(f"ZIP 本地文件头损坏: {meta.filename}")
    name_len, extra_len = struct.unpack_from("<HH", buf, 26)
    data_start = 30 + name_len + extra_len
    data_end = data_start + meta.compress_size
    if data_end > len(buf):
        raise IdentityImportError(f"ZIP 条目数据不足: {meta.filename}")
    compressed = buf[data_start:data_end]
    if meta.compress_type == 0:
        raw = compressed
    elif meta.compress_type == 8:
        raw = zlib.decompress(compressed, -zlib.MAX_WBITS, MAX_IDENTITY_BYTES + 1)
    else:
        raise IdentityImportError(f"不支持的 ZIP 压缩方式: {meta.compress_type}")
    if len(raw) != meta.file_size or len(raw) > MAX_IDENTITY_BYTES:
        raise IdentityImportError(f"解压后大小不符: {meta.filename}")
    if (zlib.crc32(raw) & 0xFFFFFFFF) != meta.crc32:
        raise IdentityImportError(f"CRC-32 校验失败: {meta.filename}")
    return raw


def fetch_identity_via_range(
    url: str | None = None,
    *,
    range_getter: Callable[[str, int, int], tuple[bytes, int]] | None = None,
    expected_size: int = DIPLAY_APK_SIZE,
    expected_hashes: dict[str, str] | None = None,
) -> dict[str, bytes]:
    """Fetch only the two MFi files via HTTP Range (~68 KB) with strict SHA-256 pinning."""
    getter = range_getter or _http_range_get
    target_url = url or resolve_download_url()
    pinned = expected_hashes or PINNED_ASSET_SHA256

    _, total_size = getter(target_url, 0, 0)
    if total_size != expected_size:
        raise IdentityImportError(f"远端 APK 大小不符: 期望 {expected_size}，实际 {total_size}")

    tail_start = max(0, total_size - _TAIL_PROBE_BYTES)
    tail, _ = getter(target_url, tail_start, total_size - 1)
    cd_offset, cd_size = parse_eocd(tail, tail_start)

    if cd_offset >= tail_start:
        cd_bytes = tail[cd_offset - tail_start : cd_offset - tail_start + cd_size]
    else:
        cd_bytes, _ = getter(target_url, cd_offset, cd_offset + cd_size - 1)

    metas = parse_central_directory(cd_bytes, set(ASSETS.values()))
    if len(metas) != len(ASSETS):
        missing = sorted(set(ASSETS.values()) - set(metas))
        raise IdentityImportError(f"APK 缺少目标身份文件: {', '.join(missing)}")

    # Coalesce adjacent entries into a single HTTP Range fetch when within 64 KB of each other.
    ordered = sorted(metas.values(), key=lambda m: m.header_offset)
    span_start = ordered[0].header_offset
    span_end = max(m.header_offset + 30 + len(m.filename) + 2048 + m.compress_size for m in ordered)
    span_end = min(total_size - 1, span_end)

    extracted_by_asset: dict[str, bytes] = {}
    if span_end - span_start <= 64 * 1024:
        chunk, _ = getter(target_url, span_start, span_end)
        for meta in ordered:
            rel = meta.header_offset - span_start
            extracted_by_asset[meta.filename] = extract_entry_from_local_bytes(chunk[rel:], meta)
    else:
        for meta in ordered:
            end = min(
                total_size - 1,
                meta.header_offset + 30 + len(meta.filename) + 2048 + meta.compress_size,
            )
            chunk, _ = getter(target_url, meta.header_offset, end)
            extracted_by_asset[meta.filename] = extract_entry_from_local_bytes(chunk, meta)

    result: dict[str, bytes] = {}
    for out_name, asset_path in ASSETS.items():
        blob = extracted_by_asset[asset_path]
        digest = hashlib.sha256(blob).hexdigest()
        if digest != pinned[out_name]:
            raise IdentityImportError(
                f"{out_name} SHA-256 校验失败（实际 {digest[:16]}...，与固定版本不符）"
            )
        result[out_name] = blob

    validate_identity(result[CERT_FILENAME], result[KEY_FILENAME])
    return result


def extract_identity_from_apk_bytes(
    archive_bytes: bytes,
    *,
    expected_apk_sha256: str = DIPLAY_APK_SHA256,
) -> dict[str, bytes]:
    """Verify full APK SHA-256 and extract + validate the two MFi identity assets."""
    if len(archive_bytes) > MAX_APK_BYTES:
        raise IdentityImportError("APK 超过大小限制")
    actual_sha = hashlib.sha256(archive_bytes).hexdigest()
    if actual_sha != expected_apk_sha256:
        raise IdentityImportError(
            f"APK SHA-256 不匹配（期望 {expected_apk_sha256[:16]}...，实际 {actual_sha[:16]}...）"
        )
    extracted: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        names = zf.namelist()
        for out_name, asset_path in ASSETS.items():
            if names.count(asset_path) != 1:
                raise IdentityImportError(f"APK 必须且仅包含一个 {asset_path}")
            info = zf.getinfo(asset_path)
            if not 1 <= info.file_size <= MAX_IDENTITY_BYTES:
                raise IdentityImportError(f"身份文件大小超限: {asset_path}")
            with zf.open(info) as entry:
                data = entry.read(MAX_IDENTITY_BYTES + 1)
            if not data or len(data) > MAX_IDENTITY_BYTES:
                raise IdentityImportError(f"身份文件大小超限: {asset_path}")
            extracted[out_name] = data
    validate_identity(extracted[CERT_FILENAME], extracted[KEY_FILENAME])
    return extracted


def download_full_apk_concurrent(
    url: str | None = None,
    *,
    workers: int = 8,
    expected_size: int = DIPLAY_APK_SIZE,
    on_progress: Callable[[int, int], None] | None = None,
) -> bytes:
    """True concurrent multi-segment HTTP Range download for `--full-apk` mode."""
    target_url = url or resolve_download_url()
    _, total = _http_range_get(target_url, 0, 0)
    if total != expected_size:
        raise IdentityImportError(f"远端 APK 大小不符: 期望 {expected_size}，实际 {total}")

    seg_size = (total + workers - 1) // workers
    ranges = [
        (i, i * seg_size, min(total - 1, (i + 1) * seg_size - 1))
        for i in range(workers)
        if i * seg_size < total
    ]
    parts: dict[int, bytes] = {}
    done_bytes = 0

    def fetch_seg(idx: int, start: int, end: int) -> tuple[int, bytes]:
        last_err: Exception | None = None
        current_url = target_url
        for attempt in range(4):
            try:
                chunk, _ = _http_range_get(current_url, start, end, timeout=120.0)
                return idx, chunk
            except urllib.error.HTTPError as err:
                last_err = err
                if err.code in (403, 410) and url is None and attempt < 3:
                    current_url = resolve_download_url()
            except Exception as err:
                last_err = err
        raise IdentityImportError(f"分片 {idx} 下载失败: {last_err}")

    with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
        futures = [pool.submit(fetch_seg, idx, s, e) for idx, s, e in ranges]
        for fut in as_completed(futures):
            idx, data = fut.result()
            parts[idx] = data
            done_bytes += len(data)
            if on_progress:
                on_progress(done_bytes, total)

    return b"".join(parts[i] for i in range(len(ranges)))


def safe_destination(dest: Path) -> Path:
    """Reject symlink destinations or paths inside the source repository."""
    expanded = dest.expanduser()
    if expanded.is_symlink():
        raise IdentityImportError("身份目录不能是符号链接")
    resolved = expanded.resolve()
    repo_root = project_root().resolve()
    if resolved == repo_root or resolved.is_relative_to(repo_root):
        raise IdentityImportError("禁止将 MFi 身份文件保存在代码仓库目录内")
    return resolved


def write_identity_atomic(
    identity: dict[str, bytes],
    destination: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    """Atomically save verified identity files with owner-only OS permissions."""
    target = safe_destination(destination or identity_dir())
    if target.exists() and not force:
        cert_ok = (target / CERT_FILENAME).is_file()
        key_ok = (target / KEY_FILENAME).is_file()
        if cert_ok and key_ok:
            return target

    ensure_private_dir(target.parent)
    staging = Path(tempfile.mkdtemp(prefix=".identity-stage-", dir=str(target.parent)))
    try:
        restrict_permissions(staging, is_dir=True)
        for filename in (CERT_FILENAME, KEY_FILENAME):
            out = staging / filename
            if os.name == "nt":
                out.write_bytes(identity[filename])
                restrict_permissions(out, is_dir=False)
            else:
                fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(identity[filename])
        if target.exists():
            backup = target.parent / f".identity-old-{os.getpid()}"
            if backup.exists():
                shutil.rmtree(backup)
            target.rename(backup)
            try:
                staging.rename(target)
            except Exception:
                backup.rename(target)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            staging.rename(target)
        restrict_permissions(target, is_dir=True)
        return target
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def import_local_pair(cert_path: Path, key_path: Path, destination: Path | None = None) -> Path:
    """Validate and copy a user-supplied local certificate + private key pair."""
    cert_bytes = cert_path.expanduser().read_bytes()
    key_bytes = key_path.expanduser().read_bytes()
    validate_identity(cert_bytes, key_bytes)
    return write_identity_atomic(
        {CERT_FILENAME: cert_bytes, KEY_FILENAME: key_bytes},
        destination=destination,
        force=True,
    )
