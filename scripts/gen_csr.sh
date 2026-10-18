#!/usr/bin/env bash
# Generate the CSR outputs from firmware/rdl/hft_csr.rdl (the only place PeakRDL is called).
#   scripts/gen_csr.sh          write into the repository
#   scripts/gen_csr.sh DIR      write the same tree under DIR (used by just gen-check)
set -euo pipefail
cd "$(dirname "$0")/.."
out=${1:-.}
rdl=firmware/rdl/hft_csr.rdl
cfg=(--peakrdl-cfg firmware/rdl/peakrdl.toml)
peakrdl=(uv run --frozen peakrdl)

mkdir -p "$out/firmware/rtl/core/gen" "$out/software/include/gen" "$out/firmware/rdl"
# 16-bit address: the full 64 KiB window of the map, so the port width does not change
# when blocks are added and every unmapped address in it answers SLVERR.
"${peakrdl[@]}" regblock "$rdl" "${cfg[@]}" -o "$out/firmware/rtl/core/gen" \
  --cpuif axi4-lite-flat --addr-width 16
"${peakrdl[@]}" c-header "$rdl" "${cfg[@]}" -o "$out/software/include/gen/hft_csr.h"
"${peakrdl[@]}" markdown "$rdl" "${cfg[@]}" -o "$out/firmware/rdl/hft_csr.md"
