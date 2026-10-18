# CSR block (F5), used with common.f and core.f. Paths are relative to this file.
# Files under gen/ are generated from firmware/rdl/hft_csr.rdl (just gen) and never edited.
# Order matters: packages first.
../rtl/core/gen/hft_csr_pkg.sv
../rtl/core/gen/hft_csr.sv
../rtl/core/rx_stats.sv
../rtl/core/hft_csr_wrap.sv
