# Core blocks (`firmware/rtl/core/`)

The hot path from the MAC to the order logic. Every block runs in the network clock domain (`net_clk`, 156.25 MHz)
at line rate, without backpressure. All of them are listed in [`filelists/core.f`](../../filelists/core.f), after
`hft_pkg`. Their assertions are in [`filelists/sva_core.f`](../../filelists/sva_core.f) and their OOC synthesis
harnesses in [`filelists/synth_ooc_core.f`](../../filelists/synth_ooc_core.f). The design decisions behind
`eth_ip_udp_rx` are recorded in the F2 plan (audit 09); this file is the contract.

## Catalog

| Block | What it does | Phase |
|-------|--------------|-------|
| `hft_pkg` | Protocol constants, big-endian lane helpers `be16`/`be32`, receive reason codes `rx_reason_e` | F2 |
| `eth_ip_udp_rx` | Ethernet/IPv4/UDP receive filter: header checks, 8-entry (IP, port) filter table, UDP payload realigned to byte 0 | F2 |

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
  F5 updates the configuration registers of the net domain in one cycle (commit strobe + `cdc_handshake`), so
  each frame sees either the old or the new configuration as a whole (`tc_cfg_change_no_tearing`).
- Counters: on each `stat_valid`, `rx_pkts += 1`, `rx_bytes += stat_bytes`, and `drop_<reason> += 1` unless
  `RX_OK`. Counter list: `rx_pkts`, `rx_bytes`, `drop_disabled`, `drop_fcs`, `drop_mac`, `drop_ethertype`,
  `drop_ip_hdr`, `drop_frag`, `drop_proto`, `drop_filter`, `drop_len`.

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

### Verification

| What | Where |
|------|-------|
| Golden model (scapy, independent of the RTL's byte offsets) and its own tests | `tb/model/net.py`, `tb/selftest/test_net_model.py` |
| Directed tests, latency, reset, K1/K2/K4 regressions | `tb/unit/test_eth_ip_udp_rx.py` (group `directed`, `tc_k4_long_packet_no_spurious_event`) |
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
the host's job. Counters live in F5, the timestamp counter in F8.
