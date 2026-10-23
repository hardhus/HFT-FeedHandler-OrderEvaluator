# Core blocks (`firmware/rtl/core/`)

The hot path from the MAC to the order logic, and the host register interface that configures and counts it.
The hot-path blocks run in the network clock domain (`net_clk`, 156.25 MHz) at line rate, without backpressure;
they are listed in [`filelists/core.f`](../../filelists/core.f), after `hft_pkg`, with their assertions in
[`filelists/sva_core.f`](../../filelists/sva_core.f) and their OOC synthesis harnesses in
[`filelists/synth_ooc_core.f`](../../filelists/synth_ooc_core.f). The CSR blocks are in
[`filelists/csr.f`](../../filelists/csr.f) (assertions [`sva_csr.f`](../../filelists/sva_csr.f), harness
[`synth_ooc_csr.f`](../../filelists/synth_ooc_csr.f)). The design decisions are recorded in the F2, F5 and F3 plans
(audit 09, 10, 11); this file is the contract.

## Catalog

| Block | What it does | Phase |
|-------|--------------|-------|
| `hft_pkg` | Protocol constants, big-endian lane helpers `be16`/`be32`, receive reason codes `rx_reason_e`, MoldUDP64 constants and packet status codes `mold_status_e` | F2, F3 |
| `eth_ip_udp_rx` | Ethernet/IPv4/UDP receive filter: header checks, 8-entry (IP, port) filter table, UDP payload realigned to byte 0 | F2 |
| `moldudp64_rx` | MoldUDP64 de-framer: one 64-B message vector per cycle, sequence tracking (gap, duplicate, A/B lines, heartbeat, end of session, resync), one event per packet | F3 |
| `gen/hft_csr` | AXI4-Lite register block, generated from `firmware/rdl/hft_csr.rdl` (PeakRDL) | F5, F3 |
| `rx_stats` | Receive counters: packets, bytes, one drop counter per reason (64 bit) | F5 |
| `mold_stats` | Feed counters: one per packet status, plus messages, duplicate, oversize and gap counts (64 bit) | F3 |
| `hft_csr_wrap` | `hft_csr` plus the transfers between host and net clock: atomic NET and FEED commits, feed resync, counter snapshot | F5, F3 |

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

### Synthesis (OOC, `just synth-core`, placed and routed, `N_FILTERS` = 8)

Inputs and configuration go through one register in the harness (`rtl/platform/synth_ooc/eth_ip_udp_rx_ooc.sv`), so
every path is register to register. The gate is the routed timing; the synthesis estimate is given for reference.

| Part | Clock | LUT | FF | WNS (ns) | WHS (ns) | `report_cdc` | Gate |
|------|-------|-----|----|----------|----------|--------------|------|
| `xc7a100tcsg324-1` (Artix-7) | 156.25 MHz | 662 | 1269 | +0.382 (synthesis +0.247) | +0.131 | no crossing | PASS |
| `xcku5p-ffvb676-2-e` (Kintex UltraScale+) | 156.25 MHz | 673 | 1269 | +3.689 (synthesis +4.641) | +0.046 | no crossing | PASS |
| `xc7a100tcsg324-1` | 250 MHz | 662 | 1269 | −2.153 (synthesis) | +0.266 | no crossing | information only |

### Out of scope

VLAN and IP options are dropped and counted, not supported. UDP checksum, IPv6, and ARP/ICMP/IGMP replies are
the host's job. The timestamp counter comes in F8.

## `moldudp64_rx`

Decodes the `eth_ip_udp_rx` payload stream as MoldUDP64 (Nasdaq MoldUDP64 v1.00), in `net_clk`, at line rate.
Each message leaves as one vector in one cycle, so the ITCH decoder (F4) can take its fields at fixed offsets.
The block keeps the feed's expected sequence number and gives one event per packet that tells downstream whether
the packet's messages count.

MoldUDP64 in brief (all fields big-endian):
- **Header, 20 B:** `session` (10 B ASCII) at 0, `seq` (8 B) at 10, `count` (2 B) at 18. `seq` is the number of the
  packet's first message; the next messages are `seq` + 1, + 2, ….
- **Message block:** a 2-B `length` (not counting itself), then `length` bytes. Blocks follow each other with no
  padding; `count` blocks fill the payload exactly.
- **Heartbeat:** `count` = 0. **End of session:** `count` = 0xFFFF. Both carry the next expected `seq` and no block.

### Ports

| Port | Width | Meaning |
|------|-------|---------|
| `s_axis_udp_*`, `s_udp_len`, `s_udp_filter_idx`, `s_udp_sof_ts` | as `eth_ip_udp_rx` `m_*` | The F2 payload stream and its metadata (contract above). `tuser` on the `tlast` beat = abort |
| `cfg_feed_en` | 1 | Feed enable |
| `cfg_session_check`, `cfg_session` | 1, 80 | Compare the header session with `cfg_session` (byte 0 = `[79:72]`, as on the wire) |
| `cfg_filter_mask` | `N_FILTERS` | Bit i: F2 filter entry i belongs to this feed. The A and B lines of one feed are two entries in the same mask |
| `cfg_resync`, `cfg_resync_learn`, `cfg_resync_seq` | 1, 1, 64 | Resync pulse: learn the next packet's `seq`, or set the expected `seq` to `cfg_resync_seq` |
| `m_msg_valid`, `m_msg_data`, `m_msg_len` | 1, 512, 7 | One message per cycle, no `ready`. Byte i = `m_msg_data[8i+7:8i]` (byte 0 = ITCH message type); bytes from `m_msg_len` on are 0. `m_msg_len` = 6..64 |
| `m_msg_seq`, `m_msg_sof_ts` | 64, 64 | The message's sequence number; the packet's SOF timestamp |
| `m_pkt_valid`, `m_pkt_status` | 1, `mold_status_e` | One event per input packet, after all of its messages |
| `m_pkt_seq`, `m_pkt_count` | 64, 16 | Header `seq` and `count` (undefined for `RUNT`) |
| `m_pkt_gap` | 64 | Messages skipped before this packet; non-zero only when accepted in sync |
| `m_pkt_msgs` | 16 | Messages output for this packet (in every status) |
| `m_pkt_dup_msgs`, `m_pkt_oversize` | 16, 16 | Messages below the expected `seq`; messages longer than 64 B. Both 0 unless the status is `OK`, `HEARTBEAT`, `EOS` or `DUP` |
| `m_pkt_synced_now` | 1 | This packet synchronized the feed |
| `feed_expected_seq`, `feed_synced`, `feed_eos` | 64, 1, 1 | Feed state, for the CSR snapshot |

`cfg_*` except the resync inputs are sampled on the first beat of each packet, so a configuration change applies
to whole packets. `hft_csr_wrap` changes them together in one cycle (FEED commit, below).

### Message sizes and line rate

- **Line rate is guaranteed for `length` ≥ 6** (blocks of 8 B or more): at most one block boundary per beat, no
  input gap needed (IPG 0). Every ITCH 5.0 message qualifies (12 to 50 B).
- **`length` ≤ 5** is legal MoldUDP64 but not ITCH. Several boundaries could fall in one beat, so the packet becomes
  `UNSUPPORTED` as soon as such a length field is complete: no more messages, the expected `seq` stays. The next
  packet then shows a gap and software re-requests.
- **`length` > 64** does not fit the vector: the message is skipped (its bytes consumed, its `seq` used up) and
  counted in `m_pkt_oversize`; the packet status does not change.

### Packet status and priority (`hft_pkg::mold_status_e`)

| Code | Status | When | Messages | Expected `seq` (E) |
|------|--------|------|----------|--------------------|
| 0 | `MOLD_OK` | normal packet (`count` 1..0xFFFE) accepted | those with `seq` ≥ E | updated |
| 1 | `MOLD_HEARTBEAT` | `count` = 0, accepted | – | updated |
| 2 | `MOLD_EOS` | `count` = 0xFFFF, accepted; `feed_eos` = 1 | – | updated |
| 3 | `MOLD_DUP` | in sync; normal packet with S + C ≤ E, or heartbeat/EOS with S < E | none | kept |
| 4 | `MOLD_DISABLED` | `cfg_feed_en` = 0 | none | kept |
| 5 | `MOLD_OTHER_FEED` | `cfg_filter_mask[s_udp_filter_idx]` = 0 | none | kept |
| 6 | `MOLD_ABORT` | `tuser` on the `tlast` beat | may have left; downstream drops them | kept |
| 7 | `MOLD_RUNT` | `s_udp_len` < 20 | none | kept |
| 8 | `MOLD_SESSION` | `cfg_session_check` and session ≠ `cfg_session` | none | kept |
| 9 | `MOLD_UNSUPPORTED` | a block with `length` ≤ 5 | those before that block | kept |
| 10 | `MOLD_MALFORMED` | framing inconsistent (below) | may have left | kept |

S = header `seq`, C = `count`, E = expected `seq`. When several conditions hold, the first in this order is
reported: `DISABLED` > `OTHER_FEED` > `ABORT` > `RUNT` > `SESSION` > `UNSUPPORTED` > `MALFORMED` > `DUP` >
accepted. `DISABLED`, `OTHER_FEED` and `SESSION` are known after the first two beats; such a packet is not framed
and gives no message, but it still gets its event. `ABORT` is known only at the end and overrides every status
below it.

`MALFORMED`: bytes left after `count` blocks, the payload ends inside a block (or inside a length field), or a
heartbeat/EOS payload is not exactly 20 B.

Per block, in this order: a block is handled when its last byte arrives (a block cut short counts for nothing);
in sync and below E it is a duplicate; otherwise longer than 64 B is oversize; otherwise the message leaves.
`m_pkt_dup_msgs` and `m_pkt_oversize` are reported only when the framing was checked (`OK`, `HEARTBEAT`, `EOS`,
`DUP`): in an aborted or malformed packet the length fields may be corrupt, and the same messages come again from
the other line or a retransmission.

### Sequence tracking

- D = S − E as a signed 64-bit number (serial number arithmetic; it works across the 2⁶⁴ wrap). D > 0: a gap of
  D messages (`m_pkt_gap`); D = 0: in order; D < 0: old.
- **Partial overlap:** a normal packet with S < E < S + C drops its first E − S messages as duplicates and is `OK`.
  This is how the second copy of an A/B pair, or a retransmission, is merged.
- **Update, only when accepted, at the end of the packet:** normal packet E ← S + C (also after a gap);
  heartbeat/EOS E ← S. An accepted normal packet clears `feed_eos`; a heartbeat leaves it.
- **Out of sync** (after reset or a learn resync): the first packet that passes every check above `DUP`
  synchronizes the feed (`m_pkt_synced_now`). It reports no gap and no duplicate; all its messages leave, and E is
  set as above. Out of sync there is no duplicate filter.
- **Resync** (`cfg_resync` pulse): learn → out of sync (E kept until the next accepted packet); set →
  E ← `cfg_resync_seq`, in sync. Both clear `feed_eos`. The pulse applies:
  - between packets, or on a packet's first beat: at once, before that packet (the moment `cfg_*` are sampled);
  - later in a packet: after the packet, after its own E update. Several waiting pulses: the last one counts;
  - a waiting pulse and a new one in the same cycle (the cycle after `tlast`, or the next packet's first beat at
    IPG 0): first the waiting one, then the new one.

### Contract for the consumer (F4, F6)

- Every `m_msg_valid` of a packet comes before that packet's `m_pkt_valid`. Downstream **keeps** the packet's
  messages if the status is `OK`, `HEARTBEAT` or `EOS` and **drops** them otherwise. Messages are cut through:
  they leave before the packet is checked to the end (FCS, framing).
- An aborted packet gives the same messages as the same bytes without the abort (duplicate filter included), so
  that nothing needs the future; its bytes may be corrupt. A rule that acts on a message before the packet event
  acts on unconfirmed data.
- Within a packet, `m_msg_seq` grows by 1 from one message to the next, or by more when oversize messages were
  skipped in between (each uses its number).

### Beat timeline and latency

| Beat | Payload bytes | Used |
|------|---------------|------|
| 0 | 0–7 | session bytes 0–7; `cfg_*` sampled (`DISABLED`, `OTHER_FEED`) |
| 1 | 8–15 | session bytes 8–9 (session verdict), `seq` bytes 0–5 |
| 2 | 16–23 | `seq` bytes 6–7, `count`, first length field (lanes 4–5); first message data from lane 6 |
| 3 + | … | message blocks; the first message ends on beat 3 at the earliest |

Latency, checked with exact equality on every message and event (not only in `tc_latency`):
- input beat holding a message's last byte → `m_msg_valid`: **2 cycles**;
- input `tlast` → `m_pkt_valid`: **3 cycles**;
- `feed_*`: a packet's update shows 2 cycles after its `tlast` (one cycle before its event); a resync applied at
  once shows one cycle after its pulse.

### Reset

`rst` is synchronous and active-high, between packets or with the source. A packet cut by it gets no event, and the
feed state (E, `feed_synced`, `feed_eos`) returns to 0.

### Implementation notes (timing)

- **Framer:** the position of the next length field is kept as 8·w + o. Only the lane offset o (3 bit) and the
  flags w = 0 / w = 1 are in the one-cycle loop; a long block loads w one framing beat later and counts it down.
  Every length-dependent decision (short block, oversize, ends in this beat, next o) is computed for each of the
  eight lanes in parallel and selected by o. The first version, one 17-bit add in the loop, failed by 4.9 ns on
  the Artix-7.
- **Realigner:** two rotators (the message in progress and one that starts in the same beat), byte write enables
  into a 64-B accumulator, then the output register.
- **Sequence compare:** D and E − S are registered on beat 2; the duplicate count, the S + C ≤ E test and the gap,
  duplicate and oversize gates are one stage later. A block's `seq` is the header `seq` plus a 16-bit block index,
  with no 64-bit adder in the framing loop. The latencies above did not change.

### Verification

| What | Where |
|------|-------|
| Golden model in pure Python, on the byte string (no beats or lanes): packet builders, ITCH 5.0 length table, `expect()` with the rules above, `random_feed()` with round-robin categories | `tb/model/mold.py` |
| Model selftests (no simulator): hand-written packets, every pair of faults for the priority, codes against `hft_pkg.sv`, 2⁶⁴ wrap, generator intent against `expect()` | `tb/selftest/test_mold_model.py` |
| Directed tests: hand packets, alignment matrix (start lane × end lane × split length field, every ITCH length, 6..64, 65, 1400), oversize up to the largest UDP payload, unsupported, malformed, runt, latency, reset in a packet, heartbeat/EOS, (+1, +1000, +2⁴⁰, 2⁶³ − 1), full and partial duplicates, abort, session, other feed, disabled, resync timing, first packet out of sync, wrap at 2⁶⁴ | `tb/unit/test_moldudp64_rx.py` |
| 10⁴ random packets against the model, coverage gate (146 bins); line rate at IPG 0 with 6-B and 12-B messages, zero loss | `tc_random_golden`, `tc_line_rate_b2b` |
| `feed_*` against the model's timeline on every cycle | the monitor of every test |
| Assertions (12, bound in every core build) and their own tests | `tb/sva/mold_props.sv`, `tb/sv/tb_mold_props.sv`, `tb/unit/test_sva_checkers.py` |
| Counters | `tb/unit/test_mold_stats.py` |
| System: `eth_ip_udp_rx` → `moldudp64_rx` → `hft_csr_wrap`; hand-computed bring-up; 2000 random frames with A/B copies, F2 faults, FEED commits, resyncs and snapshots | `tb/system/test_rx_csr.py` (`tc_feed_bring_up`, `tc_feed_random`) |

### Synthesis (OOC, `just synth-core`, placed and routed, `N_FILTERS` = 8)

Harness `rtl/platform/synth_ooc/moldudp64_rx_ooc.sv` (inputs registered, as for `eth_ip_udp_rx`).

| Part | Clock | LUT | FF | WNS (ns) | WHS (ns) | `report_cdc` | Gate |
|------|-------|-----|----|----------|----------|--------------|------|
| `xc7a100tcsg324-1` (Artix-7) | 156.25 MHz | 2482 | 2906 | +0.086 (synthesis +0.313) | +0.097 | no crossing | PASS |
| `xcku5p-ffvb676-2-e` (Kintex UltraScale+) | 156.25 MHz | 2503 | 2906 | +2.502 (synthesis +4.419) | +0.043 | no crossing | PASS |

The Artix-7 margin is small; the next steps, if a change breaks it, are a second realigner stage or one more
stage in the sequence compare (latency + 1, with this file and the tests).

### Out of scope

Re-requests (a TCP/UDP request to the exchange) are the host's job: the event gives the gap. One feed with one E
(its A and B lines in one mask), no reordering buffer for messages after a gap, no decoding of `length` ≤ 5 at line
rate. ITCH fields are decoded in F4.

## CSR block: `hft_csr`, `hft_csr_wrap`, `rx_stats`, `mold_stats`

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
| `0x0000` | GLOBAL | `ID` (RO, `0x48465431` "HFT1"), `VERSION` (RO, `0x0005_0001`), `SCRATCH` (RW) |
| `0x0100` | NET | `NET_CTRL` (`rx_en` bit 0, `accept_bcast` bit 1), `LOCAL_MAC_LO` (MAC[31:0]), `LOCAL_MAC_HI` (MAC[47:32]), `NET_COMMIT` (WO), `NET_STATUS` (RO, `commit_busy`), `FILTER[i]` at `0x0120 + 8i`: `IP`, `PORT_EN` (port bits 15:0, enable bit 16) |
| `0x0200` | FEED | Shadow: `FEED_CTRL` (`enable` bit 0, `session_check` bit 1, `resync_learn` bit 2, `filter_mask` bits 15:8), `SESSION_0..2` at `0x0204` (session bytes 0–3, byte 0 = bits 31:24; 4–7; 8–9 = bits 31:16), `RESYNC_SEQ_LO/HI` at `0x0210`. `FEED_COMMIT` at `0x0218` (WO, `commit` bit 0, `resync` bit 1), `FEED_STATUS` (RO, `commit_busy`). Snapshot: `EXPECTED_SEQ` (`LO`/`HI`) at `0x0220`, `FEED_STATE` at `0x0228` (`synced` bit 0, `eos` bit 1), `MOLD_<status>` at `0x0240 + 8 × status`, then `MOLD_MSGS`, `MOLD_DUP_MSGS`, `MOLD_OVERSIZE_MSGS`, `MOLD_GAP_EVENTS`, `MOLD_GAP_MSGS` at `0x0298`..`0x02B8` |
| `0x0300` | RING | reserved (F6) |
| `0x0400` | STATS | `STATS_CTRL` (WO, `snapshot`), `STATS_SEQ` (RO), eleven 64-bit counters as `LO`/`HI` pairs from `0x0410`: `RX_PKTS`, `RX_BYTES`, then `DROP_<reason>` at `0x0420 + 8 × (reason − 1)` |
| `0x0500`+ | RISK, STRATEGY, TX, SYMBOL | reserved (F4, F7) |

Software never writes a field that hardware also writes: shadow, command and status fields are in separate
registers, so a hardware update cannot overwrite a host write (legacy K5). A selftest checks this rule on the RDL.
For the same reason the resync value (`RESYNC_SEQ`, written by software) and the expected sequence number
(`EXPECTED_SEQ`, written by hardware) are two registers.

`VERSION` is major.minor: a minor step only adds registers (software written for an older minor still works), a
major step changes existing ones. The F3 FEED block made it `0x0005_0001`.

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

### Config commit and resync (FEED)

The FEED registers (`FEED_CTRL`, `SESSION_*`, `RESYNC_SEQ_*`) are a shadow like NET, with their own commit buffer
and `cdc_handshake` (156 bits). `FEED_COMMIT.commit` sends them; `moldudp64_rx` takes its `cfg_*` at the first
beat of each packet, so a packet sees the old or the new configuration as a whole. `FEED_STATUS.commit_busy`,
coalescing and latency are as for NET. After reset `cfg_feed_en` and `cfg_session_check` are 0 until the first
commit; the feed counts every packet as `DISABLED` until then.

`FEED_COMMIT.resync` commits in the same way and asks `moldudp64_rx` to resync with the `resync_learn` and
`RESYNC_SEQ` of that commit (learn the next packet's `seq`, or set E). It works like a self-clearing command bit:
- the request is the rising edge of the field (regblock may hold a singlepulse field one more cycle; that must not
  make a second resync, since a resync is not idempotent);
- it sets the buffer's resync bit, which stays set until a transfer carries it. A request while one is pending or
  in flight merges with it: never lost, one resync with the newest shadow;
- at net, `cfg_resync` is a 1-cycle pulse in the cycle the transfer lands. `moldudp64_rx` applies it between
  packets or after the packet in progress (see its resync rules).

Software that wants two separate resyncs waits for `commit_busy` = 0 between them.

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

The same snapshot also updates the FEED block's `EXPECTED_SEQ`, `FEED_STATE` and `MOLD_*` registers, taken in
the same `net_clk` cycle; `STATS_SEQ` counts them too. `mold_stats` counts the `moldudp64_rx` packet events:
`MOLD_<status>` += 1 per packet; `MOLD_MSGS` += `m_pkt_msgs` for `OK`, `HEARTBEAT` and `EOS` only (the messages
downstream keeps); `MOLD_DUP_MSGS`, `MOLD_OVERSIZE_MSGS` and `MOLD_GAP_MSGS` add their event fields;
`MOLD_GAP_EVENTS` += 1 when the gap is not 0. Same width, wrap and reset rules as `rx_stats`.

The feed state in a snapshot belongs to the same packet events as the counters: `moldudp64_rx` shows a packet's
state update one cycle before its event, and `mold_stats` counts the event one cycle after it, so the wrapper
delays `feed_*` by `FEED_DELAY` = 2 cycles. Between two snapshots taken in sync with no resync applied in between:

ΔE = Δ`MOLD_MSGS` + Δ`MOLD_OVERSIZE_MSGS` + Δ`MOLD_GAP_MSGS`

(every sequence number is either a kept message, a skipped oversize message or a gap). The system test checks it.

### Clocks and reset

Two domains: `host_clk`/`host_rst` (bus, register block, commit buffers) and `net_clk`/`net_rst` (`cfg_*`,
`rx_stats`, `mold_stats`). Both resets come from one source through one `reset_sync` per domain and are asserted
together (`cdc_handshake` contract). The only crossings are four `cdc_handshake` instances: NET config (442 bits,
host → net), FEED config (156 bits, host → net), snapshot request (1 bit, host → net) and snapshot (1794 bits,
net → host: 11 + 16 counters, expected `seq`, `synced`, `eos`).

### Verification

| What | Where |
|------|-------|
| Map: every address against a hand-written table, reason-code formula, access kinds, the K5 rule (and that it bites), C++20 compile of the header with hand-written `offsetof` asserts | `tb/selftest/test_csr_map.py`, `tb/selftest/csr_header_check.cpp` |
| Register model built from the RDL (not from the generated SV) | `tb/model/csr.py` |
| Bus: reset values, every register, every `wstrb`, `SLVERR` cases, K6/K7/K8 regressions, random traffic with an 86-bin coverage gate | `tb/unit/test_hft_csr.py` (AXI driver `tb/common/axil.py`) |
| AXI4-Lite assertions (bound to `hft_csr`) and their own tests | `tb/sva/axil_props.sv`, `tb/sv/tb_axil_props.sv` |
| Counters: every edge against a model, every reason, wrap (8-bit build), carry past 2³² | `tb/unit/test_rx_stats.py` |
| Feed counters: every edge against a model, every status into its own counter only, wrap (8-bit build), gap sum past 2³² and 2⁶⁴ | `tb/unit/test_mold_stats.py` |
| Commit and snapshot at five clock/jitter profiles: exact latencies, atomicity monitor on every net edge, coalescing, snapshot = counters of its acceptance edge, K5 writes while snapshots land, LO/HI past 2³² | `tb/unit/test_hft_csr_wrap.py` (harness `tb/common/csr_env.py`) |
| FEED at the same five profiles: commit latency and atomicity (also a shadow written while a commit waits), resync requests merged and never lost (also one made while a commit waits), a held singlepulse gives one resync, reset during a commit, feed state aligned with the counters in every snapshot phase | `tb/unit/test_hft_csr_wrap.py` (`tc_feed_*`, `tc_snapshot_feed_aligned`) |
| System: `eth_ip_udp_rx` + `moldudp64_rx` + `hft_csr_wrap`, each frame against the F2 model and each payload against the MoldUDP64 model under the configuration it used, 2000 random frames with random commits and snapshots, and 2000 feed frames with resyncs and the accounting identity | `tb/system/test_rx_csr.py` |

### Synthesis (`just synth-csr`, OOC place & route)

The harness `rtl/platform/synth_ooc/hft_csr_wrap_ooc.sv` registers every input in its own clock domain. The two
gates are placed and routed out of context (no board or pins); the table also gives the synthesis estimate.

| Part | net / host clock | Flow | LUT | FF | WNS (ns) | WHS (ns) | Gate |
|------|------------------|------|-----|----|----------|----------|------|
| `xcku5p-ffvb676-2-e` (Kintex UltraScale+) | 156.25 / 250 MHz | routed | 2463 | 8405 | +0.767 (synthesis +1.836) | +0.027 | PASS |
| `xc7a100tcsg324-1` (Artix-7) | 156.25 / 125 MHz | routed | 2460 | 8405 | +0.154 (synthesis +0.422) | +0.090 | PASS |
| `xc7a100tcsg324-1` | 156.25 / 250 MHz | synthesis | 2460 | 8405 | −2.753 | +0.164 | information only |

The FEED block and the wider snapshot about doubled the size (F5 alone: 1329 LUT, 4349 FF; Artix-7 margin
+0.324 ns). The Artix-7 host read path is the limit; regblock `--rt-read-response` is the next step if it fails.

`report_cdc`: CDC-3 (synchronizers) and CDC-15 (handshake data captured with an enable, constrained by `cdc.tcl`),
no critical finding. The Kintex UltraScale+ part is the class of a real 10G + PCIe Gen3 x8 card (250 MHz PCIe
user clock); the Artix-7 at 125 MHz is the PCIe Gen2 x4 (128-bit) configuration of a low-cost card. The register
read path does not reach 250 MHz on the Artix-7; the part is not upgraded to hide that.
