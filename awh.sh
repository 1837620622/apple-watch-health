#!/usr/bin/env bash
# Zero-prerequisite macOS/Linux launcher for apple-watch-health (`awh`).
# Auto-detects system uv, or bootstraps standalone uv into local cache (no brew/sudo/Python required).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${AWH_DATA_DIR:-$HOME/Library/Caches/apple-watch-health}"
[ "$(uname)" = "Darwin" ] || DATA_DIR="${AWH_DATA_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/apple-watch-health}"

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  for candidate in \
    "$DATA_DIR/bin/uv" \
    "$HOME/.local/bin/uv" \
    "/opt/homebrew/bin/uv" \
    "/usr/local/bin/uv" \
    "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if ! UV_BIN="$(find_uv)"; then
  echo "==> 未检测到 uv，正在免配置自动安装独立版 uv 到本地缓存 ($DATA_DIR/bin)..." >&2
  mkdir -p "$DATA_DIR/bin"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$DATA_DIR/bin" sh >&2
  UV_BIN="$DATA_DIR/bin/uv"
  [ -x "$UV_BIN" ] || { echo "错误: 自动安装 uv 失败，请检查网络或手动执行 brew install uv" >&2; exit 1; }
fi

if [ -n "${AWH_PYPI_MIRROR:-}" ] && [ -z "${UV_DEFAULT_INDEX:-}" ]; then
  export UV_DEFAULT_INDEX="$AWH_PYPI_MIRROR"
fi

exec "$UV_BIN" run --project "$ROOT" awh "$@"
