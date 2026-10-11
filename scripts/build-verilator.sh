#!/usr/bin/env bash
# Fallback only: build a pinned Verilator from source into .tools/verilator
# if the OSS CAD Suite build misbehaves with cocotb. Not used in the normal flow.
#   ./scripts/build-verilator.sh [tag]    (default tag: v$VERILATOR_MIN)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source tools.env
TAG="${1:-v$VERILATOR_MIN}"

PKGS=(autoconf flex bison help2man libfl-dev build-essential git perl python3 zlib1g-dev)
MISSING=()
for p in "${PKGS[@]}"; do dpkg -s "$p" &>/dev/null || MISSING+=("$p"); done
if ((${#MISSING[@]})); then sudo apt-get update && sudo apt-get install -y "${MISSING[@]}"; fi

SRC=.tools/src/verilator
rm -rf "$SRC"
git clone --depth 1 --branch "$TAG" https://github.com/verilator/verilator.git "$SRC"
(cd "$SRC" && autoconf && ./configure --prefix="$ROOT/.tools/verilator" && make -j"$(nproc)" && make install)
echo "Installed to .tools/verilator/bin; put it before .tools/oss-cad-suite/bin on PATH to use it."
