#!/usr/bin/env bash
# Idempotent Linux setup: apt prerequisites, pinned OSS CAD Suite + Verible, Python env.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source tools.env

case "$(uname -m)" in
  x86_64)
    PLAT=linux-x64; VARCH=x86_64
    OCS_SHA="$OSS_CAD_SUITE_SHA256_LINUX_X64"; VER_SHA="$VERIBLE_SHA256_LINUX_X64" ;;
  aarch64)
    PLAT=linux-arm64; VARCH=arm64
    OCS_SHA="$OSS_CAD_SUITE_SHA256_LINUX_ARM64"; VER_SHA="$VERIBLE_SHA256_LINUX_ARM64" ;;
  *) echo "Unsupported arch $(uname -m) (32-bit Pi OS? install the 64-bit image)"; exit 1 ;;
esac

# 1) System packages (only if missing)
# zlib1g-dev + liblz4-dev: Verilator FST tracing (--trace-fst) needs both headers
PKGS=(build-essential git curl ca-certificates python3 perl zlib1g-dev liblz4-dev)
MISSING=()
for p in "${PKGS[@]}"; do dpkg -s "$p" &>/dev/null || MISSING+=("$p"); done
if ((${#MISSING[@]})); then
  echo "Installing apt packages: ${MISSING[*]}"
  sudo apt-get update && sudo apt-get install -y "${MISSING[@]}"
fi

# fetch_tool <dest> <version-stamp> <url> <sha256> <tar strip-components>
# Downloads + verifies + extracts into .tools/<dest>; no-op if the stamp matches.
fetch_tool() {
  local dest=".tools/$1" want="$2" url="$3" sha="$4" strip="$5"
  local stamp="$dest/.hft-version" tgz=".tools/dl/${url##*/}"
  if [[ "$(cat "$stamp" 2>/dev/null)" == "$want" ]]; then
    echo "$1 $want already installed"
    return
  fi
  mkdir -p .tools/dl
  echo "Downloading ${url##*/}..."
  curl -fL -# --retry 3 -o "$tgz" "$url"
  local got
  got=$(sha256sum "$tgz" | cut -d' ' -f1)
  if [[ -z "$sha" ]]; then
    echo "NOTE: no pinned sha256 for ${url##*/}; got $got (record it in tools.env)"
  elif [[ "$sha" != "$got" ]]; then
    echo "SHA256 mismatch for ${url##*/}: expected $sha, got $got"; exit 1
  fi
  rm -rf "$dest" && mkdir -p "$dest"
  tar -xzf "$tgz" -C "$dest" --strip-components="$strip"
  echo "$want" > "$stamp"
  rm -f "$tgz"
}

# 2) Pinned tools. Only their bin/ dirs are put on PATH by the justfile; the
#    OSS CAD Suite 'environment' script is NOT sourced (its Python would shadow uv's).
fetch_tool oss-cad-suite "$PLAT-$OSS_CAD_SUITE_DATE"   "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/$OSS_CAD_SUITE_DATE/oss-cad-suite-$PLAT-${OSS_CAD_SUITE_DATE//-/}.tgz"   "$OCS_SHA" 1
fetch_tool verible "$VARCH-$VERIBLE_VERSION"   "https://github.com/chipsalliance/verible/releases/download/$VERIBLE_VERSION/verible-$VERIBLE_VERSION-linux-static-$VARCH.tar.gz"   "$VER_SHA" 1

# 3) Python env (pyproject.toml arrives in F0/S7)
if [[ -f pyproject.toml ]]; then
  uv sync --locked
else
  echo "pyproject.toml not found yet; skipping uv sync"
fi
echo "Setup done. Run: just doctor"
