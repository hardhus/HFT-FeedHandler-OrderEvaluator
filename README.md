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
| `firmware/rtl/core/` | Board-agnostic core: receive filter, MoldUDP64 de-framer, CSR block and counters; ITCH parser, book, rules and DMA engine planned |
| `firmware/rdl/` | SystemRDL register map (single source of the RTL register block and the C header) |
| `firmware/rtl/platform/` | Platform shells: simulation and out-of-context synthesis |
| `firmware/rtl/legacy/` | First prototype, kept as a read-only reference until it is replaced |
| `firmware/filelists/` | `.f` filelists shared by Verilator, cocotb and Vivado |
| `firmware/tb/` | cocotb tests (`unit/`, `system/`), shared runner (`common/`), SVA checkers (`sva/`), self-checking SV testbenches (`sv/`), legacy SV testbenches |
| `firmware/scripts/vivado/` | Vivado Tcl: project generation, OOC synthesis gate, XSIM runs |
| `firmware/constraints/` | Netlist-driven CDC constraints (`cdc.tcl`) |
| `firmware/lint/` | Verilator waivers and Verible format flags |
| `scripts/` | Bootstrap, setup and doctor scripts; `gen_csr.sh` (PeakRDL) |
| `software/` | C++ host side: early stage, to be rebuilt around the DMA ring interface |

## Stack

- **RTL:** SystemVerilog
- **Verification:** Verilator 5, cocotb 2, pytest; Verible for style lint and formatting
- **Synthesis checks (optional):** AMD Vivado (OOC timing/`report_cdc` gate, XSIM for the cross-simulator FIFO test)
- **Registers:** SystemRDL 2.0, PeakRDL (regblock, C header, Markdown)
- **Tooling:** just, uv, pinned OSS CAD Suite (Linux x86_64 and arm64)
- **Software:** C++20

## Status

- **F0, infrastructure:** cross-platform toolchain (WSL, Raspberry Pi 5, GitHub Actions x64/arm64), lint gate and
  cocotb test harness with build reuse, per-run seeds and randomized initial state.
- **F1, common blocks:** verified CDC and AXI-Stream building blocks in
  [`firmware/rtl/common/`](firmware/rtl/common/README.md): `reset_sync`, `sync_bit`, `cdc_handshake`,
  `cdc_async_fifo` (portable and XPM), `axis_register` and `axis_skid_buffer`.
  - Multi-clock tests with jittered clocks and a metastability model, plus SVA protocol checkers.
  - The portable and XPM FIFOs pass the same testbench in Verilator and XSIM.
  - Netlist-driven CDC constraints, with an OOC synthesis gate on timing and `report_cdc`.
- **F2, receive filter:** `eth_ip_udp_rx` in [`firmware/rtl/core/`](firmware/rtl/core/README.md).
  - It checks the Ethernet/IPv4/UDP headers (checksum included) against an 8-entry (IP, port) filter table that
    accepts unicast, multicast and optionally broadcast.
  - It streams the UDP payload at line rate, realigned to byte 0, with an abort flag.
  - It emits one stat event per frame with the drop reason.
  - Every frame is checked against an independent scapy-based golden model: 10⁴ random frames and a coverage gate,
    plus directed regressions for the legacy parser's bugs (sticky match, byte order, counter overflow).
  - SVA checkers are bound in every test.
  - Timing passes at 156.25 MHz in OOC synthesis.
- **F5, host registers:** a SystemRDL map generates the AXI4-Lite register block, the C header and the register
  reference ([`firmware/rtl/core/`](firmware/rtl/core/README.md#csr-block-hft_csr-hft_csr_wrap-rx_stats-mold_stats));
  CI fails if a generated file is stale.
  - The receiver's configuration is committed atomically across clock domains, and the counters are read through
    a snapshot taken in one network clock cycle.
  - Regressions for the legacy register bank's bugs (lost writes, unlatched write data, lost responses under
    backpressure) and an AXI4-Lite assertion checker.
  - A system test runs the receiver through the CSR block: 2000 random frames while software commits new
    configurations and takes snapshots, each frame checked against the model.
  - Placed and routed out of context: Kintex UltraScale+ at 156.25/250 MHz and Artix-7 at 156.25/125 MHz.
  - `just ci` is green on GitHub Actions (x64, arm64) and on a Raspberry Pi 5 (235 tests).
- **F3, MoldUDP64:** `moldudp64_rx` in [`firmware/rtl/core/`](firmware/rtl/core/README.md#moldudp64_rx).
  - It splits each packet into messages at line rate, one 64-byte message vector per cycle, for any block alignment
    (legacy: one message per packet at a fixed offset).
  - It tracks the expected sequence number: gaps, duplicates and A/B line arbitration (partial overlaps included),
    heartbeat, end of session, session check, and a software resync. One event per packet tells downstream whether
    to keep its messages, which leave cut-through before the packet is fully checked.
  - A FEED register block: configuration and resync over an atomic commit, the expected sequence number and 16
    counters in the same snapshot as the receive counters.
  - Every message and packet event is checked against a pure-Python MoldUDP64 model (10⁴ random packets with a
    coverage gate, line rate at back-to-back packets); the system test chains it with the F2 model on 2000 frames
    with A/B copies, resyncs and snapshots, and checks the sequence accounting between snapshots.
  - Placed and routed out of context at 156.25 MHz on Artix-7 and Kintex UltraScale+.
  - `just ci` is green on GitHub Actions (x64, arm64) and on a Raspberry Pi 5 (338 tests).

The legacy prototype under `firmware/rtl/legacy/` has known issues and will be replaced block by block in the following
phases.
