#!/usr/bin/env bash
# First command on a fresh Linux / WSL / Raspberry Pi 5 checkout:
#   ./scripts/bootstrap.sh && just ci
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

# curl is needed to fetch uv; minimal images (e.g. fresh WSL Debian) lack it.
need=()
for p in curl ca-certificates; do dpkg -s "$p" &>/dev/null || need+=("$p"); done
if ((${#need[@]})); then
  sudo apt-get update && sudo apt-get install -y "${need[@]}"
fi

if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
if ! command -v just >/dev/null; then
  uv tool install rust-just \
    || curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to "$HOME/.local/bin"
fi

cd "$(dirname "$0")/.."
exec just setup
