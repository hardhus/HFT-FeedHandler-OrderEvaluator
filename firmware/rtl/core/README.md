# Core blocks (`firmware/rtl/core/`)

The hot path from the MAC to the order logic, and the host register interface that configures and counts it.
The hot-path blocks run in the network clock domain (`net_clk`, 156.25 MHz) at line rate, without backpressure;
they are listed in [`filelists/core.f`](../../filelists/core.f), after `hft_pkg`, with their assertions in
[`filelists/sva_core.f`](../../filelists/sva_core.f) and their OOC synthesis harnesses in
[`filelists/synth_ooc_core.f`](../../filelists/synth_ooc_core.f). The CSR blocks are in
[`filelists/csr.f`](../../filelists/csr.f) (assertions [`sva_csr.f`](../../filelists/sva_csr.f), harness
[`synth_ooc_csr.f`](../../filelists/synth_ooc_csr.f)). The design decisions are recorded in the F2, F5, F3, F4 and
F6 plans (audit 09, 10, 11, 12, 13); this file is the contract.

## Catalog

| Block | What it does | Phase |
|-------|--------------|-------|
| `hft_pkg` | Protocol constants, big-endian lane helpers `be16`/`be32`, receive reason codes `rx_reason_e`, MoldUDP64 constants and packet status codes `mold_status_e`, ITCH 5.0 type codes, `itch_class_e`, `itch_outcome_e`, `msg_be` | F2, F3, F4 |
| `eth_ip_udp_rx` | Ethernet/IPv4/UDP receive filter: header checks, 8-entry (IP, port) filter table, UDP payload realigned to byte 0 | F2 |
| `moldudp64_rx` | MoldUDP64 de-framer: one 64-B message vector per cycle, sequence tracking (gap, duplicate, A/B lines, heartbeat, end of session, resync), one event per packet | F3 |
| `itch_decoder` | ITCH 5.0 classifier and field decoder, stock-locate filter (`symbol_filter`: 64 Kbit bitmap), packet events forwarded | F4 |
| `event_packer` | Turns `itch_decoder`'s output messages and packet events into 128-byte ring entries and delivers them whole per packet through a `COMMIT=1` `cdc_async_fifo` | F6 |
| `event_ring_writer` | Writes the committed entries into the host RAM ring, one 128-byte AXI4 burst each, in order; lossless while enabled | F6 |
| `gen/event_desc_pkg` | Ring entry layout and pack functions, generated from `firmware/formats/event_desc.toml` | F6 |
| `gen/hft_csr` | AXI4-Lite register block, generated from `firmware/rdl/hft_csr.rdl` (PeakRDL) | F5, F3, F4, F6 |
| `rx_stats` | Receive counters: packets, bytes, one drop counter per reason (64 bit) | F5 |
| `mold_stats` | Feed counters: one per packet status, plus messages, duplicate, oversize and gap counts (64 bit) | F3 |
| `itch_stats` | ITCH counters of committed messages: one per class and one per outcome (64 bit) | F4 |
| `hft_csr_wrap` | `hft_csr` plus the transfers between host and net clock: atomic NET and FEED commits (ITCH_CTRL in FEED), feed resync, symbol bitmap access, counter snapshot, RING block and ring counters, `cfg_epoch` | F5, F3, F4, F6 |

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
| `cfg_msg_ctx`, `m_msg_ctx` | `CTX_W`, `CTX_W` | An opaque per-packet context: sampled on the packet's first beat with the other `cfg_*` and given with each of its messages (F4: `ITCH_CTRL`, `CTX_W` = 12) |
| `m_pkt_valid`, `m_pkt_status` | 1, `mold_status_e` | One event per input packet, after all of its messages |
| `m_pkt_seq`, `m_pkt_count` | 64, 16 | Header `seq` and `count` (undefined for `RUNT`) |
| `m_pkt_gap` | 64 | Messages skipped before this packet; non-zero only when accepted in sync |
| `m_pkt_msgs` | 16 | Messages output for this packet (in every status) |
| `m_pkt_dup_msgs`, `m_pkt_oversize` | 16, 16 | Messages below the expected `seq`; messages longer than 64 B. Both 0 unless the status is `OK`, `HEARTBEAT`, `EOS` or `DUP` |
| `m_pkt_synced_now` | 1 | This packet synchronized the feed |
| `m_pkt_sof_ts`, `m_pkt_ctx` | 64, `CTX_W` | The packet's SOF timestamp and `cfg_msg_ctx` on its first beat, in every status, so a packet without messages has them too (F6: ring entries of heartbeats and end of session) |
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
| `xc7a100tcsg324-1` (Artix-7) | 156.25 MHz | 2482 | 2942 | +0.155 (synthesis +0.313) | +0.097 | no crossing | PASS |
| `xcku5p-ffvb676-2-e` (Kintex UltraScale+) | 156.25 MHz | 2503 | 2942 | +3.441 (synthesis +4.419) | +0.042 | no crossing | PASS |

Measured with the 12-bit per-packet context (`CTX_W` = 12, F4).

The Artix-7 margin is small; the next steps, if a change breaks it, are a second realigner stage or one more
stage in the sequence compare (latency + 1, with this file and the tests).

### Out of scope

Re-requests (a TCP/UDP request to the exchange) are the host's job: the event gives the gap. One feed with one E
(its A and B lines in one mask), no reordering buffer for messages after a gap, no decoding of `length` ≤ 5 at line
rate. ITCH fields are decoded in F4 (`itch_decoder`).

## `itch_decoder`

Decodes each `moldudp64_rx` message as Nasdaq TotalView-ITCH 5.0 (specification revision 2023-04-28), in
`net_clk`, one message per cycle, without backpressure. It classifies the message by type and length, turns the
big-endian fields of the order and trade messages into numbers (legacy K3), filters by stock locate, and forwards
every packet event with the same latency, so the rule "a packet's messages come before its event" still holds
downstream. The 23 type codes are in `hft_pkg` (`ITCH_T_*`, legacy K12) and are checked against the model's table.

### Ports

| Port | Width | Meaning |
|------|-------|---------|
| `s_msg_valid`, `s_msg_data`, `s_msg_len`, `s_msg_seq`, `s_msg_sof_ts` | 1, 512, 7, 64, 64 | `moldudp64_rx`'s message vector (byte 0 = type, bytes from `len` on 0) |
| `s_msg_class_en`, `s_msg_class_filter` | 6, 6 | `ITCH_CTRL` of the message's packet: `moldudp64_rx` carries it as its per-packet context (`cfg_msg_ctx` = `{class_filter, class_en}`), sampled with the other FEED settings on the packet's first beat, so a FEED commit applies to whole packets |
| `s_pkt_*` | as `moldudp64_rx` `m_pkt_*` | Packet events, forwarded on `m_pkt_*` |
| `sym_req_valid/ready/wr/addr/wdata/wstrb`, `sym_rsp_valid/rdata` | 1/1/1/11/32/4, 1/32 | Symbol bitmap commands from `hft_csr_wrap` (word address; bit i of word w = locate 32w + i) |
| `m_ev_valid`, `m_ev_class`, `m_ev_type`, `m_ev_len`, `m_ev_seq`, `m_ev_sof_ts` | 1, 3, 8, 7, 64, 64 | One event per output message |
| `s_msg_epoch`, `m_ev_epoch`, `s_pkt_epoch`, `m_pkt_epoch` | `CFG_EPOCH_W` | The FEED commit count of the message's or event's packet, passed through for the ring entries |
| `m_ev_locate`, `m_ev_tracking`, `m_ev_ts` | 16, 16, 48 | Common header (stock locate, tracking number, timestamp in ns since midnight) |
| `m_ev_order_ref` … `m_ev_cross_type` | below | Normalized fields; 0 where they do not apply |
| `m_ev_raw` | 512 | The input vector, unchanged (every class; the host decodes the classes the hardware does not) |
| `m_res_valid`, `m_res_class`, `m_res_outcome` | 1, 3, 2 | One result per input message (`itch_class_e`, `itch_outcome_e`), for the counters |
| `m_pkt_*` | as `s_pkt_*` | The packet events, with the messages' latency |

### Classes and the pass rule

| Code | `itch_class_e` | Types | Output |
|------|----------------|-------|--------|
| 0 | `ITCH_ORDER` | A F E C X D U | header + normalized fields + raw |
| 1 | `ITCH_TRADE` | P Q B | header + normalized fields + raw |
| 2 | `ITCH_ADMIN` | S R H Y L V W K J h | header + raw |
| 3 | `ITCH_AUCTION` | I N O | header + raw |
| 4 | `ITCH_UNKNOWN` | every other type byte | header + raw |
| 5 | `ITCH_MALFORMED` | a known type whose length is not the specification length | header + raw, not decoded |

Unknown types are not dropped: the specification has grown (J 2017, h 2018, O 2023), and a feed that drops a
message silently corrupts the host's book. Only the length is checked; field values (side, price limit, enums)
are passed as they are.

The **header** (locate, tracking, timestamp) is output for every class, MALFORMED included, when the message has
at least 11 bytes (`ITCH_HDR_LEN`), else it is 0. The header is at the same offsets in every message.

Per message, in this order (`m_res_outcome`):
1. `class_en[class]` = 0 → `ITCH_RES_MASKED` (no event);
2. `class_filter[class]` = 1, locate ≠ 0 and the locate's bitmap bit 0 → `ITCH_RES_FILTERED` (no event);
3. else `ITCH_RES_OUT` (`m_ev_valid`).

Locate 0 (market-wide messages: S, V, W, K) is never filtered. The filter key is `m_ev_locate`, so a message
shorter than the header (locate 0) is never filtered either.

### Normalized fields

Alpha fields (side, stock, attribution, printable, cross type) are in wire order: byte 0 of the field in bits
[7:0]. Integers are unsigned; prices are `Price(4)` (4 decimal places).

| Field | Width | A | F | E | C | X | D | U | P | Q | B |
|-------|-------|---|---|---|---|---|---|---|---|---|---|
| `order_ref` | 64 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | original | ✓ | | |
| `new_ref` | 64 | | | | | | | ✓ | | | |
| `shares` | 64 | ✓ | ✓ | executed | executed | cancelled | | ✓ | ✓ | 8 B shares | |
| `price` | 32 | ✓ | ✓ | | execution | | | ✓ | ✓ | cross price | |
| `match` | 64 | | | ✓ | ✓ | | | | ✓ | ✓ | ✓ |
| `side` | 8 | ✓ | ✓ | | | | | | ✓ | | |
| `stock` | 64 | ✓ | ✓ | | | | | | ✓ | ✓ | |
| `attribution` | 32 | | ✓ | | | | | | | | |
| `printable` | 8 | | | | ✓ | | | | | | |
| `cross_type` | 8 | | | | | | | | | ✓ | |

E, C, X, D and U carry no price or side: the order book (order_ref → price, side, shares) is not in F4.

### Symbol filter (`symbol_filter`, inside `itch_decoder`)

- 2048 × 32-bit RAM on `net_clk` (two RAMB36): port A for the commands and the sweep (READ_FIRST, byte writes),
  port B for one lookup per message. One clock: on Artix-7/UltraScale+ a read of an address that the other port
  writes in the same cycle is undefined across clocks (UG473 conflict rules), so host writes reach the RAM as
  commands in `net_clk`, never as a second clock port.
- A lookup in the cycle a word is written sees the old word; from the next cycle on, the new one.
- Commands one at a time (`sym_req_ready`); a write is in the RAM one cycle after acceptance, and every command
  gets `sym_rsp_valid` one cycle after acceptance (read: `sym_rsp_rdata` = the word; write: an acknowledgement).
- **Sweep after reset:** the RAM has no reset. When `rst` falls, the block writes 0 to all 2048 words (2048
  cycles, about 13.1 µs); meanwhile `sym_req_ready` = 0 (a command waits) and every lookup gives 0 (filtered
  classes are FILTERED). Then the bitmap reads 0: the deterministic reset value.

### Latency

Checked with exact equality on every output by every test:
- `s_msg_valid` → `m_ev_valid` / `m_res_valid`: **2 cycles**;
- `s_pkt_valid` → `m_pkt_valid`: **2 cycles**, so a packet event still follows its packet's last result. A message
  in the same cycle as a packet event belongs to the next packet.

Stage 1: type, class, length check, the lookup address from the input (bytes 1–2). Stage 2: bit select from the
RAM word, the pass decision, field extraction into the output registers.

### Contract for the consumer (F6)

- Every event and result of a packet comes before that packet's `m_pkt_valid`. Downstream keeps them if the status
  is `OK`, `HEARTBEAT` or `EOS` and drops them otherwise (as for `moldudp64_rx`).
- The event's fields are those of the message as received; `m_ev_raw` holds the whole message for the classes the
  hardware does not decode.

### Reset

`rst` is synchronous. A message or packet event in the pipeline when it comes is dropped; with `moldudp64_rx`'s
reset rule, a packet cut by a reset has no event. After the reset the sweep runs (above).

### Implementation notes (timing)

The worst path is the lookup: RAM port B output → bit select (MUXF7/F8) → pass decision → `m_ev_valid`. It meets
156.25 MHz on the Artix-7 without a RAM output register; field extraction fits in stage 2 (no third stage).

### Verification

| What | Where |
|------|-------|
| Golden model in pure Python: the 23 types written out from the specification (page per type), `encode`/`decode`, `classify`, `expect()` with the rules above, a random generator with 106 categories (every type, ±1 byte, unknown types, edge locates, field extremes) | `tb/model/itch.py` |
| Model selftests (no simulator): hand-written hex messages for every type, K3 (`00 00 25 1C` = 9500), class and outcome codes and the 23 type codes against `hft_pkg.sv` (K12), the length table against `mold.ITCH50_LEN`, field layout without gaps or overlaps | `tb/selftest/test_itch_model.py` |
| Directed tests: hand messages, K3, K12, every length ±1, all 256 type bytes × 6..64 B, field extremes, Q's 8-B shares, class mask, class filter, bitmap layout (every word), write during lookup, sweep after reset, byte strobes, packet forwarding, latency, reset in a packet | `tb/unit/test_itch_decoder.py` |
| 10⁵ random messages against the model with config changes, bitmap writes in the lookup's cycle and reads, coverage gate (133 bins); line rate, one message per cycle, zero loss | `tc_random_golden`, `tc_line_rate` |
| Assertions (16, bound in every core build) and their own tests | `tb/sva/itch_props.sv`, `tb/sv/tb_itch_props.sv`, `tb/unit/test_sva_checkers.py` |
| System: `eth_ip_udp_rx` → `moldudp64_rx` → `itch_decoder` → `hft_csr_wrap`; every message, result and event against the chain model; hand-counted bring-up (locates learned from Stock Directory events); 2000 frames of real ITCH with ITCH_CTRL commits, bitmap writes and reads, resyncs, snapshots | `tb/system/test_rx_csr.py` (`tc_itch_bring_up`, `tc_itch_random`) |

### Synthesis (OOC, `just synth-core`, placed and routed)

Harness `rtl/platform/synth_ooc/itch_decoder_ooc.sv` (inputs registered).

| Part | Clock | LUT | FF | BRAM | WNS (ns) | WHS (ns) | Gate |
|------|-------|-----|----|------|----------|----------|------|
| `xc7a100tcsg324-1` (Artix-7) | 156.25 MHz | 635 | 3115 | 2 × RAMB36 | +0.978 (synthesis +1.145) | +0.100 | PASS |
| `xcku5p-ffvb676-2-e` (Kintex UltraScale+) | 156.25 MHz | 634 | 3115 | 2 × RAMB36 | +4.097 (synthesis +4.751) | +0.045 | PASS |

Most FFs are output registers (`m_ev_raw` alone is 512).

### Out of scope

The order book (order_ref → price, side, shares, for E/X/D/U) is a separate decision before F7. The host ring
entry is `event_packer`'s (below). Filtering by symbol name: the host maps names to
locates (Stock Directory messages or Nasdaq's daily locate files) and writes the bitmap. Field content validation.

## Host event ring: `event_packer`, `cdc_async_fifo` (`COMMIT=1`), `event_ring_writer`

```
itch_decoder (net_clk) --m_ev_*, m_pkt_*--> event_packer (net_clk) --1024-bit entries, commit / rollback-->
  cdc_async_fifo COMMIT=1 (256 entries) --> event_ring_writer (host_clk) --m_axi_hmw_* (AXI4 write)--> host RAM ring
```

The host gets every output event in host memory, in order, whole per packet, without reading a register per event
(legacy K9, M1). Entries of a packet become visible only when the packet is accepted (store and forward: at most one
packet time, about 1.2 µs for 1472 B, on the host path; the order logic of F7 does not take this path). The system
top `tb/system/rx_csr_top.sv` joins the whole chain.

### Entry format

128 bytes, little-endian, two 64-byte lines. The last byte of each line (63, 127) is a `ctrl` byte with the same
value in both: bits 3:0 the format version (1), bit 7 the phase. The layout has one source,
[`firmware/formats/event_desc.toml`](../../formats/event_desc.toml); `just gen` writes `gen/event_desc_pkg.sv` (pack
functions), [`software/include/gen/event_desc.h`](../../../software/include/gen/event_desc.h) (C++ structs with
`static_assert` on every offset) and the Python model `tb/model/desc_gen.py`, and checks the schema rules (fields
inside the entry, no overlap, natural alignment, nothing on a `ctrl` byte). Every entry has `seq`, `sof_ts`,
`ring_idx`, `kind`, `flags` and `cfg_epoch`.

| `kind` | When | Content |
|--------|------|---------|
| `ITCH` (1) | An ORDER or TRADE message, outcome OUT | `itch_decoder`'s normalized fields (0 where they do not apply), alpha fields in wire order |
| `ITCH_RAW` (2) | An ADMIN, AUCTION, UNKNOWN or MALFORMED message, outcome OUT | The message bytes as received, 0 from `len` on |
| `GAP` (3) | An accepted packet with `gap` > 0 | `seq` = first missing, `first_seq` = the packet's, `gap` |
| `EOS` (4) | An accepted packet with status `EOS` | The packet's `seq` |

`flags.overflow_before` (bit 0): packets were lost between the previous committed entry and this one.

### Commit rules (`event_packer`)

- Only OUT messages make entries; FILTERED and MASKED ones are counted in the ITCH counters only.
- An accepted packet (`OK`, `HEARTBEAT`, `EOS`): its message entries, then a GAP entry if its gap is not 0, then an
  EOS entry if its status is `EOS`, then `fifo_commit`. A heartbeat with a gap writes only its GAP entry. Any other
  status: `fifo_rollback`, and nothing is counted (the `itch_stats` rule).
- **Drop, one place:** a full ring stops the writer, the FIFO fills, and a write that finds it full (or in reset)
  drops the packet: nothing more of it is written and it rolls back at its end. An accepted packet is then counted
  whole (`RING_DROP_PKTS`, `RING_DROP_ENTRIES`, GAP and EOS included) and the next committed entry carries
  `overflow_before`. A rejected packet that did not fit is no loss: not counted, no flag.
- **Capacity:** a packet has at most `RING_MAX_PKT_ENTRIES` = 183 entries (1472-byte payload, 20-byte header, at
  least 2 + 6 bytes per message: 181, plus GAP and EOS). The FIFO (256) holds one, so a packet always fits an empty
  FIFO. With the writer stopped the path holds the ring plus 256 entries (one more while the FIFO is backlogged:
  the writer keeps one entry in its register); `tc_ring_pressure` checks the count.

### Ring protocol (`event_ring_writer`)

- Entry `prod` goes to `base + (prod mod N) × 128` with `ring_idx` = `prod` and phase = `~bit(prod, log2 N)`: 1 on
  the first pass, so memory that starts at 0 holds no valid entry (the NVMe pattern).
- `prod` and `CONS_IDX` are 32-bit wrapping counts compared by difference: `prod − CONS_IDX = N` means full, and the
  writer waits; nothing is dropped at the ring.
- One AXI4 INCR burst per entry: `AWLEN` = 1024/`DATA_W` − 1, `AWSIZE` = log2(`DATA_W`/8), `WSTRB` all ones. AW and
  W go on their own; at most `MAX_OUTSTANDING` (4) bursts wait for their answer. The base is 4 KiB aligned, so no
  burst crosses 4 KiB.
- A BRESP other than OKAY counts in `RING_BRESP_ERR` and sets `RING_STATUS.bresp_err`; the write is not retried.
- Rate: one entry every 1024/`DATA_W` `host_clk` cycles while the ring has room (no gap in W), if the memory has
  AWREADY and WREADY high and answers in time; `tc_rate_contract` checks it.

### Host rules

1. The region is 2^`RING_SIZE_LOG2` entries of 128 bytes, `RING_SIZE_LOG2` in 4..20, aligned to 4 KiB (a hugepage in
   practice). Before **every** enable the host fills it with zeros and writes `CONS_IDX` = 0, then `RING_BASE_*`,
   `RING_SIZE_LOG2` and `RING_CTRL.enable` = 1. A rising edge of `enable` with `CONS_IDX` ≠ 0 or the size out of
   range is refused: `RING_STATUS.enable_err`, nothing is written; write 0, then 1 to retry. Base and size are taken
   on the accepted edge and ignored until the next one.
2. To read: poll the next slot; an entry is there when **both** `ctrl` bytes show the expected phase (PCIe does not
   order the bytes of one write in host memory). Read the `ctrl` bytes with acquire, then the fields; `ring_idx` is a
   further check.
3. Write `CONS_IDX` now and then (a posted write, e.g. every N/4 entries), never past `PROD_IDX`.
4. `overflow_before` on an entry: whole packets were lost before it, `RING_DROP_PKTS` and `RING_DROP_ENTRIES` count
   them. A GAP entry is a gap of the feed, not a loss of the ring.
5. `RING_WRITTEN` and `RING_BRESP_ERR` are live 64-bit counters in two registers: read HI, LO, HI and repeat if the
   two HI differ. The snapshot counters need no such rule.
6. A disable lets the bursts in flight finish and starts no new entry; the FIFO may fill meanwhile and drop.
7. `cfg_epoch` of an entry is the FEED commit count of its packet's first beat (see FEED): the host keeps the same
   count and so knows which configuration made each entry.

### Identities

In every snapshot `RING_COMMIT_ENTRIES` + `RING_DROP_ENTRIES` = `ITCH_OUT` + `MOLD_GAP_EVENTS` + `MOLD_EOS`. When the
FIFO and the bursts in flight are empty, `RING_COMMIT_ENTRIES` = `RING_WRITTEN` + `RING_BRESP_ERR` and `PROD_IDX` =
the entries written since the enable. The system tests check both.

### Latency

`event_packer`: `s_ev_valid` → `fifo_wr_en` 1 cycle; packet event → `fifo_commit` or `fifo_rollback` 1 cycle (2 with
GAP and EOS); counters 3 cycles after the event. FIFO: see its commit latency
([`../common/README.md`](../common/README.md)). `event_ring_writer`: FIFO pop → AWVALID and WVALID 2 cycles.

### Reset

`event_packer` and the FIFO's write side reset with `net_rst`, the writer with `host_rst` (AWVALID and WVALID go
low; reset the memory side with it). The entries of a packet cut by a reset are gone; the counters start again from
0.

### Verification

| What | Where |
|------|-------|
| Schema rules, hand-written hex entries of every kind, the C++ header compiled with `static_assert`s | `tb/selftest/test_event_desc.py`, `desc_hand.py`, `event_desc_check.cpp` |
| Ring model: `expect_entries` (commit, rollback, GAP and EOS order, drop and `overflow_before`, epoch), `RingConsumer` (both phases, `ctrl`, `ring_idx`), pacings | `tb/model/ring.py`, `tb/selftest/test_ring_model.py` |
| `COMMIT=1` FIFO at every clock profile: random commit and rollback, the reader sees only committed words, rollback at the full boundary, reset in the middle | `tb/unit/test_cdc_async_fifo_commit.py` |
| `event_packer`: every class × outcome, every status, GAP and EOS order, whole-packet drop, busy as full, `overflow_before`, largest packet into an empty FIFO, reset mid-packet; exact cycles of every write and end; 10⁵ random messages with a coverage gate | `tb/unit/test_event_packer.py` |
| `event_ring_writer` (`DATA_W` 64, 128, 256, 512): hand stamp, wrap and phase, full ring, indexes past 2³², enable rules and refusals, BRESP error, rate contract, outstanding limit, reset mid-burst; 10⁶ entries (`DATA_W` 512) and 10⁵ (64) lossless in order (`long`, K9) | `tb/unit/test_event_ring_writer.py` |
| Assertions (`packer_props` 8, `ring_props` 9, `axi_wr_props` 8: 4 KiB, beat count, channel stability, B after AW, VALID low after reset), each violation firing only its own | `tb/sva/`, `tb/sv/`, `tb/unit/test_sva_checkers.py` |
| System: every entry read back from host memory through the ring protocol against the chain model, byte by byte; the ring identity in every snapshot; bring-up, 10⁴-message replay, gap and recovery, ring pressure, refused enable and BRESP error; `tc_itch_random` with a 64-entry ring and a consumer that stops, runs, slows and pauses; real Nasdaq windows with no loss (M1) | `tb/system/test_rx_csr.py` |
| 25 mutants of the ring path, all caught | `audit/f6-mutasyon/s9_mut.py` |

### Synthesis (OOC, `just synth-ooc-on <part> event_path_ooc <profile> core.f synth_ooc_core.f impl`, placed and routed)

Harness `rtl/platform/synth_ooc/event_path_ooc.sv` (packer, `COMMIT=1` FIFO of 256 entries, writer; inputs
registered).

| Part | net / host clock | `DATA_W` | LUT | LUTRAM | FF | BRAM | WNS (ns) | WHS (ns) | Gate |
|------|------------------|----------|-----|--------|----|------|----------|----------|------|
| `xcku5p-ffvb676-2-e` (Kintex UltraScale+) | 156.25 / 250 MHz | 512 | 2211 | 3672 | 4653 | 0 | +0.855 (synthesis +2.058) | +0.011 | PASS |
| `xc7a100tcsg324-1` (Artix-7) | 156.25 / 125 MHz | 512 | 2206 | 4740 | 4683 | 0 | +0.353 (synthesis +1.207) | +0.053 | PASS |
| `xc7a100tcsg324-1` | 156.25 / 125 MHz | 128 | 2590 | 4740 | 4697 | 0 | +0.219 (synthesis +1.207) | +0.053 | PASS |

The FIFO is LUTRAM (`ram_style = "distributed"`, about a fifth of the Artix-7's memory LUTs), no BRAM. The host
side's worst path is the LUTRAM read into the writer's input register, so the writer stamps the word only after
that register. `report_cdc`: CDC-6 and CDC-15 (the FIFO's data, constrained), no critical finding.

### Out of scope

An interrupt (`irq`) is F8's. Line-rate stress with IPG 0 and the clock ratio matrix of audit/04 §3.4 (scenarios 8
and 9) are left for the platform phases.

## CSR block: `hft_csr`, `hft_csr_wrap`, `rx_stats`, `mold_stats`, `itch_stats`

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
| `0x0000` | GLOBAL | `ID` (RO, `0x48465431` "HFT1"), `VERSION` (RO, `0x0005_0003`), `SCRATCH` (RW) |
| `0x0100` | NET | `NET_CTRL` (`rx_en` bit 0, `accept_bcast` bit 1), `LOCAL_MAC_LO` (MAC[31:0]), `LOCAL_MAC_HI` (MAC[47:32]), `NET_COMMIT` (WO), `NET_STATUS` (RO, `commit_busy`), `FILTER[i]` at `0x0120 + 8i`: `IP`, `PORT_EN` (port bits 15:0, enable bit 16) |
| `0x0200` | FEED | Shadow: `FEED_CTRL` (`enable` bit 0, `session_check` bit 1, `resync_learn` bit 2, `filter_mask` bits 15:8), `SESSION_0..2` at `0x0204` (session bytes 0–3, byte 0 = bits 31:24; 4–7; 8–9 = bits 31:16), `RESYNC_SEQ_LO/HI` at `0x0210`. `FEED_COMMIT` at `0x0218` (WO, `commit` bit 0, `resync` bit 1), `FEED_STATUS` (RO, `commit_busy`). Snapshot: `EXPECTED_SEQ` (`LO`/`HI`) at `0x0220`, `FEED_STATE` at `0x0228` (`synced` bit 0, `eos` bit 1), `MOLD_<status>` at `0x0240 + 8 × status`, then `MOLD_MSGS`, `MOLD_DUP_MSGS`, `MOLD_OVERSIZE_MSGS`, `MOLD_GAP_EVENTS`, `MOLD_GAP_MSGS` at `0x0298`..`0x02B8` |
| `0x0400` | STATS | `STATS_CTRL` (WO, `snapshot`), `STATS_SEQ` (RO), eleven 64-bit counters as `LO`/`HI` pairs from `0x0410`: `RX_PKTS`, `RX_BYTES`, then `DROP_<reason>` at `0x0420 + 8 × (reason − 1)` |
| `0x0500` | RISK | reserved (F7) |
| `0x0600` | ITCH | Shadow (carried by `FEED_COMMIT`): `ITCH_CTRL` (`class_en` bits 5:0, `class_filter` bits 13:8; bit = `itch_class_e` code). Snapshot: `ITCH_<class>` at `0x0640 + 8 × class`, then `ITCH_OUT`, `ITCH_FILTERED`, `ITCH_MASKED` at `0x0670`, `0x0678`, `0x0680` |
| `0x0800` | RING | Host domain, no commit: `RING_CTRL` (`enable`), `RING_BASE_LO` (bits 31:12), `RING_BASE_HI`, `RING_SIZE_LOG2`, `CONS_IDX` (RW), `PROD_IDX` (RO, live), `RING_STATUS` (RO: `bresp_err` bit 0, `enable_err` bit 1), live counters `RING_WRITTEN` at `0x0820` and `RING_BRESP_ERR` at `0x0828`. Snapshot: `RING_COMMIT_ENTRIES`, `RING_DROP_PKTS`, `RING_DROP_ENTRIES` from `0x0840`. Alone in its 512-byte read window (read path timing, audit/13 S8). See Host event ring |
| `0x1000`+ | STRATEGY, TX | reserved (F7) |
| `0x8000` | SYMBOL | Memory, 2048 × 32 bit, RW: bit i of word w = stock locate 32w + i (1 = subscribed) |

Software never writes a field that hardware also writes: shadow, command and status fields are in separate
registers, so a hardware update cannot overwrite a host write (legacy K5). A selftest checks this rule on the RDL.
For the same reason the resync value (`RESYNC_SEQ`, written by software) and the expected sequence number
(`EXPECTED_SEQ`, written by hardware) are two registers.

`VERSION` is major.minor: a minor step only adds registers (software written for an older minor still works), a
major step changes existing ones. The F3 FEED block made it `0x0005_0001`, the F4 ITCH block and SYMBOL
`0x0005_0002`, the F6 RING block `0x0005_0003`.

The generated read mux has one register stage per 512-byte window (`--rt-read-fanin`), and the logic in front of it
grows with the registers in that window: RING at `0x0300`, in FEED's window, failed the Artix-7 125 MHz gate. A new
block goes in a window with room; `test_read_windows_stay_small` allows at most 43 registers in one.

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

The FEED registers (`FEED_CTRL`, `SESSION_*`, `RESYNC_SEQ_*`) and `ITCH_CTRL` are a shadow like NET, with their
own commit buffer and `cdc_handshake` (176 bits, with `cfg_epoch`). `FEED_COMMIT.commit` sends them; `moldudp64_rx` takes its `cfg_*`
at the first beat of each packet and carries `ITCH_CTRL` with the packet's messages (`cfg_msg_ctx`), so a packet
sees the old or the new configuration as a whole, in both blocks. `FEED_STATUS.commit_busy`, coalescing and
latency are as for NET. After reset `cfg_feed_en`, `cfg_session_check` and `cfg_class_en` are 0 until the first
commit; the feed counts every packet as `DISABLED` and the decoder every message as `MASKED` until then.

`FEED_COMMIT.resync` commits in the same way and asks `moldudp64_rx` to resync with the `resync_learn` and
`RESYNC_SEQ` of that commit (learn the next packet's `seq`, or set E). It works like a self-clearing command bit:
- the request is the rising edge of the field (regblock may hold a singlepulse field one more cycle; that must not
  make a second resync, since a resync is not idempotent);
- it sets the buffer's resync bit, which stays set until a transfer carries it. A request while one is pending or
  in flight merges with it: never lost, one resync with the newest shadow;
- at net, `cfg_resync` is a 1-cycle pulse in the cycle the transfer lands. `moldudp64_rx` applies it between
  packets or after the packet in progress (see its resync rules).

Software that wants two separate resyncs waits for `commit_busy` = 0 between them.

**`cfg_epoch`.** Every `FEED_COMMIT` write with `commit` or `resync` set adds 1 to an 8-bit count (wrapping), kept in
the FEED configuration: it changes with the other FEED `cfg_*`, and `moldudp64_rx` carries it with each packet
(`cfg_msg_ctx`) into every ring entry. Every write counts, also two in consecutive bus cycles and commits that
coalesce; a write with byte 0 disabled does not (user decision, audit/13 S6). The host keeps the same count.

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

`itch_stats` counts only committed messages, as `MOLD_MSGS` does: each result of `itch_decoder` adds to 16-bit
pending counts (one per class and per outcome); the forwarded packet event adds them to the counters for `OK`,
`HEARTBEAT` and `EOS` and drops them otherwise. A result in the same cycle as a packet event belongs to the next
packet. A packet has fewer than 2¹⁶ messages (at most 181 in a 1472-byte payload); an assertion
(`itch_stats_props`) checks the pending limit. The ITCH counters are in the same snapshot. Both counter banks count
the packet events `itch_decoder` forwards (2 cycles after `moldudp64_rx`'s), so `FEED_DELAY` was 4 (F4; F6 makes it 6, below). In every
snapshot:

Σ `ITCH_<class>` = `MOLD_MSGS`, and `ITCH_OUT` + `ITCH_FILTERED` + `ITCH_MASKED` = Σ `ITCH_<class>`

(the system test checks both in every snapshot).

The ring counters `RING_COMMIT_ENTRIES`, `RING_DROP_PKTS` and `RING_DROP_ENTRIES` (`event_packer`, net clock) are in
the same snapshot. `event_packer` moves them 3 cycles after the forwarded packet event, so the wrapper delays the
packet events and results by `STATS_DELAY` = 2 cycles before `mold_stats` and `itch_stats`, and `feed_*` by
`FEED_DELAY` = 6: in every snapshot every bank and the feed state belong to the same packet events (user decision,
audit/13 S6). Only the counter inputs are delayed, not the data path. In every snapshot:

`RING_COMMIT_ENTRIES` + `RING_DROP_ENTRIES` = `ITCH_OUT` + `MOLD_GAP_EVENTS` + `MOLD_EOS`

(every OUT message, GAP and EOS of an accepted packet is committed or dropped; the system tests check it in every
snapshot).

### Symbol bitmap (SYMBOL)

A SYMBOL access (regblock external memory, one access in flight) goes to `itch_decoder`'s command port: the
request (write flag, word address, data, byte strobes) over one `cdc_handshake` (48 bits, host → net), the
response (read data) back over another (32 bits). The read or write is acknowledged when the response lands, so
a write's BRESP means it is in the bitmap: every message looked up after it sees the new value.

Latency (handshakes free, `itch_decoder` ready): request → transfer accepted 2 `host_clk` edges; accepted →
command at the port `STAGES` + 2 `net_clk` edges; response → accepted 2 `net_clk` edges; accepted → ack
`STAGES` + 1 `host_clk` edges (each crossing one edge more with CDC jitter); regblock adds its own response cycle.

During the sweep after a reset (about 13.1 µs) an access waits at the port, and the bus with it: regblock keeps
one access in flight, so other CSR accesses wait too. This is far below a PCIe completion timeout (50 µs at the
least).

### Kill switch latency (`RISK_CTRL.kill`)

`RISK_CTRL.kill` is not a shadow and has no commit: it reaches `ctl_kill` (`strategy_engine` and `risk_check`)
through a `sync_bit`, `STAGES` `net_clk` edges after the register changes (one more with CDC jitter). It is a bus
write like any other, though: regblock keeps one external access in flight and holds every other access behind
it, and the order path commands (RULE_*, RISK_COMMIT, BUDGET_ADD, TX_COMMIT, TX_USERREF*, LOCATE_ALLOW) are external
accesses answered once in effect. A kill write behind one of them waits for it. There is no bypass (decision 38): a
reset sweep runs with kill already 1, and live the wait is the command's round trip, plus its fence when it is
fenced.

Measured in `test_hft_csr_wrap.tc_kill_latency` (all five clock profiles), from the write's W handshake to
`ctl_kill` = 1:

| Kill write | Time | Conditions |
|---|---|---|
| on an idle bus | 9.8–23.6 ns | |
| behind a command in flight | 69–106 ns | mailbox empty, so the command is not fenced |
| behind a command fenced by a full mailbox | 158–232 ns | 16 orders, `risk_check` taking one every cycle, no hardware order in between |

The last row is the best case of that situation, not its worst. A fenced command waits until every earlier
`MBOX_SEND` has been taken by `risk_check` and decided, and `risk_check` takes a host order only in a cycle with
no hardware order and no cancel to write out, so hardware orders and cancels in that time lengthen the wait. They
are bounded (an analysis from the blocks' contracts, not yet a measurement; S8 checks it with the real blocks):
while the command waits the bus holds every other access, so no order is sent, no rule is armed and kill and
tx_en do not change. At most 16 hardware orders are queued in `strategy_engine` (8 waiting for their packet, 8
leaving), and each rule fires at most once more (a rule fires once per packet and its packet's acceptance
disarms it; decision 18 keeps a rule armed only for a fire already queued), so at most 24 hardware orders reach
`risk_check`. Cancels still to be written are at most 8 (held + live <= 8). Each hardware order takes one cycle
and its reject can delay one cancel write by a cycle: at most 24 + 8 + 24 = 56 net cycles (about 360 ns) on top
of the case measured above. A kill written behind such a command waits with it. Right after a reset the first command also waits for the mailbox FIFO's reset handshake (a few cycles;
kill is 1 then anyway).

### Software contract after reset

A reset (power-on, and later a platform or PCIe reset; host and net reset together) returns the whole
configuration to its reset values: `rx_en` = 0, `feed_en` = 0, `class_en` = 0, every shadow register to its
reset value, the ring off (`RING_CTRL.enable` = 0), and the SYMBOL bitmap to 0 (the sweep). Until software configures the device again, the receiver
drops every frame (`DISABLED`), the feed counts every packet as `DISABLED`, and no ITCH message is output; a
filtered class would drop its messages without an error, counted only in `ITCH_FILTERED`. The counters start
again from 0.

This is the standard device contract: a device reset clears the configuration and the driver restores it (NDIS
`NdisMResetComplete` with `AddressingReset`; Linux NIC drivers rewrite their filter lists after a reset). The bitmap
cannot be kept over a reset: block RAM content has no reset, and a value that looked kept would be undefined.

The driver keeps its own copy of the configuration (it is the only source; it does not read the device back) and
after every reset, in this order:
1. checks `ID` and `VERSION`;
2. writes the NET shadow and `NET_COMMIT`;
3. writes all 2048 `SYMBOL` words, the unsubscribed ones included (the first write waits until the sweep is done);
4. sets up the ring by the host rules of the Host event ring (zeroed region, base, size, `CONS_IDX` = 0, enable)
   and resets its own consumer index and epoch count;
5. writes the FEED shadow with `ITCH_CTRL` and `FEED_COMMIT`, with a resync if needed;
6. waits for `commit_busy` = 0 after each commit.

How the host learns about a reset (a platform reset notification, or a reset counter register) is F8's.

### Clocks and reset

Two domains: `host_clk`/`host_rst` (bus, register block, commit buffers) and `net_clk`/`net_rst` (`cfg_*`,
`rx_stats`, `mold_stats`, `itch_stats`, the symbol command port). Both resets come from one source through one
`reset_sync` per domain and are asserted together (`cdc_handshake` contract). The only crossings are six
`cdc_handshake` instances: NET config (442 bits, host → net), FEED config with `ITCH_CTRL` and `cfg_epoch` (176 bits, host → net),
snapshot request (1 bit, host → net), snapshot (2562 bits, net → host: 11 + 16 + 9 + 3 counters, expected `seq`,
`synced`, `eos`), symbol request (48 bits, host → net) and symbol response (32 bits, net → host).

In simulation, `assert_arm` (bound to `hft_csr`) keeps every assertion off until the first reset has been seen
and released: Verilator keeps each instance's clock input as its own variable, and random power-up values can
give a rising edge at time 0 while every flop still holds its random state. The hand-written checkers are armed by
their own first reset; regblock's generated assertions are not.

### Verification

| What | Where |
|------|-------|
| Map: every address against a hand-written table, reason-code, status-code and class-code formulas, the SYMBOL memory, access kinds, the K5 rule (and that it bites), C++20 compile of the header with hand-written `offsetof` asserts | `tb/selftest/test_csr_map.py`, `tb/selftest/csr_header_check.cpp` |
| Register model built from the RDL (not from the generated SV), memories included | `tb/model/csr.py` |
| Bus: reset values, every register, every `wstrb`, `SLVERR` cases, K6/K7/K8 regressions, SYMBOL through a RAM responder (every `wstrb`, one access in flight), resets with traffic in flight (`AxilDriver.reset`), random traffic with a coverage gate | `tb/unit/test_hft_csr.py` (AXI driver `tb/common/axil.py`) |
| AXI4-Lite assertions (bound to `hft_csr`) and their own tests | `tb/sva/axil_props.sv`, `tb/sv/tb_axil_props.sv` |
| Counters: every edge against a model, every reason, wrap (8-bit build), carry past 2³² | `tb/unit/test_rx_stats.py` |
| Feed counters: every edge against a model, every status into its own counter only, wrap (8-bit build), gap sum past 2³² and 2⁶⁴ | `tb/unit/test_mold_stats.py` |
| ITCH counters: every edge against a model, commit and drop for every status, IPG 0 boundary, wrap (8-bit build), carry past 2³² and wrap at 2⁶⁴ (backdoor write of the counter register), pending counts at 65535; the pending limit assertion | `tb/unit/test_itch_stats.py`, `tb/sva/itch_stats_props.sv` |
| Commit and snapshot at five clock/jitter profiles: exact latencies, atomicity monitor on every net edge, coalescing, snapshot = counters of its acceptance edge, K5 writes while snapshots land, LO/HI past 2³² | `tb/unit/test_hft_csr_wrap.py` (harness `tb/common/csr_env.py`) |
| FEED at the same five profiles: commit latency and atomicity (also a shadow written while a commit waits, `ITCH_CTRL` included), resync requests merged and never lost (also one made while a commit waits), a held singlepulse gives one resync, reset during a commit, feed state aligned with the counters in every snapshot phase | `tb/unit/test_hft_csr_wrap.py` (`tc_feed_*`, `tc_itch_ctrl_feed_commit`, `tc_snapshot_feed_aligned`) |
| SYMBOL at the same five profiles, against a model of the decoder's command port with backpressure: every access reaches the port once, in order, unchanged; exact latencies; BRESP after the write is in effect; waiting during the sweep; resets while a command waits and in flight (10 each) | `tb/unit/test_hft_csr_wrap.py` (`tc_sym_*`) |
| RING (F6): every register through the bus, `PROD_IDX`, status and live counters following their inputs; the ring counters aligned with the ITCH and MOLD counters at every cycle around a packet event; `cfg_epoch` against hand-written counts (consecutive writes, coalesced commits, a held pulse, a write of 0, reset) | `tb/unit/test_hft_csr_wrap.py` |
| System: `eth_ip_udp_rx` + `moldudp64_rx` + `itch_decoder` + `hft_csr_wrap`, each frame against the F2 model, each payload against the MoldUDP64 model and each message against the ITCH model under the configuration it used; 2000 random frames with random commits and snapshots, 2000 feed frames with resyncs and the accounting identity, 2000 ITCH frames with bitmap and `ITCH_CTRL` changes and the ITCH identities | `tb/system/test_rx_csr.py` |

### Synthesis (`just synth-csr`, OOC place & route)

The harness `rtl/platform/synth_ooc/hft_csr_wrap_ooc.sv` registers every input in its own clock domain. The two
gates are placed and routed out of context (no board or pins); the table also gives the synthesis estimate.

| Part | net / host clock | Flow | LUT | FF | WNS (ns) | WHS (ns) | Gate |
|------|------------------|------|-----|----|----------|----------|------|
| `xcku5p-ffvb676-2-e` (Kintex UltraScale+) | 156.25 / 250 MHz | routed | 3641 | 11838 | +0.767 (synthesis +1.607) | +0.042 | PASS |
| `xc7a100tcsg324-1` (Artix-7) | 156.25 / 125 MHz | routed | 3639 | 11836 | +0.129 (synthesis +0.422) | +0.090 | PASS |
| `xc7a100tcsg324-1` | 156.25 / 250 MHz | synthesis | 3644 | 11836 | −3.532 | +0.164 | information only |

F5 alone was 1329 LUT, 4349 FF (Artix-7 margin +0.324 ns); the F3 FEED block made it 2460 / 8405, the F4 ITCH block,
SYMBOL path and 2370-bit snapshot 3426 / 10697, the F6 RING block, ring counters and the counter alignment 3639 /
11836. The Artix-7 host read path is the limit: RING in FEED's read window failed it by 0.234 ns, alone in its own
window it passes (above, Map). regblock `--rt-read-response` does not help this path (it ends before the read mux's
register).

`report_cdc`: CDC-3 (synchronizers, 12) and CDC-15 (handshake data captured with an enable, constrained by
`cdc.tcl`, 3260 bits), no critical finding. The Kintex UltraScale+ part is the class of a real 10G + PCIe Gen3 x8 card (250 MHz PCIe
user clock); the Artix-7 at 125 MHz is the PCIe Gen2 x4 (128-bit) configuration of a low-cost card. The register
read path does not reach 250 MHz on the Artix-7; the part is not upgraded to hide that.
