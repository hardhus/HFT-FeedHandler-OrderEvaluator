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
# --cpuif passthrough: the AXI4-Lite side is axil_cpuif2 in hft_csr_regs, which gives both blocks their request
# from flops (A40: the generated AXI4-Lite interface arbitrated AR/AW combinationally into the read mux).
# --rt-read-fanin: one register stage inside the read mux (read latency +1 cycle). Without it the
# read path missed timing after place & route on the A7 at 125 MHz (-0.462 ns, 80 % routing).
# Two register blocks with the same options: hft_csr (the whole map, the RDL's last addrmap, so the tools
# below take it without --top) and hft_order_csr (RISK, STRATEGY, TX at their absolute addresses, decision 41).
for top in hft_csr hft_order_csr; do
  "${peakrdl[@]}" regblock "$rdl" "${cfg[@]}" --top "$top" -o "$out/firmware/rtl/core/gen" \
    --cpuif passthrough --addr-width 16 --rt-read-fanin
done
"${peakrdl[@]}" c-header "$rdl" "${cfg[@]}" -o "$out/software/include/gen/hft_csr.h"
"${peakrdl[@]}" markdown "$rdl" "${cfg[@]}" -o "$out/firmware/rdl/hft_csr.md"
