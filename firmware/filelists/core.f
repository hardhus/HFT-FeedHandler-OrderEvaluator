# Core (hot-path) RTL, used with common.f. Paths are relative to this file.
# Order matters: packages first. Never combined with legacy.f (both define hft_pkg).
../rtl/core/hft_pkg.sv
../rtl/core/eth_ip_udp_rx.sv
../rtl/core/moldudp64_rx.sv
../rtl/core/mold_stats.sv
../rtl/core/symbol_filter.sv
../rtl/core/itch_decoder.sv
../rtl/core/itch_stats.sv
../rtl/core/event_packer.sv
../rtl/core/event_ring_writer.sv
