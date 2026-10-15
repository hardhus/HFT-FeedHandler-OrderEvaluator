# Firmware coding style

These rules apply to new RTL under `firmware/rtl/{common,core,platform}`. Code in `firmware/rtl/legacy/` is a
read-only reference and is replaced phase by phase; do not copy its patterns.

`just lint` enforces most of this: Verilator `-Wall`, Verible style rules from
[`.rules.verible_lint`](../.rules.verible_lint) and the formatter check. A lint failure blocks CI. Waivers in
[`lint/waivers.vlt`](lint/waivers.vlt) need a one-line reason.

## Files

- One module per file. The file name equals the module name (`reset_sync.sv` → `module reset_sync`). Lint and
  filelist automation depend on this.
- Every RTL file is listed in a filelist under `firmware/filelists/`. Paths in a `.f` file are relative to the
  `.f` file itself. Packages come before their users.
- Packages are imported inside the module (`module m import foo_pkg::*; (...)`), never at file (`$unit`) scope.
- A package (`*_pkg.sv`) comes first in its filelist and is not linted as a Verilator top of its own; Verilator
  checks it through the modules that import it. Every constant in it must have a user (an unused one fails lint),
  so a constant is added together with its first user.

## Header

Every module starts with a short header comment:

```systemverilog
// <name>: <one-line purpose>.
// Clock domain : <clk name(s)>
// Latency      : <cycles, input to output>
// Throughput   : <e.g. 1 beat/cycle>   (omit if not meaningful)
```

## Reset and clocking

- Reset is active-high and named `rst` (or `rst_<domain>`).
- Control flops (FSM state, valid bits, counters) use a synchronous reset. Datapath registers are not reset.
- **Exception:** `reset_sync` uses an asynchronous assert and a synchronous deassert, as its definition requires.
  It is the only module allowed to have an async reset.

## Clock domain crossing

The blocks are described in [`rtl/common/README.md`](rtl/common/README.md).

- Every signal that crosses a clock domain goes through a CDC block from `rtl/common/` (`sync_bit`,
  `cdc_handshake`, `cdc_async_fifo`). No ad hoc double-flops in leaf modules.
- Synchronizer flops carry `(* ASYNC_REG = "TRUE" *)` and have no reset. Only `sync_bit` and `reset_sync`
  declare them.
- A multi-bit value crosses only as a gray code (FIFO pointers, through `sync_bit`), through `cdc_handshake`, or
  through `cdc_async_fifo`. Never pass a binary bus through `sync_bit`.
- A level passed through `sync_bit` must stay stable for more than 2 destination clock periods; a shorter pulse may
  be lost. Send events (one-cycle pulses) through `cdc_handshake`.
- A register that is read in another domain without a synchronizer, because a protocol keeps it stable, is marked
  `(* hft_cdc_data = "TRUE" *)` (for example `cdc_handshake.data_q`). FIFO storage is async-read LUTRAM
  (`ram_style = "distributed"`).
- CDC timing constraints are not written by hand. [`constraints/cdc.tcl`](constraints/cdc.tcl) derives them from
  the netlist (`ASYNC_REG` chains, `hft_cdc_data` cells, LUTRAM read across clocks). Do not use
  `set_clock_groups -asynchronous`, which would override them.
- `cdc_async_fifo` reset contract: `rst` is synchronous to `wr_clk`; no `wr_en` / `rd_en` while
  `wr_rst_busy` / `rd_rst_busy` is high; hold `rst` for at least `2*SYNC_STAGES+2` slow-clock cycles after power-up.
- `cdc_handshake`: `src_rst` and `dst_rst` come from one reset source (one `reset_sync` per domain).
- Every new CDC use must pass `just synth-common`-style checks: `report_cdc` without Critical findings and without an
  unconstrained crossing.

## Ports and naming

- Module ports do not use SystemVerilog `interface`s. Use plain ports with the AXI naming convention:
  `s_axis_tdata`, `s_axis_tvalid`, `m_axi_awaddr`, … AXI-Stream byte 0 is `tdata[7:0]`.
- Signals are `lower_snake_case`. Parameters and localparams are `UPPER_SNAKE_CASE`. Types end in `_t`.
- Register outputs may use a `_q` suffix and their next-state signal a `_d` suffix. This is recommended, not
  enforced. Verible's `port-name-suffix` and `dff-name-style` are disabled because they conflict with the AXI
  names. `instance-shadowing` is also disabled, because it flags ordinary named connections such as `.clk(clk)`.
  Still, do not reuse an outer name for an inner declaration.

## Processes

- Use `always_ff` for flops and `always_comb` for combinational logic. Never use plain `always`.
- No `automatic` variables inside `always_ff`. No latches: every `always_comb` assigns all outputs on all paths.
- `if`/`else` bodies always use `begin`/`end` (Verible `explicit-begin`).

## Simulation-only code

- Synthesizable RTL has no `$display`.
- Assertions go inside `` `ifndef SYNTHESIS `` blocks or in a separate SVA bind file under `tb/sva/`.

## Comments and formatting

- Code comments are in English.
- Formatting is Verible's, with the flags in [`lint/verible-format.flags`](lint/verible-format.flags):
  100-column limit and aligned ports, parameters and assignments. Run `just fmt`. Editors should use the same
  flags.

## Tests (cocotb)

- Tests live in `firmware/tb/unit/test_<module>.py` (unit) or `firmware/tb/system/` (system).
- cocotb coroutines are named `tc_*`, so pytest does not collect them. The pytest entry point
  `test_<module>()` calls `common.runner.run_cocotb(...)` and is parametrized over the module's parameters.
- Every bug fix comes with a test that fails without the fix.
- A reference model must not reuse the RTL's offsets or layout. It derives fields from an independent source
  (e.g. scapy's parse), and its own tests use hand-written vectors. A model that copies the RTL's assumptions
  passes the same bugs.
- Required coverage bins are a gate: an empty bin fails the test. A bin is removed only with a written reason,
  never to make a test pass.
- Tests are never loosened or trimmed (fewer frames, smaller ranges, weaker checks) to pass or to save time. A
  test changes only when the test itself is wrong. A planned negative check (mutation, violation) that does not
  bite is explained and replaced or strengthened.
- A reference model only guards the inputs a test actually sends. Every counter, saturation, floor and width limit
  therefore gets a directed test at and beyond the limit. Example: the largest legal IPv4 datagram in
  `tc_size_limits`.
- A rewrite that must keep behaviour (timing, cleanup) is checked against the previous RTL: run both in lockstep on
  the same stimulus, or compare the rewritten formulas over their whole input range.
- A test bound equals the documented bound (latency, capacity, throughput), never one cycle looser.
- A bound in a module header is a requirement derived from the block's users (the target architecture and
  phase plans), not a measurement. When a test shows the requirement does not hold for some parameters, the
  allowed parameter range is narrowed (or the RTL fixed). A bound is never lowered to whatever was measured.
  Measured values may be noted, marked as measured.
