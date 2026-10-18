# Core blocks (`firmware/rtl/core/`)

The hot path from the MAC to the order logic, and the host register interface that configures and counts it.
The hot-path blocks run in the network clock domain (`net_clk`, 156.25 MHz) at line rate, without backpressure;
they are listed in [`filelists/core.f`](../../filelists/core.f), after `hft_pkg`, with their assertions in
[`filelists/sva_core.f`](../../filelists/sva_core.f) and their OOC synthesis harnesses in
[`filelists/synth_ooc_core.f`](../../filelists/synth_ooc_core.f). The CSR blocks are in
[`filelists/csr.f`](../../filelists/csr.f) (assertions [`sva_csr.f`](../../filelists/sva_csr.f), harness
[`synth_ooc_csr.f`](../../filelists/synth_ooc_csr.f)). The design decisions are recorded in the F2 and F5 plans
(audit 09, 10); this file is the contract.

## Catalog

| Block | What it does | Phase |
|-------|--------------|-------|
| `hft_pkg` | Protocol constants, big-endian lane helpers `be16`/`be32`, receive reason codes `rx_reason_e` | F2 |
| `eth_ip_udp_rx` | Ethernet/IPv4/UDP receive filter: header checks, 8-entry (IP, port) filter table, UDP payload realigned to byte 0 | F2 |
| `gen/hft_csr` | AXI4-Lite register block, generated from `firmware/rdl/hft_csr.rdl` (PeakRDL) | F5 |
| `rx_stats` | Receive counters: packets, bytes, one drop counter per reason (64 bit) | F5 |
| `hft_csr_wrap` | `hft_csr` plus the transfers between host and net clock: atomic config commit, counter snapshot | F5 |

## `eth_ip_udp_rx`

### Ports

| Port | Width | Meaning |
|------|-------|---------|
| `s_axis_rx_*` | 64-bit AXIS | From the MAC. Byte 0 = `tdata[7:0]`; `tkeep` full before `tlast`, contiguous from lane 0 on it; `tuser` = bad frame (FCS), valid on the `tlast` beat only. `s_axis_rx_tready` is always 1 |
| `cfg_rx_en` | 1 | Receive enable, latched at the SOF beat |
| `cfg_local_mac` | 48 | Own unicast MAC |
| `cfg_accept_bcast` | 1 | Accept `ff:ff:ff:ff:ff:ff` |
| `cfg_filter_{ip,port,en}` | `N_FILTERS` × 32 / 16 / 1 | (dst IP, dst port) filter table, `N_FILTERS = 8` |
| `ts_now` | 64 | Free-running timestamp (from `timestamp_counter`, F8) |
| `m_axis_udp_*` | 64-bit AXIS | UDP payload, byte 0 = `tdata[7:0]`; no `tready` (line rate). `tuser` on the last beat = abort |
| `m_udp_sof_ts`, `m_udp_src_ip`, `m_udp_dst_ip`, `m_udp_dst_port`, `m_udp_len`, `m_udp_filter_idx` | 64, 32, 32, 16, 16, `FIDX_W` | Per-packet metadata. `m_udp_len` = payload bytes (UDP length − 8) |
| `stat_valid`, `stat_reason`, `stat_bytes` | 1, `rx_reason_e`, 16 | One event per frame; `stat_bytes` = frame length, saturating |

The receiver decides only through the filter table: there is no `cfg_local_ip`. A unicast feed puts its own IP in
one entry. Multicast frames are accepted at the MAC when the MAC address is the RFC 1112 mapping of the dst IP; the
filter table then picks the groups. VLAN-tagged frames are dropped (`ETHERTYPE`).

### Reason codes and priority

Each frame gives exactly one stat event, one cycle after its `tlast` beat. When several checks fail, the first
one in this order is reported. `stat_reason` is the counter index of the F5 counter bank.

| Order | Code | `rx_reason_e` | Condition | Payload out? |
|-------|------|---------------|-----------|--------------|
| 1 | 1 | `RX_DROP_DISABLED` | `cfg_rx_en` = 0 at the SOF beat | no |
| 2 | 2 | `RX_DROP_FCS` | `s_axis_rx_tuser` on the `tlast` beat | if the headers pass, with `tuser` = 1 |
| 3 | 9 | `RX_DROP_LEN` | frame < 42 B: headers incomplete | no |
| 4 | 3 | `RX_DROP_MAC` | dst MAC is not own, accepted broadcast, or the multicast MAC of dst IP | no |
| 5 | 4 | `RX_DROP_ETHERTYPE` | EtherType ≠ 0x0800 (VLAN 0x8100 included) | no |
| 6 | 5 | `RX_DROP_IP_HDR` | version ≠ 4, IHL ≠ 5, or wrong header checksum | no |
| 7 | 6 | `RX_DROP_FRAG` | MF = 1 or fragment offset ≠ 0 | no |
| 8 | 7 | `RX_DROP_PROTO` | protocol ≠ 17 | no |
| 9 | 8 | `RX_DROP_FILTER` | no enabled entry matches (dst IP, dst port) | no |
| 10 | 9 | `RX_DROP_LEN` | UDP length ≠ IP length − 20, or UDP length ≤ 8 | no |
| 11 | 9 | `RX_DROP_LEN` | runt (< 60 B), truncated (< 14 + IP length), or bytes after the datagram in a frame longer than 60 B | yes, with `tuser` = 1 |
| – | 0 | `RX_OK` | everything passed | yes |

When several entries match, the lowest index is `m_udp_filter_idx`. A 60-B frame may carry padding after the
datagram; MoldUDP64 packets (≥ 62 B) never do. The UDP checksum is not checked.

### Beat timeline and latency

| Beat | Bytes | Used |
|------|-------|------|
| 0 (SOF) | 0–7 | dst MAC; `ts_now` and `cfg_rx_en` latched |
| 1 | 8–15 | EtherType, version/IHL |
| 2 | 16–23 | IP length, flags/fragment, protocol |
| 3 | 24–31 | src IP, dst IP upper half |
| 4 | 32–39 | dst IP lower half, dst port, UDP length. **Stage A**: every `cfg_*` except `cfg_rx_en` is read in this cycle |
| 5 | 40–47 | UDP checksum, payload bytes 0–5. **Stage B** (cycle after stage A): header verdict, metadata |
| 6 + j | … | output beat j = `{beat 6+j [15:0], beat 5+j [63:16]}` |

Latency (no `tvalid` gaps), checked with exact equality by `tc_latency`:
- SOF beat → first payload beat: **7 cycles**. Exception: when the first beat is also the last one and the frame
  is padded, that beat is held for the input `tlast` (below) and comes 1 cycle after it.
- input `tlast` → payload `tlast`: **1 cycle**, or **2 cycles** when the last input beat holds more than 2 payload
  bytes (a tail beat follows).
- input `tlast` → `stat_valid`: **1 cycle**.

### Contract for the consumer (F3 `moldudp64_rx`)

- **Abort:** if the **last** beat of a packet has `m_axis_udp_tuser` = 1, the whole packet is invalid (FCS error,
  runt, truncated frame, or bytes after the datagram). Before the last beat, `tuser` is always 0. Frames rejected
  in the header never appear on the output.
- **Framing:** `tkeep` = `8'hFF` before the last beat; on the last beat it is contiguous from lane 0 and not empty.
  The packet holds exactly `m_udp_len` bytes, or fewer when it ends with an abort (truncated frame).
- **Metadata:** `m_udp_*` is valid from the first payload beat through `tlast` and does not change in between, so
  it may be read on any beat.
- **No backpressure:** the output has no `tready`; the consumer takes one beat per cycle.

### Contract for the CSR block (F5 `hft_csr_wrap`)

- Every `cfg_*` input except `cfg_rx_en` is read in **one** cycle per frame (stage A); `cfg_rx_en` at the SOF.
  `hft_csr_wrap` changes all `cfg_*` in one cycle (config commit, below), so each frame sees either the old or
  the new configuration as a whole (`tc_cfg_change_no_tearing` here, `tc_rx_csr_random` in the system test).
- Counters: the `stat_*` event feeds `rx_stats` (below).

### Reset

`rst` is synchronous and active-high. It comes with the MAC reset or between frames, and the first beat after it is
a SOF. A reset in the middle of a frame stops its payload and gives no stat event for it.

### Implementation notes (timing)

The OOC gate at 156.25 MHz first failed by 2.8 ns. All latencies stayed the same; the logic at `tlast` and in stage B
was made shallower:
- Header checks become one bit each on their own beat.
- The IPv4 checksum is tested as "sum of the 10 words is k × 0xFFFF" (k = 1..10): constant compares instead of
  the fold adders.
- Frame-length checks at `tlast` use `bytes_q` (a multiple of 8) and the contiguous `tkeep`. The datagram end is a
  down-counter `left_q` whose flags are precomputed with constant compares.

These are rewrites of the same function, not reduced checks. They were verified three ways:
- Every rewritten formula was compared with the old one over its whole input range. Example: all 655 351 reachable
  checksum sums. The frame-length logic was compared beat by beat over 1–1699 B frames × ip_len 0–1600.
- The old RTL ran in lockstep with the new one on random traffic.
- All tests passed unchanged.

They rely on the input contract above: full beats before `tlast`, and a contiguous `tkeep` on the `tlast` beat
(SVA `a_in_keep_full`, `a_in_keep_contig`).

The only difference is a fix. The old logic compared a 16-bit saturated frame length with the datagram end. It
dropped the largest legal datagrams (ip_len above 65521) as `LEN`, while the model accepts them.
`tc_size_limits` covers this. The `left_q` floor (−65536) only stops wrapping on frames past about 131 KB; there the
verdict is `LEN` either way.

### Verification

| What | Where |
|------|-------|
| Golden model (scapy, independent of the RTL's byte offsets) and its own tests | `tb/model/net.py`, `tb/selftest/test_net_model.py` |
| Directed tests, latency, reset, K1/K2/K4 regressions; the directed group also runs at `N_FILTERS` = 1 and 5 | `tb/unit/test_eth_ip_udp_rx.py` (group `directed`, `tc_k4_long_packet_no_spurious_event`) |
| Limits: largest datagram and ±1 B, 64 KiB counters, `left_q` floor (`tc_size_limits`); every reachable checksum sum k·0xFFFF, k = 1..9 (`tc_checksum_sums`); `ts_now` wrap (`tc_ts_wrap`) | same module |
| 10⁴ random frames against the model, coverage gate (52 bins) | `tc_random_golden` (writes `coverage.json` in its run directory) |
| Line rate, IPG 0, zero loss | `tc_line_rate_b2b` |
| Assertions (bound in every test) and their own tests | `tb/sva/udp_rx_props.sv`, `tb/sv/tb_udp_rx_props.sv`, `tb/unit/test_sva_checkers.py` |

### Synthesis (OOC, `just synth-core`, Artix-7 `xc7a100tcsg324-1`, `N_FILTERS` = 8)

Inputs and configuration go through one register in the harness (`rtl/platform/synth_ooc/eth_ip_udp_rx_ooc.sv`), so
every path is register to register.

| Profile | LUT | FF | WNS (ns) | WHS (ns) | `report_cdc` | Gate |
|---------|-----|----|----------|----------|--------------|------|
| net2host (156.25 MHz) | 662 | 1269 | +0.247 | +0.266 | no crossing | PASS |
| host2net (250 MHz) | 662 | 1269 | −2.153 | +0.266 | no crossing | information only |

### Out of scope

VLAN and IP options are dropped and counted, not supported. UDP checksum, IPv6, and ARP/ICMP/IGMP replies are
the host's job. The timestamp counter comes in F8.

## CSR block: `hft_csr`, `hft_csr_wrap`, `rx_stats`

The host register interface, generated from one source: [`firmware/rdl/hft_csr.rdl`](../../rdl/hft_csr.rdl)
(SystemRDL 2.0). `just gen` runs PeakRDL (`scripts/gen_csr.sh`) and writes:
- `gen/hft_csr.sv` and `gen/hft_csr_pkg.sv`: the register block (PeakRDL-regblock, AXI4-Lite);
- `software/include/gen/hft_csr.h`: the C header (packed structs and field macros);
- `firmware/rdl/hft_csr.md`: the register reference.

The generated files are committed and never edited by hand; `just gen-check` (part of `just ci`) fails when
they differ from a fresh `just gen`. The blocks are listed in [`filelists/csr.f`](../../filelists/csr.f).

### Bus

- AXI4-Lite subordinate, 32-bit data, 16-bit address: the whole 64 KiB window. `wstrb` writes single bytes.
- `SLVERR` (and read data 0) for an unmapped address, a write to a read-only register and a read from a
  write-only register; such an access changes nothing.
- AW and W are accepted in any order and stored; every accepted request gets exactly one response, in order,
  also while `bready`/`rready` are held low (legacy K5–K8).
- Read latency includes one register stage inside the read mux (`--rt-read-fanin`, for timing).

### Map

Only blocks with a user are defined; the rest of the window is reserved (unmapped, `SLVERR`). Field details:
[`hft_csr.md`](../../rdl/hft_csr.md).

| Base | Block | Registers |
|------|-------|-----------|
| `0x0000` | GLOBAL | `ID` (RO, `0x48465431` "HFT1"), `VERSION` (RO, `0x0005_0000`), `SCRATCH` (RW) |
| `0x0100` | NET | `NET_CTRL` (`rx_en` bit 0, `accept_bcast` bit 1), `LOCAL_MAC_LO` (MAC[31:0]), `LOCAL_MAC_HI` (MAC[47:32]), `NET_COMMIT` (WO), `NET_STATUS` (RO, `commit_busy`), `FILTER[i]` at `0x0120 + 8i`: `IP`, `PORT_EN` (port bits 15:0, enable bit 16) |
| `0x0200` | FEED | reserved (F3) |
| `0x0300` | RING | reserved (F6) |
| `0x0400` | STATS | `STATS_CTRL` (WO, `snapshot`), `STATS_SEQ` (RO), eleven 64-bit counters as `LO`/`HI` pairs from `0x0410`: `RX_PKTS`, `RX_BYTES`, then `DROP_<reason>` at `0x0420 + 8 × (reason − 1)` |
| `0x0500`+ | RISK, STRATEGY, TX, SYMBOL | reserved (F4, F7) |

Software never writes a field that hardware also writes: shadow, command and status fields are in separate
registers, so a hardware update cannot overwrite a host write (legacy K5). A selftest checks this rule on the RDL.

### Config commit (NET)

The NET registers are a shadow: writing them changes nothing in the receiver.
1. Write the registers (any order).
2. Write `NET_COMMIT` = 1. The whole shadow is copied into a commit buffer and sent to the net clock domain in
   one `cdc_handshake` transfer. `eth_ip_udp_rx`'s `cfg_*` then change together in one `net_clk` cycle, so a frame
   sees the old or the new configuration as a whole.
3. Read `NET_STATUS.commit_busy` until it is 0: the last commit is in effect.

A commit while one is in flight is not lost: the buffer takes the newer shadow and is sent when the handshake is
free (commits coalesce, the newest wins). Writes after a commit never reach the receiver without a new commit.
Reads return the shadow (what was written), not what the receiver uses.

Latency: `NET_COMMIT` pulse → transfer accepted 2 `host_clk` edges; accepted → `cfg_*` changed `STAGES` + 1 = 3
`net_clk` edges (one more with CDC jitter). After reset `cfg_rx_en` = 0 and the other `cfg_*` are undefined until
the first commit; the receiver drops every frame as `DISABLED` until then.

### Counters and snapshot (STATS)

`rx_stats` counts in the net clock domain, on each `stat_valid` of `eth_ip_udp_rx`: `RX_PKTS` += 1,
`RX_BYTES` += `stat_bytes`, and `DROP_<reason>` += 1 unless `RX_OK`. The counters are 64 bits, wrap, and are
cleared only by reset (no software clear: no race between host and hardware).

The STATS counter registers change only when a snapshot lands. Protocol (one software owner):
1. Read `STATS_SEQ`.
2. Write `STATS_CTRL` = 1.
3. Read `STATS_SEQ` until it changes.
4. Read the counters. `LO` and `HI` of one counter, and all counters, belong to the same snapshot.

A snapshot takes all eleven counters in **one** `net_clk` cycle: the acceptance edge of the net → host
`cdc_handshake`. A frame event sampled on that edge is in the next snapshot. Requests while one is in flight
coalesce; none is lost (a snapshot taken after the last request always lands). The counters read 0 before the
first snapshot. `STATS_SEQ` counts landed snapshots and changes one `host_clk` edge after the counters.

Latency (handshakes free): `STATS_CTRL` pulse → request accepted 1 `host_clk` edge; request at net → counters
taken 1 `net_clk` edge.

### Clocks and reset

Two domains: `host_clk`/`host_rst` (bus, register block, commit buffer) and `net_clk`/`net_rst` (`cfg_*`,
`rx_stats`). Both resets come from one source through one `reset_sync` per domain and are asserted together
(`cdc_handshake` contract). The only crossings are three `cdc_handshake` instances: config (442 bits, host → net),
snapshot request (1 bit, host → net) and counters (704 bits, net → host).

### Verification

| What | Where |
|------|-------|
| Map: every address against a hand-written table, reason-code formula, access kinds, the K5 rule (and that it bites), C++20 compile of the header with hand-written `offsetof` asserts | `tb/selftest/test_csr_map.py`, `tb/selftest/csr_header_check.cpp` |
| Register model built from the RDL (not from the generated SV) | `tb/model/csr.py` |
| Bus: reset values, every register, every `wstrb`, `SLVERR` cases, K6/K7/K8 regressions, random traffic with an 86-bin coverage gate | `tb/unit/test_hft_csr.py` (AXI driver `tb/common/axil.py`) |
| AXI4-Lite assertions (bound to `hft_csr`) and their own tests | `tb/sva/axil_props.sv`, `tb/sv/tb_axil_props.sv` |
| Counters: every edge against a model, every reason, wrap (8-bit build), carry past 2³² | `tb/unit/test_rx_stats.py` |
| Commit and snapshot at five clock/jitter profiles: exact latencies, atomicity monitor on every net edge, coalescing, snapshot = counters of its acceptance edge, K5 writes while snapshots land, LO/HI past 2³² | `tb/unit/test_hft_csr_wrap.py` (harness `tb/common/csr_env.py`) |
| System: `eth_ip_udp_rx` + `hft_csr_wrap`, each frame against the F2 model under the configuration it used, 2000 random frames with random commits and snapshots | `tb/system/test_rx_csr.py` |

### Synthesis (`just synth-csr`, OOC place & route)

The harness `rtl/platform/synth_ooc/hft_csr_wrap_ooc.sv` registers every input in its own clock domain. The two
gates are placed and routed out of context (no board or pins); the table also gives the synthesis estimate.

| Part | net / host clock | Flow | LUT | FF | WNS (ns) | WHS (ns) | Gate |
|------|------------------|------|-----|----|----------|----------|------|
| `xcku5p-ffvb676-2-e` (Kintex UltraScale+) | 156.25 / 250 MHz | routed | 1329 | 4349 | +0.758 (synthesis +1.988) | +0.044 | PASS |
| `xc7a100tcsg324-1` (Artix-7) | 156.25 / 125 MHz | routed | 1328 | 4349 | +0.324 (synthesis +1.049) | +0.098 | PASS |
| `xc7a100tcsg324-1` | 156.25 / 250 MHz | synthesis | 1328 | 4349 | −2.951 | +0.164 | information only |

`report_cdc`: CDC-3 (synchronizers) and CDC-15 (handshake data captured with an enable, constrained by `cdc.tcl`),
no critical finding. The Kintex UltraScale+ part is the class of a real 10G + PCIe Gen3 x8 card (250 MHz PCIe
user clock); the Artix-7 at 125 MHz is the PCIe Gen2 x4 (128-bit) configuration of a low-cost card. The register
read path does not reach 250 MHz on the Artix-7; the part is not upgraded to hide that.
