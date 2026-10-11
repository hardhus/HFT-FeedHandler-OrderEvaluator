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
- Every signal that crosses a clock domain goes through a CDC block from `rtl/common/`. No ad hoc
  double-flops in leaf modules.

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
