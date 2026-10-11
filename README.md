# HFT FeedHandler & OrderEvaluator

[![ci](https://github.com/hardhus/HFT-FeedHandler-OrderEvaluator/actions/workflows/ci.yml/badge.svg)](https://github.com/hardhus/HFT-FeedHandler-OrderEvaluator/actions/workflows/ci.yml)

An FPGA feed handler and order evaluator for NASDAQ TotalView-ITCH 5.0 over MoldUDP64. The design is hybrid. The FPGA
parses the feed, maintains state, and runs software-programmed tick-to-trade rules and pre-trade risk checks. Events
reach the host through a DMA ring buffer that the host busy-polls, and a C++ application runs the more complex
strategies. The RTL core is board-agnostic and is verified in simulation only (Verilator + cocotb); no FPGA board is
required.

## Quick Start

Linux, WSL or Raspberry Pi 5 (64-bit):

```bash
git clone https://github.com/hardhus/HFT-FeedHandler-OrderEvaluator.git
cd HFT-FeedHandler-OrderEvaluator
./scripts/bootstrap.sh && just ci
```

Windows (PowerShell): `just setup` and then `just ci`. Both run inside WSL.

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for details and [firmware/CODING_STYLE.md](firmware/CODING_STYLE.md)
for the RTL rules.

## Repository Structure

| Path | Contents |
|------|----------|
| `justfile` | Single entry point (`just` lists all recipes) |
| `firmware/rtl/common/` | Reusable building blocks (reset/CDC/AXIS helpers) |
| `firmware/rtl/core/` | Board-agnostic core (parser, book, rules, DMA engine): planned |
| `firmware/rtl/platform/` | Platform shells: simulation and out-of-context synthesis |
| `firmware/rtl/legacy/` | First prototype, kept as a read-only reference until it is replaced |
| `firmware/filelists/` | `.f` filelists shared by Verilator, cocotb and Vivado |
| `firmware/tb/` | cocotb tests (`unit/`, `system/`), shared runner (`common/`), reference models, SVA, legacy SV testbenches |
| `firmware/scripts/vivado/` | Vivado Tcl: project generation and OOC synthesis |
| `firmware/lint/` | Verilator waivers and Verible format flags |
| `scripts/` | Bootstrap, setup and doctor scripts |
| `software/` | C++ host side: early stage, to be rebuilt around the DMA ring interface |

## Stack

- **RTL:** SystemVerilog
- **Verification:** Verilator 5, cocotb 2, pytest; Verible for style lint and formatting
- **Synthesis checks (optional):** AMD Vivado (OOC timing and utilization reports, XSIM for the legacy testbench)
- **Tooling:** just, uv, pinned OSS CAD Suite (Linux x86_64 and arm64)
- **Software:** C++20

## Status

Phase F0 (infrastructure) is in place: a cross-platform toolchain, the lint gate, the cocotb test harness and a first
verified block (`reset_sync`). The legacy prototype under `firmware/rtl/legacy/` has known issues and will be replaced
block by block in the following phases.
