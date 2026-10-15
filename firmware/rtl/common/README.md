# Common blocks (`firmware/rtl/common/`)

Small, verified building blocks that every later phase reuses: clock-domain crossings (CDC), reset synchronization
and AXI-Stream pipeline registers. All of them are listed in [`filelists/common.f`](../../filelists/common.f) and are
vendor-independent. The only Xilinx-specific piece, `cdc_async_fifo_xpm`, lives in
[`rtl/platform/xilinx/`](../platform/xilinx/) and is selected with `cdc_async_fifo #(.IMPL("xpm"))`.

The CDC rules that go with these blocks are in [`CODING_STYLE.md`](../../CODING_STYLE.md#clock-domain-crossing).

## Catalog

| Block | What it does | Use it for |
|-------|--------------|------------|
| `reset_sync` | Async assert, sync deassert (`STAGES` flops) | One per clock domain in the platform shell |
| `sync_bit` | `STAGES`-flop synchronizer, bits synchronized independently | Single-bit levels (enables, kill switch, status bits) and gray-coded buses; never binary multi-bit values |
| `cdc_handshake` | 4-phase req/ack transfer of one `WIDTH`-bit word | Rare multi-bit values: config shadow commit, counter snapshots, rule commit |
| `cdc_async_fifo` | Gray-pointer async FIFO, FWFT, XPM_FIFO_ASYNC port names | Streams between domains: descriptor FIFO (net → host), order mailbox (host → net) |
| `axis_register` | Forward AXI-Stream register (`tvalid`/payload registered) | Breaking long forward data paths |
| `axis_skid_buffer` | AXI-Stream register slice, both directions registered | Module boundaries where `tready` must not be combinational |

**Which CDC block?** A level that may be sampled late and is held for more than 2 destination clock periods →
`sync_bit`. A single event or a multi-bit value that changes rarely and must arrive whole → `cdc_handshake`. A stream of values, one per cycle → `cdc_async_fifo`. There is no CDC on the
tick-to-trade path; these blocks only affect the host, config and counter paths.

## Interfaces and timing

| Block | Parameters | Clock domains | Latency | Throughput | Reset |
|-------|------------|---------------|---------|------------|-------|
| `reset_sync` | `STAGES=3` | `clk` | assert immediate, deassert `STAGES` edges | – | `rst_in` async, active-high; any pulse width gives a full reset |
| `sync_bit` | `WIDTH=1`, `STAGES=2` (≥ 2) | `clk` (destination); `d` asynchronous | `STAGES` edges (+1 in jitter mode) | hold each level > 2 `clk` periods (longest, jitter included) | none (settles to `d`) |
| `cdc_handshake` | `WIDTH=32`, `STAGES=2` | `src_clk` (`src_*`), `dst_clk` (`dst_*`) | `STAGES+1` dst cycles to `dst_valid` (+1 jitter) | one word per req/ack round trip | `src_rst`, `dst_rst` asserted together |
| `cdc_async_fifo` | `WIDTH=64`, `DEPTH_LOG2=4` (≥ 4, both implementations), `SYNC_STAGES=2`, `IMPL="portable"` | `wr_clk` (`rst`, `wr_*`, `full`, `overflow`, `wr_rst_busy`), `rd_clk` (`rd_*`, `dout`, `empty`, `underflow`, `rd_rst_busy`) | write → `!empty` ≤ `SYNC_STAGES+3` rd cycles | 1 word/cycle per side (≥ 95 % of the slower clock) for `SYNC_STAGES` ≤ 3; this is why `DEPTH_LOG2` ≥ 4: a shallower FIFO is shorter than the pointer round trip | `rst` synchronous to `wr_clk`, busy handshake (below) |
| `axis_register` | `DATA_W=64` (multiple of 8), `USER_W=1` | `clk` | 1 cycle | 1 beat/cycle | `rst` sync (control only) |
| `axis_skid_buffer` | `DATA_W=64` (multiple of 8), `USER_W=1` | `clk` | 1 cycle | 1 beat/cycle | `rst` sync (control only) |

AXI-Stream ports on both AXIS blocks: `s_axis_{tdata,tkeep,tlast,tuser,tvalid,tready}` and the same with `m_axis_`.
Unused fields are tied off by the caller and trimmed by synthesis. In `axis_register`, `s_axis_tready` is
combinational from `m_axis_tready`; in `axis_skid_buffer` it comes straight from a flop.

### `cdc_async_fifo` contract

- **FWFT only.** `dout` is valid whenever `empty` is low, and `rd_en` pops it (`valid = !empty`, `ready = rd_en`).
- **Reset.** `rst` is synchronous to `wr_clk`. After power-up, hold it for at least `2*SYNC_STAGES+2` cycles of the
  slower clock; later a 1-cycle pulse is enough. The reset travels to the read side and back, and `wr_rst_busy` /
  `rd_rst_busy` stay high until both sides are clean.
- **Busy and errors.** While a busy flag is high, writes/reads are ignored. Every ignored request (busy, `full` or
  `empty`) produces one `overflow` / `underflow` pulse, so nothing is lost silently.
- **Storage.** The portable version uses an async-read array that maps to LUTRAM (`ram_style = "distributed"`).
  Deep FIFOs should use `IMPL="xpm"` (Vivado only; its FWFT capacity is one word larger).
- Both implementations pass the same self-checking testbench (`tb/sv/tb_cdc_fifo_equiv.sv`) under the same
  contract assertions (`tb/sva/fifo_props.sv`).

## Usage

```systemverilog
// Stream from the network domain to the host domain. Only write when the FIFO can accept.
cdc_async_fifo #(.WIDTH(512), .DEPTH_LOG2(5)) u_desc_fifo (
    .rst        (net_rst),      // synchronous to wr_clk
    .wr_clk     (net_clk),
    .wr_en      (desc_valid && !desc_full && !desc_wr_busy),
    .din        (desc),
    .full       (desc_full),
    .overflow   (desc_overflow), // count it: it should never pulse in normal operation
    .wr_rst_busy(desc_wr_busy),
    .rd_clk     (host_clk),
    .rd_en      (ring_ready && !desc_rd_busy),
    .dout       (ring_desc),     // valid while !empty (FWFT)
    .empty      (desc_empty),
    .underflow  (),
    .rd_rst_busy(desc_rd_busy)
);

// Config word from the host domain to the network domain; src_ready stays low until the word has landed.
cdc_handshake #(.WIDTH(64)) u_cfg_cdc (
    .src_clk(host_clk), .src_rst(host_rst), .src_data(cfg_shadow), .src_valid(cfg_commit),
    .src_ready(cfg_idle), .dst_clk(net_clk), .dst_rst(net_rst), .dst_data(cfg_net),
    .dst_valid(cfg_net_load));

// Kill switch level from the host domain.
sync_bit #(.STAGES(2)) u_kill_sync (.clk(net_clk), .d(host_kill), .q(net_kill));
```

## Verification

| Block | Tests (`firmware/tb/unit/`) | Matrix |
|-------|-----------------------------|--------|
| `reset_sync` | `test_reset_sync.py`, `tb/sv/tb_reset_sync_smoke.sv` | STAGES |
| `sync_bit` | `test_sync_bit.py` | (W1,S2), (W8,S3) × jitter × 4 clock profiles |
| `axis_register`, `axis_skid_buffer` | `test_axis_pipeline.py` (+ `axis_props` SVA) | 2 modules × DATA_W {8, 64} |
| `cdc_handshake` | `test_cdc_handshake.py` | jitter × 4 clock profiles |
| `cdc_async_fifo` | `test_cdc_async_fifo.py` (+ `fifo_props` SVA), `test_cdc_fifo_equiv.py` (Verilator), `just xsim-test` (XSIM, portable and XPM) | DEPTH_LOG2 {2, 4} × jitter × 4 clock profiles; equivalence seeds 1–3 |

Clock profiles: `same` (6.4/6.4 ns), `net2host` (6.4 → 4.0 ns), `host2net` (4.0 → 6.4 ns), `jitter` (both ±20%).
Jitter mode (`HFT_SIM_CDC_JITTER`) makes `sync_bit` capture recently changed bits one cycle late at random, which
models metastability.

## Synthesis (Artix-7 xc7a100tcsg324-1, OOC post-synthesis)

`just synth-common` synthesizes every block in both clock directions and fails on negative register-to-register slack,
a Critical `report_cdc` finding, an unexpected CDC warning or an unconstrained crossing. `net2host` is 156.25 MHz →
250 MHz (`wr`/`src`/`clk` at 156.25 MHz), `host2net` is the reverse.

| Block | LUT | FF | LUTRAM | WNS net2host / host2net (ns) | report_cdc |
|-------|-----|----|--------|------------------------------|------------|
| `reset_sync` (STAGES=3) | 0 | 3 | 0 | 5.575 / 3.175 | no crossing |
| `sync_bit` (1×2) | 0 | 2 | 0 | 5.575 / 3.175 | no crossing (`d` is an async port) |
| `axis_register` (64 b) | 3 | 75 | 0 | 3.995 / 1.595 | no crossing |
| `axis_skid_buffer` (64 b) | 79 | 150 | 0 | 4.016 / 1.616 | no crossing |
| `cdc_handshake` (32 b) | 6 | 71 | 0 | 1.869 / 1.869 | CDC-3 ×2, CDC-15 ×32 |
| `cdc_async_fifo` portable (64×16) | 35 | 112 | 99 | 0.691 / 0.739 | CDC-3 ×4, CDC-6 ×2, CDC-15 ×64 |
| `cdc_async_fifo` IMPL=xpm (64×16) | 88 | 313 | 99 | 1.159 / 0.526 | CDC-3 ×2, CDC-15 ×64 |

The FIFO rows come from the harness `rtl/platform/synth_ooc/cdc_async_fifo_ooc.sv`, which registers `dout` in the read
domain the way a real consumer would.
