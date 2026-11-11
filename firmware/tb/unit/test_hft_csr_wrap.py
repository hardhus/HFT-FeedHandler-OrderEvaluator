"""hft_csr_wrap: config commit from the host CSR block to the net clock domain (audit/10 §2.3, S4)
and the counter snapshot back (§2.4, S5).

Clocks: the RATIO profile gives (net period, host period): net2host = 156.25 / 250 MHz (the real
pair), host2net = net faster than host (e.g. a 125 MHz host on the A7 profile is still slower than
net; this checks the other order), jitter = both clocks drifting. Both resets are asserted together
and released in their own domain (one source, one reset_sync per domain).

A monitor samples every cfg_* output on every net_clk edge. The receiver may only ever see the
configurations software committed, each whole, in commit order (a coalesced commit may skip
older ones), never a shadow write that no commit covered.

FEED: the same monitor idea for cfg_feed_* and cfg_resync (csr_env.Feed): only committed FEED
configurations, in order; cfg_resync exactly in the landing cycle of a transfer that carries a
resync request, every request carried by exactly one transfer (requests that meet merge, none is
lost, none appears from nowhere).

Counters: a generator drives stat_*, mpkt_* and mres_* on net_clk and moves feed_*; a monitor
records the edge that samples each event. A snapshot taken on acceptance edge E must equal the sum
of the events sampled before E, exactly (the event sampled on E belongs to the next snapshot), for
the rx_stats, mold_stats and itch_stats counters (ITCH: committed results only), and the feed state
must be the one sampled FEED_DELAY = 6 net edges before E (it then belongs to the same packet
events as the counters). The acceptance and landing edges are observed on the snapshot handshake
inside the wrapper (grey box). The bus side is the generated block (tested in test_hft_csr.py);
axil_props is bound to it here too. The harness (Wrap, Snap, Sym, NetCfg, FeedCfg) is in
common/csr_env.py, shared with the system test tb/system/test_rx_csr.py.

ITCH_CTRL is part of the FEED configuration (FeedCfg.class_en/class_filter), so every FEED test
also checks that it moves with FEED_COMMIT, whole. SYMBOL: a model of itch_decoder's command port
(csr_env.Sym) answers with random backpressure; every access must reach the port once, in order and
unchanged, and its ack must come after the command was taken (BRESP = in effect).

Order path (audit/14 S7, decisions 32-38): csr_env.Ord models strategy_engine, risk_check and order_tx_builder
at their command ports with their documented timing, reads the mailbox and answers the allow bitmap. Every
command must reach net once, in bus order, whole, with its write response after its effect; the fence must keep
it behind every earlier MBOX_SEND (decided) and behind earlier kill/tx_en writes; TX_COMMIT goes only with the
order path closed; the status bits are the result of the last command of their kind; the kill latency is
measured on an idle bus and behind a command (README).
"""

import os
from dataclasses import replace

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, gather
from cocotb.utils import get_sim_time

from common.clocks import PROFILES, profile, start_clock
from common.csr_env import (
    COMMANDS,
    LOCATE_ALLOW,
    MBOX_SEND,
    MBOX_SPACE,
    MBOX_STATUS,
    ORD_NAMES,
    RISK_CTRL,
    RULE_STATUS,
    TX_CTRL,
    TX_STATUS,
    TX_USERREF,
    FEED_COMMIT,
    FEED_DELAY,
    ITCH_CTRL,
    MASK64,
    N_CNT,
    SNAP_NAMES,
    SCRATCH,
    STATS_CTRL,
    SYMBOL,
    STATS_DELAY,
    FeedCfg,
    LimCfg,
    MboxOrder,
    NetCfg,
    Ord,
    RuleCfg,
    Snap,
    Sym,
    TxCfg,
    Wrap,
    le_int,
)
from common.runner import run_cocotb
from model.csr import OKAY, CsrModel, addr, wstrb_mask
from model.itch import ItchClass, Outcome
from model.mold import MoldStatus
from model.net import RxReason

RUNTIME_PARAMS = ("ratio", "group")
STAGES = 2


def jitter() -> bool:
    """CDC jitter model in the build, or drifting clocks: latencies may be one edge longer."""
    return os.environ["JITTER"] == "1" or os.environ["RATIO"] == "jitter"


async def reset(dut) -> None:
    """Both resets asserted together, released in their own domains (one source, two reset_syncs)."""
    dut.host_rst.value = 1
    dut.net_rst.value = 1
    for _ in range(STAGES + 4):
        await RisingEdge(dut.host_clk)
        await RisingEdge(dut.net_clk)

    async def release(clk, rst):
        await FallingEdge(clk)
        rst.value = 0

    await gather(release(dut.host_clk, dut.host_rst), release(dut.net_clk, dut.net_rst))
    await RisingEdge(dut.host_clk)


async def start(dut) -> Wrap:
    """Idle bus and stat inputs, both resets, then both clocks (no edge with undriven inputs)."""
    net_ps, host_ps, jit = profile()
    for name in ("awvalid", "wvalid", "bready", "arvalid", "rready"):
        getattr(dut, f"s_axil_{name}").value = 0
    dut.stat_valid.value = 0
    dut.stat_reason.value = 0
    dut.stat_bytes.value = 0
    for name in ("mpkt_valid", "mpkt_status", "mpkt_gap", "mpkt_msgs", "mpkt_dup_msgs", "mpkt_oversize",
                 "mres_valid", "mres_class", "mres_outcome", "feed_expected_seq", "feed_synced", "feed_eos",
                 "sym_req_ready", "sym_rsp_valid", "sym_rsp_rdata", "host_ring_prod_idx", "host_ring_bresp_err",
                 "host_ring_enable_err", "host_ring_cnt_written", "host_ring_cnt_bresp_err",
                 "cnt_ring_commit_entries", "cnt_ring_drop_pkts", "cnt_ring_drop_entries", "st_armed", "st_error",
                 "ev_userref_ignored", "st_userref", "st_budget", "loc_req_ready", "loc_rsp_valid", "loc_rsp_rdata",
                 "st_tx_cfg_valid", "ev_cfg_refused", "m_host_ready", "ord_fire_valid", "ord_fire_rule",
                 "ord_sent_valid", "ord_sent_source", "ord_rej_valid", "ord_rej_reason", "ord_lost_valid",
                 "ord_lost_reason", "ord_queued"):  # fmt: skip
        getattr(dut, name).value = 0
    dut.host_rst.value = 1
    dut.net_rst.value = 1
    start_clock(dut.net_clk, net_ps, jit)
    start_clock(dut.host_clk, host_ps, jit)
    await reset(dut)
    w = Wrap(dut)
    w.snap = Snap(w)
    w.sym = Sym(w)
    w.ord = Ord(w)
    return w


# ---------------------------------------------------------------------------------------------


@cocotb.test(timeout_time=1, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_reset_defaults(dut):
    """After reset: cfg_rx_en = 0 (receiver off) on every net edge, commit_busy = 0, and writes
    without a commit change nothing in the net domain."""
    w = await start(dut)
    assert await w.busy() == 0
    await w.write_cfg(replace(NetCfg.random(w.rng), rx_en=1))
    for _ in range(50):
        await RisingEdge(dut.net_clk)
        await ReadOnly()
        assert dut.cfg_rx_en.value == 0, "rx_en reached the receiver without a commit"
    assert not w.seen


@cocotb.test(timeout_time=2, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_commit_latency(dut):
    """NET_COMMIT pulse -> handshake acceptance: exactly 2 host edges (handshake free);
    acceptance -> cfg_* changed: exactly STAGES+1 net edges (or +1 with jitter). 20 commits at
    random phases between the clocks."""
    w = await start(dut)
    host, net = set(), set()
    for k in range(20):
        await w.write_cfg(NetCfg.random(w.rng))
        n_seen = len(w.seen)
        await w.commit()
        await w.wait_idle()
        assert await w.applied() == w.committed[-1]
        assert len(w.seen) == n_seen + 1, "one commit, one change"
        pulse, accept, change = w.pulses[-1], w.accepts[-1], w.seen[-1][0]
        host.add(sum(pulse < t <= accept for t in w.host_edges))
        net.add(sum(accept < t <= change for t in w.net_edges))
        for _ in range(w.rng.randint(0, 7)):  # move the phase between the clocks
            await RisingEdge(dut.host_clk)
    assert len(w.pulses) == len(w.accepts) == 20
    assert host == {2}, f"host edges pulse -> acceptance: {sorted(host)}"
    allowed = {STAGES + 1, STAGES + 2} if jitter() else {STAGES + 1}
    assert net <= allowed, f"net edges acceptance -> cfg change: {sorted(net)}, documented {sorted(allowed)}"


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_commit_atomic(dut):
    """100 random configurations, each written in random order and committed, sometimes waiting
    for commit_busy = 0, sometimes not. The monitor checks every net edge: cfg_* is always one
    committed configuration, whole, in commit order. The last one is applied at the end."""
    w = await start(dut)
    for _ in range(100):
        await w.write_cfg(NetCfg.random(w.rng))
        await w.commit()
        if w.rng.random() < 0.5:
            await w.wait_idle()
            assert await w.applied() == w.committed[-1]
    await w.wait_idle()
    assert await w.applied() == w.committed[-1]
    assert len(w.seen) >= 50


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_commit_while_busy(dut):
    """Commits while the handshake is busy: bursts of 2..6 commits with 0..3 shadow writes between
    them. No commit is lost (the newest is applied), coalesced commits skip older configurations,
    each committed shadow arrives whole, shadow writes after the last commit of a burst never reach
    cfg_*, and commit_busy reads 1 while a commit is in flight."""
    w = await start(dut)
    busy_seen = 0
    regs = [a for a, _ in NetCfg().writes()]
    for _ in range(40):
        await w.write_cfg(NetCfg.random(w.rng))
        for _ in range(w.rng.randint(2, 6)):
            await w.commit()
            for _ in range(w.rng.randint(0, 3)):
                await w.write(w.rng.choice(regs), w.rng.getrandbits(32))
        busy_seen += await w.busy()
        for _ in range(w.rng.randint(1, 4)):  # not committed: must never reach cfg_*
            await w.write(w.rng.choice(regs), w.rng.getrandbits(32))
        await w.wait_idle()
        assert await w.applied() == w.committed[-1], "the newest commit was not applied"
    assert busy_seen > 0
    transfers = len(w.accepts)
    assert transfers < len(w.committed), f"no commit coalesced ({transfers} transfers, {len(w.committed)} commits)"


@cocotb.test(timeout_time=2, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_shadow_readback(dut):
    """Reads return the shadow (what was written) before any commit, and cfg_* does not change
    until NET_COMMIT."""
    w = await start(dut)
    first = NetCfg.random(w.rng)
    await w.write_cfg(first)
    await w.commit()
    await w.wait_idle()
    second = NetCfg.random(w.rng)
    await w.write_cfg(second)
    for a, d in second.writes():
        assert await w.drv.read(a) == (d, OKAY), f"readback {a:#06x}"
    for _ in range(100):
        await RisingEdge(dut.net_clk)
    assert await w.applied() == first, "cfg_* changed without a commit"
    await w.commit()
    await w.wait_idle()
    assert await w.applied() == second


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_reset_during_commit(dut):
    """Both resets at a random point of a commit transfer: afterwards cfg_rx_en = 0 and stays 0
    (no stale transfer lands), commit_busy = 0, and the next commit works."""
    w = await start(dut)
    for _ in range(10):
        await w.write_cfg(replace(NetCfg.random(w.rng), rx_en=1))
        await w.commit()
        for _ in range(w.rng.randint(0, 3 * STAGES + 6)):
            await RisingEdge(dut.host_clk)
        await reset(dut)  # the driver is idle (the commit write has its B); it keeps the bus
        w.regs = {}
        for _ in range(4 * STAGES + 10):
            await RisingEdge(dut.net_clk)
            await ReadOnly()
            assert dut.cfg_rx_en.value == 0, "a transfer from before the reset enabled the receiver"
        await RisingEdge(dut.host_clk)
        assert await w.busy() == 0
        cfg = replace(NetCfg.random(w.rng), rx_en=1)
        await w.write_cfg(cfg)
        await w.commit()
        await w.wait_idle()
        assert await w.applied() == cfg


@cocotb.test(timeout_time=2, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_counters_before_snapshot(dut):
    """After reset, with traffic running: STATS_SEQ = 0 and every counter reads 0 until the first
    snapshot (the counters only change when a snapshot lands)."""
    w = await start(dut)
    w.snap.p_valid = 0.8
    for _ in range(3):
        for _ in range(30):
            await RisingEdge(dut.net_clk)
        assert await w.snap.seq() == 0
        assert await w.snap.read_counters() == [0] * N_CNT
    assert len(w.snap.events) > 50


@cocotb.test(timeout_time=2, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_snapshot_latency(dut):
    """Handshakes free: snapshot pulse -> request accepted 1 host edge; request at net -> counters
    taken 1 net edge. 10 snapshots at random phases."""
    w = await start(dut)
    w.snap.p_valid = 0.5
    host, net = set(), set()
    for _ in range(10):
        seq, got = await w.snap.snapshot()
        await w.snap.check(seq, got)
        host.add(sum(w.snap.pulses[-1] < t <= w.snap.req_accepts[-1] for t in w.host_edges))
        net.add(sum(w.snap.req_valid[-1] < t <= w.snap.accepts[-1] for t in w.net_edges))
        for _ in range(w.rng.randint(0, 7)):
            await RisingEdge(dut.host_clk)
    assert len(w.snap.pulses) == len(w.snap.req_accepts) == len(w.snap.accepts) == 10
    assert host == {1}, f"host edges pulse -> request accepted: {sorted(host)}"
    assert net == {1}, f"net edges request at net -> counters taken: {sorted(net)}"


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_snapshot_consistent(dut):
    """Random traffic (sparse to one event per cycle) and 40 snapshots: each equals, exactly, the
    counters of one net cycle (every event sampled before the acceptance edge): rx_stats, mold_stats
    and itch_stats (committed results only)."""
    w = await start(dut)
    for _ in range(40):
        w.snap.p_valid = w.rng.choice([0.05, 0.5, 1.0])
        for _ in range(w.rng.randint(0, 40)):
            await RisingEdge(dut.net_clk)
        seq, got = await w.snap.snapshot()
        await w.snap.check(seq, got)
    assert {r for _, r, _ in w.snap.events} == set(RxReason), "every counter moved"
    results = {res for _, res, _ in w.snap.itch_events if res}
    assert {c for c, _ in results} == set(ItchClass) and {o for _, o in results} == set(Outcome)


@cocotb.test(timeout_time=10, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_snapshot_boundary(dut):
    """An event on every net cycle: the event sampled on the acceptance edge is not in that
    snapshot (it is in the next one). The test checks that such an event existed each time."""
    w = await start(dut)
    w.snap.p_valid = 1.0
    for _ in range(20):
        seq, got = await w.snap.snapshot()
        await w.snap.check(seq, got)
        edge = w.snap.accepts[seq - 1]
        assert any(t == edge for t, _, _ in w.snap.events), "no event on the acceptance edge"


@cocotb.test(timeout_time=50, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_snapshot_feed_aligned(dut):
    """Regression (audit/11 S8 notes, audit/12 §2.7): the feed state in a snapshot belongs to
    the same packet events as the mold counters. Packet events as itch_decoder forwards them:
    feed_* show a packet's update 1 cycle before moldudp64_rx's packet event, which reaches mpkt_valid
    2 cycles later (OK with messages, a gap or oversize messages; E moves by all of them). From a
    synced start E0 with all counters 0, every snapshot must satisfy the accounting identity
    EXPECTED_SEQ - E0 = MOLD_MSGS + MOLD_OVERSIZE_MSGS + MOLD_GAP_MSGS (mod 2^64). Snapshots are
    taken until one was accepted on each edge from 1 before to FEED_DELAY after the edge that
    samples a feed update (the window in which a shorter delay takes a state one packet ahead of
    the counters)."""
    w = await start(dut)
    w.snap.drive = False
    idx = {n: i for i, n in enumerate(SNAP_NAMES)}
    e0 = w.rng.getrandbits(64)
    dut.feed_expected_seq.value = e0
    dut.feed_synced.value = 1
    for _ in range(4):
        await RisingEdge(dut.net_clk)
    updates: list[int] = []  # net edges that sample a feed update

    async def packets():
        e = e0
        while True:
            for _ in range(w.rng.randint(3, 7)):  # updates at least 7 edges apart
                await RisingEdge(dut.net_clk)
            msgs, over = w.rng.randint(0, 40), w.rng.choice([0, 0, 1, 3])
            gap = w.rng.choice([0, 0, 1, 1000, 1 << 40])
            e = (e + msgs + over + gap) & MASK64
            dut.feed_expected_seq.value = e
            await RisingEdge(dut.net_clk)
            updates.append(get_sim_time())
            for _ in range(2):  # moldudp64_rx's packet event, then itch_decoder's 2 cycles
                await RisingEdge(dut.net_clk)
            dut.mpkt_valid.value = 1
            dut.mpkt_status.value = int(MoldStatus.OK)
            dut.mpkt_gap.value = gap
            dut.mpkt_msgs.value = msgs
            dut.mpkt_oversize.value = over
            await RisingEdge(dut.net_clk)
            dut.mpkt_valid.value = 0

    cocotb.start_soon(packets())
    phases: set[int] = set()
    for _ in range(300):
        seq, got = await w.snap.snapshot()
        await w.snap.check(seq, got)
        edge = w.snap.edge_index[w.snap.accepts[seq - 1]]
        for u in updates:
            if -1 <= edge - w.snap.edge_index[u] <= FEED_DELAY:
                phases.add(edge - w.snap.edge_index[u])
        e = got[idx["FEED.EXPECTED_SEQ"]]
        moved = sum(got[idx[f"FEED.MOLD_{n}"]] for n in ("MSGS", "OVERSIZE_MSGS", "GAP_MSGS"))
        assert (e - e0) & MASK64 == moved & MASK64, (
            f"snapshot {seq}: EXPECTED_SEQ - E0 = {(e - e0) & MASK64}, counters account for {moved & MASK64}")
        assert got[idx["FEED.FEED_STATE"]] == 1, "synced"
        if phases == set(range(-1, FEED_DELAY + 1)):
            break
        for _ in range(w.rng.randint(0, 3)):
            await RisingEdge(dut.net_clk)
    assert phases == set(range(-1, FEED_DELAY + 1)), f"snapshot phases around a feed update: {sorted(phases)}"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_snapshot_ring_aligned(dut):
    """User decision (audit/13 S6): at every snapshot RING_COMMIT_ENTRIES + RING_DROP_ENTRIES = ITCH_OUT +
    MOLD_GAP_EVENTS + MOLD_EOS. Packet events as itch_decoder forwards them, the ring counters moving 3 cycles
    after each, as event_packer's do. Snapshots are taken until one was accepted on each edge from 1 before to
    STATS_DELAY + 3 after the edge that samples a packet event: the window in which a shorter delay takes the ITCH
    counters without the ring counters. Hand-counted accepted packets, some of them dropped whole."""
    w = await start(dut)
    w.snap.drive = False
    idx = {n: i for i, n in enumerate(SNAP_NAMES)}
    for _ in range(4):
        await RisingEdge(dut.net_clk)
    events: list[int] = []  # net edges that sample a packet event

    async def packets():
        commit = drop_pkts = drop_entries = 0
        while True:
            msgs = w.rng.randint(0, 3)
            for _ in range(msgs):
                dut.mres_valid.value = 1
                dut.mres_class.value = int(ItchClass.ORDER)
                dut.mres_outcome.value = int(Outcome.OUT)
                await RisingEdge(dut.net_clk)
            dut.mres_valid.value = 0
            status, gap = w.rng.choice([(MoldStatus.OK, 0), (MoldStatus.OK, 7), (MoldStatus.EOS, 0), (MoldStatus.EOS, 7)])
            dut.mpkt_valid.value = 1
            dut.mpkt_status.value = int(status)
            dut.mpkt_gap.value = gap
            await RisingEdge(dut.net_clk)
            events.append(get_sim_time())
            dut.mpkt_valid.value = 0
            for _ in range(2):
                await RisingEdge(dut.net_clk)
            entries = msgs + int(gap != 0) + int(status == MoldStatus.EOS)
            if w.rng.random() < 0.25:
                drop_pkts += 1
                drop_entries += entries
            else:
                commit += entries
            dut.cnt_ring_commit_entries.value = commit
            dut.cnt_ring_drop_pkts.value = drop_pkts
            dut.cnt_ring_drop_entries.value = drop_entries
            for _ in range(w.rng.randint(0, 3)):
                await RisingEdge(dut.net_clk)

    cocotb.start_soon(packets())
    want_phases = set(range(-1, STATS_DELAY + 4))
    phases: set[int] = set()
    for _ in range(600):
        seq, got = await w.snap.snapshot()
        await w.snap.check(seq, got)
        edge = w.snap.edge_index[w.snap.accepts[seq - 1]]
        phases |= {edge - w.snap.edge_index[e] for e in events if edge - w.snap.edge_index[e] in want_phases}
        left = got[idx["RING.RING_COMMIT_ENTRIES"]] + got[idx["RING.RING_DROP_ENTRIES"]]
        right = got[idx["ITCH.ITCH_OUT"]] + got[idx["FEED.MOLD_GAP_EVENTS"]] + got[idx["FEED.MOLD_EOS"]]
        assert left == right, f"snapshot {seq}: ring counters account for {left} entries, ITCH and MOLD for {right}"
        if phases == want_phases:
            break
        for _ in range(w.rng.randint(0, 3)):
            await RisingEdge(dut.net_clk)
    assert phases == want_phases, f"snapshot phases around a packet event: {sorted(phases)}"


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_snapshot_while_busy(dut):
    """Bursts of 2..6 snapshot requests back to back (no polling): none is lost: after the last
    request of a burst a snapshot is taken (acceptance after it) and every taken snapshot lands;
    requests coalesce (fewer snapshots than requests). The read after each burst is exact."""
    w = await start(dut)
    w.snap.p_valid = 0.5
    requests = 0
    for _ in range(20):
        for _ in range(w.rng.randint(2, 6)):
            assert await w.drv.write(STATS_CTRL, 1) == OKAY
            requests += 1
        last = w.snap.pulses[-1]
        await w.snap.quiet()
        assert w.snap.accepts and w.snap.accepts[-1] > last, "no snapshot was taken after the last request"
        seq = await w.snap.seq()
        await w.snap.check(seq, await w.snap.read_counters())
    # The host-side pending bit is set in every profile; the net-side one only when a request reaches
    # net while the data transfer is busy, which depends on the clock ratio (not in nojit-net2host).
    dut._log.info(f"pending-request cycles: {w.snap.pending_cycles}")
    assert w.snap.pending_cycles["host"] > 0, "no request was ever pending on the host side"
    assert len(w.snap.pulses) == requests
    taken = len(w.snap.accepts)
    assert taken < requests, f"no request coalesced ({taken} snapshots, {requests} requests)"


@cocotb.test(timeout_time=10, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_stats_seq(dut):
    """STATS_SEQ counts landed snapshots: +1 per snapshot when they do not overlap."""
    w = await start(dut)
    w.snap.p_valid = 0.3
    for k in range(1, 16):
        seq, got = await w.snap.snapshot()
        assert seq == k
        await w.snap.check(seq, got)


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_k5_sw_write_during_hw_update(dut):
    """K5: host writes to SCRATCH and the NET shadow while snapshots land (hardware updates the
    STATS registers). Every write answers OKAY and is kept, also writes accepted within a host cycle
    of a landing (the test checks such a coincidence happened)."""
    w = await start(dut)
    w.snap.p_valid = 0.7
    _, host_ps, _ = profile()
    regs = [SCRATCH] + [a for a, _ in NetCfg().writes()]
    model = CsrModel()

    async def requester():
        for _ in range(150):
            assert await w.drv.write(STATS_CTRL, 1) == OKAY
            for _ in range(w.rng.randint(0, 6)):
                await RisingEdge(dut.host_clk)

    task = cocotb.start_soon(requester())
    for _ in range(400):
        a, d = w.rng.choice(regs), w.rng.getrandbits(32)
        assert await w.drv.write(a, d) == OKAY
        model.write(a, d)
    await task
    for a in regs:
        assert await w.drv.read(a) == model.read(a), f"read {a:#06x}"
    near = [t for t in w.drv.hs_time["w"] if any(abs(t - x) <= host_ps for x in w.snap.landed)]
    assert near, "no write was accepted near a landing cycle"


@cocotb.test(timeout_time=200, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_hi_lo_read(dut):
    """A counter past 2**32 reads correctly as LO/HI: 65538 frames of 0xFFFF bytes give
    RX_BYTES = 2**32 + 0xFFFE (LO 0xFFFE, HI 1) and RX_PKTS = 65538 (hand-written values)."""
    w = await start(dut)
    w.snap.drive = False
    await RisingEdge(dut.net_clk)
    for _ in range(65538):
        dut.stat_valid.value = 1
        dut.stat_reason.value = int(RxReason.OK)
        dut.stat_bytes.value = 0xFFFF
        await RisingEdge(dut.net_clk)
    dut.stat_valid.value = 0
    assert await w.drv.read(addr("STATS.RX_BYTES.LO")) == (0, OKAY), "counters changed without a snapshot"
    seq, got = await w.snap.snapshot()
    assert seq == 1
    assert await w.drv.read(addr("STATS.RX_BYTES.LO")) == (0xFFFE, OKAY)
    assert await w.drv.read(addr("STATS.RX_BYTES.HI")) == (1, OKAY)
    assert await w.drv.read(addr("STATS.RX_PKTS.LO")) == (65538, OKAY)
    assert await w.drv.read(addr("STATS.RX_PKTS.HI")) == (0, OKAY)


@cocotb.test(timeout_time=1, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_feed_reset_defaults(dut):
    """After reset: cfg_feed_en, cfg_session_check, cfg_resync and cfg_class_en = 0 on every net
    edge (the Feed monitor checks it until the first transfer), FEED_STATUS.commit_busy = 0, and
    FEED writes (ITCH_CTRL included) without a commit change nothing in the net domain."""
    w = await start(dut)
    assert await w.feed.busy() == 0
    await w.feed.write_cfg(replace(FeedCfg.random(w.rng), enable=1, session_check=1, class_en=0x3F))
    for _ in range(50):
        await RisingEdge(dut.net_clk)
        await ReadOnly()
        assert dut.cfg_feed_en.value == 0, "feed_en reached moldudp64_rx without a commit"
        assert dut.cfg_class_en.value == 0, "class_en reached the decoder without a commit"
    assert not w.feed.seen and not w.feed.expected


@cocotb.test(timeout_time=2, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_feed_commit_latency(dut):
    """FEED_COMMIT (commit or resync) -> handshake acceptance: exactly 2 host edges (handshake
    free); acceptance -> landed (cfg_* changed, cfg_resync): exactly STAGES+1 net edges (+1 with
    jitter). 20 requests at random phases, every other one a resync."""
    w = await start(dut)
    host, net = set(), set()
    for k in range(20):
        await w.feed.write_cfg(FeedCfg.random(w.rng))
        await w.feed.commit(resync=k % 2 == 1)
        await w.feed.wait_idle()
        assert await w.feed.applied() == w.feed.committed[-1]
        pulse, accept, land = w.feed.pulse_times[-1], w.feed.accept_times[-1], w.feed.land_times[-1]
        host.add(sum(pulse < t <= accept for t in w.host_edges))
        net.add(sum(accept < t <= land for t in w.net_edges))
        for _ in range(w.rng.randint(0, 7)):
            await RisingEdge(dut.host_clk)
    assert len(w.feed.pulse_times) == len(w.feed.accept_times) == len(w.feed.land_times) == 20
    assert w.feed.resyncs == 10
    w.feed.check_done()
    assert host == {2}, f"host edges request -> acceptance: {sorted(host)}"
    allowed = {STAGES + 1, STAGES + 2} if jitter() else {STAGES + 1}
    assert net <= allowed, f"net edges acceptance -> landed: {sorted(net)}, documented {sorted(allowed)}"


class PendingWatch:
    """Grey box, host side: events that reach the FEED path while a request waits for the
    handshake (feed_pending_q): a change of the shadow, and a resync request (rising edge)."""

    def __init__(self, root):
        self.root, self.shadow_changes, self.resync_requests = root, 0, 0
        cocotb.start_soon(self._run())

    async def _run(self) -> None:
        r = self.root
        prev = None
        while True:
            await RisingEdge(r.host_clk)
            await ReadOnly()
            shadow = int(r.feed_shadow.value)
            if r.feed_pending_q.value:
                self.shadow_changes += int(prev is not None and shadow != prev)
                self.resync_requests += int(r.feed_resync_req.value)
            prev = shadow


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_feed_commit_atomic(dut):
    """100 random FEED configurations, each written in random order and committed, sometimes
    waiting for commit_busy = 0, sometimes not. The monitor checks every net edge: the FEED cfg_*
    are always one committed configuration, whole, in commit order. The last one is applied."""
    w = await start(dut)
    for _ in range(100):
        await w.feed.write_cfg(FeedCfg.random(w.rng))
        await w.feed.commit()
        if w.rng.random() < 0.5:
            await w.feed.wait_idle()
            assert await w.feed.applied() == w.feed.committed[-1]
    await w.feed.wait_idle()
    assert await w.feed.applied() == w.feed.committed[-1]
    assert len(w.feed.seen) >= 50
    # A commit queued behind a busy handshake, then a whole new configuration written into the
    # shadow while that commit still waits: the commit carries the shadow of its own pulse (the
    # buffer), never the newer writes.
    watch = PendingWatch(dut)
    for _ in range(10):
        cfg = FeedCfg.random(w.rng)
        await w.feed.write_cfg(cfg)
        await w.feed.commit()  # takes the handshake
        w.feed.committed.append(cfg)
        before = watch.shadow_changes
        pend = [w.drv.write_nowait(FEED_COMMIT, 1)]  # waits behind it (pending)
        newer = FeedCfg.random(w.rng)
        for a, d in newer.writes():
            pend.append(w.drv.write_nowait(a, d))
            w.feed.regs[a] = d
        for q in pend:
            assert (await q.wait())[1] == OKAY
        await w.feed.wait_idle()
        assert watch.shadow_changes > before, "no shadow write landed while the commit was pending"
        assert await w.feed.applied() == cfg, "writes after a commit reached cfg_*"
    w.feed.check_done()
    assert w.feed.resyncs == 0, "a commit without resync made cfg_resync"


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_feed_commit_while_busy(dut):
    """FEED commits and resyncs while the handshake is busy: bursts of 2..6 requests (a third of
    them resyncs) with 0..3 shadow writes between them. No commit is lost (the newest is applied),
    coalesced commits skip older configurations, resync requests merge into one transfer but are
    never lost, shadow writes after the last request never reach cfg_*, and commit_busy reads 1
    while a request is in flight."""
    w = await start(dut)
    busy_seen = 0
    regs = [a for a, _ in FeedCfg().writes()]
    for _ in range(40):
        await w.feed.write_cfg(FeedCfg.random(w.rng))
        for _ in range(w.rng.randint(2, 6)):
            await w.feed.commit(resync=w.rng.random() < 0.33)
            for _ in range(w.rng.randint(0, 3)):
                await w.feed.write(w.rng.choice(regs), w.rng.getrandbits(32))
        busy_seen += await w.feed.busy()
        for _ in range(w.rng.randint(1, 4)):
            await w.feed.write(w.rng.choice(regs), w.rng.getrandbits(32))
        await w.feed.wait_idle()
        assert await w.feed.applied() == w.feed.committed[-1], "the newest FEED commit was not applied"
        w.feed.check_done()
    assert busy_seen > 0
    assert len(w.feed.expected) < len(w.feed.committed), "no FEED request coalesced"
    assert 0 < w.feed.resyncs <= w.feed.resync_requests


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_feed_resync(dut):
    """FEED_COMMIT.resync alone (learn and set, waiting for idle): one cfg_resync pulse each, with
    the resync_learn and RESYNC_SEQ written before it (the monitor matches cfg_* to that commit);
    a plain commit right after: no pulse; a resync requested while a plain commit is in flight goes
    with the next transfer."""
    w = await start(dut)
    for k in range(10):
        cfg = replace(FeedCfg.random(w.rng), resync_learn=k % 2)
        await w.feed.write_cfg(cfg)
        before = w.feed.resyncs
        await w.feed.commit(resync=True)
        await w.feed.wait_idle()
        assert await w.feed.applied() == cfg
        assert w.feed.resyncs == before + 1, "one resync request, one cfg_resync pulse"
        await w.feed.commit()
        await w.feed.wait_idle()
        assert w.feed.resyncs == before + 1, "a plain commit made cfg_resync"
    for _ in range(10):
        await w.feed.write_cfg(FeedCfg.random(w.rng))
        before = w.feed.resyncs
        await w.feed.commit()
        await w.feed.commit(resync=True)  # while the first is pending or in flight
        await w.feed.wait_idle()
        assert w.feed.resyncs == before + 1
    # Two resync requests while a plain commit occupies the handshake: they merge into one transfer
    # (the newest shadow, resync once), like a self-clearing command bit written twice.
    for _ in range(10):
        new = FeedCfg.random(w.rng)
        await w.feed.write_cfg(new)
        before, transfers = w.feed.resyncs, len(w.feed.expected)
        await w.feed.commit()
        # Queued right behind it (a SCRATCH write between keeps the two resync pulses apart).
        w.feed.committed += [new, new]
        w.feed.resync_requests += 2
        pend = [w.drv.write_nowait(FEED_COMMIT, 2), w.drv.write_nowait(SCRATCH, 0), w.drv.write_nowait(FEED_COMMIT, 2)]
        for q in pend:
            assert (await q.wait())[1] == OKAY
        await w.feed.wait_idle()
        assert await w.feed.applied() == new
        assert len(w.feed.expected) == transfers + 2, "expected the commit and one merged resync transfer"
        assert w.feed.resyncs == before + 1, "two resync requests in one window did not merge into one"
    # A resync requested while a plain commit waits behind a busy handshake: it merges into that
    # commit's transfer (resync once), it is not lost.
    watch = PendingWatch(dut)
    for _ in range(10):
        cfg = FeedCfg.random(w.rng)
        await w.feed.write_cfg(cfg)
        before, transfers, seen = w.feed.resyncs, len(w.feed.expected), watch.resync_requests
        w.feed.committed += [cfg, cfg, cfg]
        w.feed.resync_requests += 1
        pend = [w.drv.write_nowait(FEED_COMMIT, 1), w.drv.write_nowait(FEED_COMMIT, 1), w.drv.write_nowait(FEED_COMMIT, 2)]
        for q in pend:
            assert (await q.wait())[1] == OKAY
        await w.feed.wait_idle()
        assert watch.resync_requests > seen, "the resync request did not arrive while a commit was pending"
        assert len(w.feed.expected) == transfers + 2, "expected the first commit and one merged transfer"
        assert w.feed.resyncs == before + 1, "the resync request behind a pending commit was lost"
    w.feed.check_done()


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_feed_resync_held_pulse(dut):
    """A FEED_COMMIT write with byte 0 disabled right behind a resync write keeps regblock's
    singlepulse field high for a second cycle. That is still one request: one transfer resyncs,
    once (a resync is not idempotent). The test checks that the held pulse happened."""
    w = await start(dut)
    for _ in range(20):
        await w.feed.write_cfg(FeedCfg.random(w.rng))
        w.feed.committed.append(FeedCfg.from_regs(w.feed.regs))
        w.feed.resync_requests += 1
        before = w.feed.resyncs
        first = w.drv.write_nowait(FEED_COMMIT, 2)
        second = w.drv.write_nowait(FEED_COMMIT, 0, strb=0b1110)
        assert await first.wait() == (0, OKAY) and await second.wait() == (0, OKAY)
        await w.feed.wait_idle()
        assert w.feed.resyncs == before + 1, "a held resync pulse made more than one resync"
    w.feed.check_done()
    assert w.feed.held > 0, "the resync pulse was never held for a second cycle"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_feed_reset_during_commit(dut):
    """Both resets at a random point of a FEED transfer (half of them resyncs): afterwards
    cfg_feed_en = 0 and stays 0 and no cfg_resync appears (no stale transfer lands), commit_busy =
    0, and the next commit works."""
    w = await start(dut)
    for k in range(10):
        await w.feed.write_cfg(replace(FeedCfg.random(w.rng), enable=1))
        await w.feed.commit(resync=k % 2 == 0)
        for _ in range(w.rng.randint(0, 3 * STAGES + 6)):
            await RisingEdge(dut.host_clk)
        await reset(dut)
        w.feed.regs = {}
        w.feed.resync_requests = w.feed.request_edges = 0
        for _ in range(4 * STAGES + 10):
            await RisingEdge(dut.net_clk)
            await ReadOnly()
            assert dut.cfg_feed_en.value == 0, "a FEED transfer from before the reset enabled the feed"
            assert dut.cfg_resync.value == 0, "a resync from before the reset"
        await RisingEdge(dut.host_clk)
        assert await w.feed.busy() == 0
        cfg = replace(FeedCfg.random(w.rng), enable=1)
        await w.feed.write_cfg(cfg)
        await w.feed.commit()
        await w.feed.wait_idle()
        assert await w.feed.applied() == cfg


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_itch_ctrl_feed_commit(dut):
    """ITCH_CTRL is a FEED shadow register: written alone it reads back but changes nothing at net;
    FEED_COMMIT carries it together with the rest of the FEED block (the Feed monitor checks every
    net edge that cfg_class_* belong to the same committed configuration as the other FEED cfg_*).
    20 rounds that change only ITCH_CTRL, only the other FEED registers, or both."""
    w = await start(dut)
    cfg = FeedCfg.random(w.rng)
    for k in range(20):
        if k % 3 != 1:
            cfg = replace(cfg, class_en=w.rng.getrandbits(6), class_filter=w.rng.getrandbits(6))
        if k % 3 != 0:
            cfg = replace(FeedCfg.random(w.rng), class_en=cfg.class_en, class_filter=cfg.class_filter)
        before = await w.feed.applied() if k else None
        await w.feed.write_cfg(cfg)
        value = cfg.class_en | cfg.class_filter << 8
        assert await w.drv.read(ITCH_CTRL) == (value, OKAY), "ITCH_CTRL readback"
        for _ in range(30):
            await RisingEdge(dut.net_clk)
        if before is not None:
            assert await w.feed.applied() == before, "a FEED write without a commit reached net"
        await w.feed.commit()
        await w.feed.wait_idle()
        got = await w.feed.applied()
        assert got == cfg and (got.class_en, got.class_filter) == (cfg.class_en, cfg.class_filter)
    w.feed.check_done()


def sym_addr(word: int) -> int:
    return SYMBOL.addr + 4 * word


async def sweep_free(w: Wrap) -> None:
    """Nothing pending in the symbol path (grey box)."""
    r = w.root
    for _ in range(200):
        await ReadOnly()
        if not (r.sym_pending_q.value or r.sym_cmd_q.value or r.sym_rsp_pending_q.value):
            if r.u_sym_req_cdc.src_ready.value:
                await RisingEdge(r.host_clk)
                return
        await RisingEdge(r.host_clk)
    raise AssertionError("the symbol path never became idle")


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_sym_read_write(dut):
    """Every WSTRB on the first, the last and random bitmap words, read back; random backpressure
    at the port; then SYMBOL accesses mixed with SCRATCH writes and ID reads in flight. Every access
    reaches the port once, in order, with its word address, data and byte strobes; the port's
    RAM ends as software wrote it."""
    w = await start(dut)
    sym = w.sym
    sym.p_ready = 0.4
    # Distinct words: a batch below takes disjoint ones, and a repeated word would put a write and a read of it
    # in flight together (the order between the write and read channels is not defined).
    words = [0, SYMBOL.entries - 1] + w.rng.sample(range(1, SYMBOL.entries - 1), 6)
    want: dict[int, int] = {}
    for strb in range(16):
        for word in words:
            data = w.rng.getrandbits(32)
            assert await w.drv.write(sym_addr(word), data, strb) == OKAY
            bits = wstrb_mask(strb)
            want[word] = (want.get(word, 0) & ~bits) | (data & bits)
        for word in words:
            assert await w.drv.read(sym_addr(word)) == (want[word], OKAY), f"word {word}"
    model = CsrModel()
    for _ in range(100):
        pend = []
        for word in w.rng.sample(words, w.rng.randint(1, 4)):  # disjoint words: no write/read race
            if w.rng.random() < 0.5:
                data, strb = w.rng.getrandbits(32), w.rng.getrandbits(4)
                bits = wstrb_mask(strb)
                want[word] = (want.get(word, 0) & ~bits) | (data & bits)
                pend.append((w.drv.write_nowait(sym_addr(word), data, strb), (0, OKAY)))
            else:
                pend.append((w.drv.read_nowait(sym_addr(word)), (want[word], OKAY)))
            # A FEED write and an ID read in flight with it (disjoint registers: the order between
            # the write and read channels is not defined).
            d = w.rng.getrandbits(32)
            pend.append((w.drv.write_nowait(SCRATCH, d), (0, OKAY)))
            pend.append((w.drv.read_nowait(addr("GLOBAL.ID")), model.read(addr("GLOBAL.ID"))))
        for q, exp in pend:
            got = await q.wait()
            if q.kind == "r" and SYMBOL.word(q.addr) is not None and got != exp:
                word = SYMBOL.word(q.addr)
                hist = [(e, c) for e, c in sym.cmds if c.addr == word][-4:]
                reqs = [(e, c) for e, c in sym.host_reqs if c.addr == word][-4:]
                raise AssertionError(f"r {q.addr:#06x}: {got}, want {exp}; port RAM {sym.ram.get(word, 0)}; "
                                     f"port cmds {hist}; host reqs {reqs}; acks {sym.acks[-4:]}")  # fmt: skip
            assert got == exp if q.kind == "r" else got[1] == exp[1], f"{q.kind} {q.addr:#06x}: {got}"
    await sweep_free(w)
    sym.check_order()
    assert {k: sym.ram.get(k, 0) for k in want} == want, "the port's RAM differs from what software wrote"
    assert sym.waits > 0, "no backpressure at the port"
    assert {c.wstrb for _, c in sym.cmds if c.wr} == set(range(16)), "every byte strobe value"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_sym_latency(dut):
    """Handshakes free, port always ready: request -> request handshake acceptance exactly 2 host
    edges; acceptance -> command at the port STAGES+2 net edges (+1 with jitter); response ->
    response handshake acceptance 2 net edges; acceptance -> ack STAGES+1 host edges (+1 with
    jitter). 20 accesses (writes and reads) at random phases."""
    w = await start(dut)
    sym = w.sym
    host, to_net, rsp, back = set(), set(), set(), set()
    for k in range(20):
        word = w.rng.randrange(SYMBOL.entries)
        if k % 2:
            assert (await w.drv.read(sym_addr(word)))[1] == OKAY
        else:
            assert await w.drv.write(sym_addr(word), w.rng.getrandbits(32)) == OKAY
        await sweep_free(w)
        req, acc = sym.host_reqs[-1][0], sym.host_accepts[-1]
        start_, rsp_t, rsp_acc, ack = sym.cmd_starts[-1], sym.rsps[-1], sym.rsp_accepts[-1], sym.acks[-1][0]
        host.add(sum(req < t <= acc for t in w.host_edges))
        to_net.add(sum(acc < t <= start_ for t in w.net_edges))
        rsp.add(sum(rsp_t < t <= rsp_acc for t in w.net_edges))
        back.add(sum(rsp_acc < t <= ack for t in w.host_edges))
        for _ in range(w.rng.randint(0, 7)):
            await RisingEdge(dut.host_clk)
    sym.check_order()
    assert len(sym.host_accepts) == len(sym.rsp_accepts) == 20
    assert host == {2}, f"host edges request -> acceptance: {sorted(host)}"
    assert rsp == {2}, f"net edges response -> acceptance: {sorted(rsp)}"
    j = {0, 1} if jitter() else {0}
    assert to_net <= {STAGES + 2 + d for d in j}, f"net edges acceptance -> command: {sorted(to_net)}"
    assert back <= {STAGES + 1 + d for d in j}, f"host edges acceptance -> ack: {sorted(back)}"


@cocotb.test(timeout_time=10, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_sym_bresp_after_effect(dut):
    """BRESP means the write is in the bitmap: when a SYMBOL write's B arrives, the port has
    already taken the command (its accepting edge is before the ack) and its RAM holds the new
    word; a read right after returns it. 60 writes with random backpressure."""
    w = await start(dut)
    sym = w.sym
    sym.p_ready = 0.3
    want: dict[int, int] = {}
    for _ in range(60):
        word, data, strb = w.rng.randrange(SYMBOL.entries), w.rng.getrandbits(32), w.rng.choice([0xF, w.rng.getrandbits(4)])
        bits = wstrb_mask(strb)
        want[word] = (want.get(word, 0) & ~bits) | (data & bits)
        n = len(sym.cmds)
        assert await w.drv.write(sym_addr(word), data, strb) == OKAY
        b_time = w.drv.hs_time["b"][-1]
        assert len(sym.cmds) == n + 1, "B before the command reached the port"
        assert sym.cmds[-1][0] < sym.acks[-1][0] <= b_time, "the ack came before the port took the write"
        assert sym.ram.get(word, 0) == want[word], "B before the write was in the RAM"
        assert await w.drv.read(sym_addr(word)) == (want[word], OKAY)
    sym.check_order()


@cocotb.test(timeout_time=10, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_sym_wait_sweep(dut):
    """While itch_decoder is not ready (its 2048-cycle bitmap sweep after reset), a SYMBOL access
    waits at the port and the bus with it: a SCRATCH write and read queued behind it get no
    response either (one external access in flight). When ready rises, the SYMBOL read answers 0,
    then the others, in order; the command did not change while it waited."""
    w = await start(dut)
    sym = w.sym
    sym.hold = True
    word = w.rng.randrange(SYMBOL.entries)
    first = w.drv.read_nowait(sym_addr(word))
    while not sym.host_reqs:  # regblock has taken it: everything after it waits
        await RisingEdge(dut.host_clk)
    behind = [w.drv.write_nowait(SCRATCH, 0x5A5A_1234), w.drv.read_nowait(SCRATCH)]
    for _ in range(2048):
        await RisingEdge(dut.net_clk)
    assert first.result is None and all(q.result is None for q in behind), "an access finished during the hold"
    assert sym.waits > 2000 and not sym.cmds
    sym.hold = False
    assert await first.wait() == (0, OKAY)
    assert (await behind[0].wait())[1] == OKAY and await behind[1].wait() == (0x5A5A_1234, OKAY)
    assert w.drv.hs_time["r"][0] < w.drv.hs_time["b"][0], "the SYMBOL read answered after the access behind it"
    await sweep_free(w)
    sym.check_order()


async def sym_reset_and_recover(w: Wrap, dut) -> None:
    """Both resets with the bus driver told (it drops the lost request); then for 4 * STAGES + 30
    net edges no command is valid and no ack comes, the symbol path is idle (no stale transfer
    lands), and a fresh write and read of a SYMBOL word work and reach the port once each."""
    sym = w.sym
    acks = len(sym.acks)
    w.drv.reset(dut.host_rst)
    await reset(dut)
    sym.hold = False
    for _ in range(4 * STAGES + 30):
        await RisingEdge(dut.net_clk)
        await ReadOnly()
        assert not dut.sym_req_valid.value, "a SYMBOL command from before the reset"
    assert not (dut.sym_pending_q.value or dut.sym_cmd_q.value or dut.sym_rsp_pending_q.value)
    assert len(sym.acks) == acks, "an ack from before the reset"
    await RisingEdge(dut.host_clk)
    n = len(sym.host_reqs), len(sym.cmds), len(sym.rsps), len(sym.acks)
    word, data = w.rng.randrange(SYMBOL.entries), w.rng.getrandbits(32)
    assert await w.drv.write(sym_addr(word), data) == OKAY
    assert await w.drv.read(sym_addr(word)) == (data, OKAY)
    await sweep_free(w)
    reqs = [c for _, c in sym.host_reqs[n[0]:]]
    assert [c for _, c in sym.cmds[n[1]:]] == reqs and len(reqs) == 2, "after the reset: two accesses, two commands"
    assert len(sym.rsps) - n[2] == 2 and len(sym.acks) - n[3] == 2


@cocotb.test(timeout_time=10, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_sym_reset_waiting(dut):
    """Both resets while a SYMBOL write waits at the port (not ready), at a random point of the
    wait, 10 times: no command from before the reset is ever taken, and the path works again
    (sym_reset_and_recover)."""
    w = await start(dut)
    sym = w.sym
    for _ in range(10):
        sym.hold = True
        taken = len(sym.cmds)
        starts = len(sym.cmd_starts)
        w.drv.write_nowait(sym_addr(w.rng.randrange(SYMBOL.entries)), w.rng.getrandbits(32))
        while len(sym.cmd_starts) == starts:
            await RisingEdge(dut.net_clk)
        for _ in range(w.rng.randint(0, 20)):
            await RisingEdge(dut.net_clk)
        assert len(sym.cmds) == taken, "the port took a command while not ready"
        await sym_reset_and_recover(w, dut)


@cocotb.test(timeout_time=10, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_sym_reset_in_flight(dut):
    """Both resets at 10 points spread over a SYMBOL read's round trip (request handshake, command,
    response handshake, ack; port always ready): no ack and no command from before the
    reset come afterwards, and the path works again (sym_reset_and_recover)."""
    w = await start(dut)
    sym = w.sym
    phases = set()
    for k in range(10):  # reset points spread over the round trip, 0 .. 4 * STAGES + 8 host edges
        reqs, cmds = len(sym.host_reqs), len(sym.cmds)
        w.drv.read_nowait(sym_addr(w.rng.randrange(SYMBOL.entries)))
        while len(sym.host_reqs) == reqs:
            await RisingEdge(dut.host_clk)
        for _ in range(k * (4 * STAGES + 8) // 9):
            await RisingEdge(dut.host_clk)
        phases.add("before the port" if len(sym.cmds) == cmds else "taken by the port")
        await sym_reset_and_recover(w, dut)
    assert phases == {"before the port", "taken by the port"}, f"reset phases: {sorted(phases)}"


@cocotb.test(timeout_time=30, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_feed_epoch(dut):
    """User decision (audit/13 S6): cfg_epoch counts every FEED_COMMIT write with commit or resync set, because the
    host keeps the same count, and wraps at 256 (hand-written values). Commits that coalesce and writes in consecutive
    bus cycles count one by one, a pulse held by a write with byte 0 disabled counts once, a write of 0 not at all, and
    a reset starts it again. The Feed monitor checks at every net edge that it changes only where a transfer lands."""
    w = await start(dut)
    await w.feed.write_cfg(FeedCfg.random(w.rng))
    for k in range(1, 261):
        await w.feed.commit(resync=k % 7 == 0)
        await w.feed.wait_idle()
        await w.feed.applied()
        assert int(dut.cfg_epoch.value) == k % 256, f"after {k} commits"
    w.feed.check_done()
    assert int(dut.cfg_epoch.value) == 4, "hand-written: 260 commits wrap to 4"

    for _ in range(3):  # no wait: they coalesce
        await w.feed.commit()
    await w.feed.wait_idle()
    await w.feed.applied()
    assert int(dut.cfg_epoch.value) == 7, "3 commits that coalesce count 3"

    first = w.drv.write_nowait(FEED_COMMIT, 1)
    held = w.drv.write_nowait(FEED_COMMIT, 0, strb=0b1110)
    w.feed.committed.append(FeedCfg.from_regs(w.feed.regs))
    assert await first.wait() == (0, OKAY) and await held.wait() == (0, OKAY)
    await w.feed.wait_idle()
    await w.feed.applied()
    assert int(dut.cfg_epoch.value) == 8, "a held pulse is one commit"

    w.feed.back_to_back = 0
    w.feed.committed += [FeedCfg.from_regs(w.feed.regs)] * 3
    w.feed.resync_requests += 1
    pend = [w.drv.write_nowait(FEED_COMMIT, 1), w.drv.write_nowait(FEED_COMMIT, 2), w.drv.write_nowait(FEED_COMMIT, 1)]
    for q in pend:
        assert await q.wait() == (0, OKAY)
    await w.feed.wait_idle()
    await w.feed.applied()
    assert w.feed.back_to_back > 0, "the writes never came in consecutive cycles"
    assert int(dut.cfg_epoch.value) == 11, "3 writes in consecutive cycles count 3"

    assert await w.drv.write(FEED_COMMIT, 0) == OKAY
    await w.feed.wait_idle()
    assert int(dut.cfg_epoch.value) == 11, "a write of 0 changed the count"
    await w.feed.commit()
    await w.feed.wait_idle()
    await w.feed.applied()
    assert int(dut.cfg_epoch.value) == 12, "a write of 0 counted"

    await reset(dut)
    w.feed.regs = {}
    w.feed.resync_requests = w.feed.request_edges = 0
    await w.feed.commit()
    await w.feed.wait_idle()
    await w.feed.applied()
    assert int(dut.cfg_epoch.value) == 1, "the count starts again after a reset"
    w.feed.check_done()


@cocotb.test(timeout_time=2, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_ring_registers(dut):
    """RING is in the host domain, with no commit: a write reaches host_ring_* by itself, and reads back through the
    fields' own widths. PROD_IDX, RING_STATUS and the host counters read the input live; software cannot write them
    (K5). The snapshot counters do not move with a read. All 0 after reset, so an enable needs a size first."""
    w = await start(dut)
    regs = {n: addr(f"RING.{n}") for n in ("RING_CTRL", "RING_BASE_LO", "RING_BASE_HI", "RING_SIZE_LOG2", "CONS_IDX",
                                           "PROD_IDX", "RING_STATUS")}  # fmt: skip

    async def check(name, value):
        assert await w.drv.read(regs[name]) == (value, OKAY), name

    for name in ("RING_CTRL", "RING_BASE_LO", "RING_BASE_HI", "RING_SIZE_LOG2", "CONS_IDX"):
        await check(name, 0)
    assert not dut.host_ring_enable.value and int(dut.host_ring_size_log2.value) == 0

    assert await w.drv.write(regs["RING_BASE_LO"], 0x1234_5FFF) == OKAY
    assert await w.drv.write(regs["RING_BASE_HI"], 0xABCD_EF01) == OKAY
    await check("RING_BASE_LO", 0x1234_5000)
    await check("RING_BASE_HI", 0xABCD_EF01)
    assert int(dut.host_ring_base.value) == 0xABCD_EF01_1234_5000, "bits 11:0 are 0"

    assert await w.drv.write(regs["RING_SIZE_LOG2"], 0xFFFF_FFF4) == OKAY
    await check("RING_SIZE_LOG2", 0x14)
    assert int(dut.host_ring_size_log2.value) == 0x14

    assert await w.drv.write(regs["CONS_IDX"], 0xDEAD_BEEF) == OKAY
    await check("CONS_IDX", 0xDEAD_BEEF)
    assert int(dut.host_ring_cons.value) == 0xDEAD_BEEF

    assert await w.drv.write(regs["RING_CTRL"], 0xFFFF_FFFF) == OKAY
    await check("RING_CTRL", 1)
    assert dut.host_ring_enable.value
    assert await w.drv.write(regs["RING_CTRL"], 0) == OKAY
    assert not dut.host_ring_enable.value

    for prod, bresp, enable, written, errors in ((7, 1, 0, 1 << 40 | 3, 5), (0xFFFF_FFFF, 0, 1, 9, 1 << 33), (0, 1, 1, 0, 0)):
        dut.host_ring_prod_idx.value = prod
        dut.host_ring_bresp_err.value = bresp
        dut.host_ring_enable_err.value = enable
        dut.host_ring_cnt_written.value = written
        dut.host_ring_cnt_bresp_err.value = errors
        await RisingEdge(dut.host_clk)
        await check("PROD_IDX", prod)
        await check("RING_STATUS", bresp | enable << 1)
        assert await w.drv.read(addr("RING.RING_WRITTEN.LO")) == (written & 0xFFFF_FFFF, OKAY)
        assert await w.drv.read(addr("RING.RING_WRITTEN.HI")) == (written >> 32, OKAY)
        assert await w.drv.read(addr("RING.RING_BRESP_ERR.LO")) == (errors & 0xFFFF_FFFF, OKAY)
        assert await w.drv.read(addr("RING.RING_BRESP_ERR.HI")) == (errors >> 32, OKAY)
    for name in ("PROD_IDX", "RING_STATUS"):
        assert await w.drv.write(regs[name], 0xFFFF_FFFF) != OKAY, f"{name} takes a software write"


# ---- order path (audit/14 S7) ----


async def wr(w: Wrap, a: int, data: int, strb: int = 0xF) -> None:
    assert await w.drv.write(a, data, strb) == OKAY, f"write {a:#06x}"


async def rd(w: Wrap, a: int) -> int:
    data, resp = await w.drv.read(a)
    assert resp == OKAY, f"read {a:#06x}"
    return data


async def write_all(w: Wrap, writes: list[tuple[int, int]]) -> None:
    writes = list(writes)
    w.rng.shuffle(writes)
    for a, d in writes:
        await wr(w, a, d)


async def open_path(w: Wrap) -> None:
    """An accepted TX_COMMIT (st_tx_cfg_valid), tx_en = 1, kill = 0; waits until ctl_* show it."""
    await w.ord.idle()
    await write_all(w, TxCfg.random(w.rng).writes())
    w.ord.refuse_tx = False
    await wr(w, COMMANDS["TX_COMMIT"], 1)
    await wr(w, TX_CTRL, 1)
    await wr(w, RISK_CTRL, 0)
    for _ in range(STAGES + 4):
        await RisingEdge(w.root.net_clk)
    await ReadOnly()
    assert w.root.ctl_kill.value == 0 and w.root.ctl_tx_en.value == 1, "the order path did not open"
    await RisingEdge(w.root.host_clk)


@cocotb.test(timeout_time=2, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_order_reset_defaults(dut):
    """Decision 5: after reset RISK_CTRL.kill reads 1 and ctl_kill is 1, TX_CTRL.tx_en 0 and ctl_tx_en 0 on every
    net edge from reset on; RULE_STATUS 0xFF, TX_STATUS 0, MBOX_STATUS 0, MBOX_SPACE 16, TX_USERREF the counter; the
    write-only commands answer reads with SLVERR; no command reached net."""
    w = await start(dut)
    for _ in range(20):
        await RisingEdge(dut.host_clk)
    assert await rd(w, RISK_CTRL) == 1 and await rd(w, TX_CTRL) == 0
    assert await rd(w, RULE_STATUS) == 0xFF and await rd(w, TX_STATUS) == 0
    assert await rd(w, MBOX_STATUS) == 0 and await rd(w, MBOX_SPACE) == 16
    assert await rd(w, TX_USERREF) == 1
    for name, a in COMMANDS.items():
        assert (await w.drv.read(a))[1] != OKAY, f"{name} answered a read"
    assert w.ord.ctl and all(k == 1 and e == 0 for _, k, e in w.ord.ctl), "kill not 1 or tx_en not 0 after reset"
    assert not w.ord.pulses and not w.ord.loc_cmds


@cocotb.test(timeout_time=10, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_rule_commit(dut):
    """RULE_COMMIT replaces rule RULE_SEL with the shadow: one cfg_rule_wr pulse with the index and the whole rule
    (strat_rule_t, hand-written layout in RuleCfg.packed, and one rule with hand-checked slices); the write response
    comes after it, and RULE_STATUS then shows strategy_engine's st_error. A write with the commit bit 0 or byte 0
    disabled is answered and sends nothing; shadow writes alone send nothing; the shadow reads back."""
    w = await start(dut)
    o = w.ord
    hand = RuleCfg(1, 0b101, 0x1234, 2, 1, 0x1122_3344, 0x5566_7788, 2, 1, 0x99AA_BBCC, 1, 0xDDEE_FF00, 0x33)
    v = hand.packed()
    assert v & 0xFF == 0x33 and v >> 8 & 0xFFFF_FFFF == 0xDDEE_FF00 and v >> 40 & 1 == 1
    assert v >> 41 & 0xFFFF_FFFF == 0x99AA_BBCC and v >> 162 == 1 and v >> 143 & 0xFFFF == 0x1234
    for k in range(40):
        rule = hand if k == 0 else RuleCfg.random(w.rng)
        idx = w.rng.randrange(8)
        await write_all(w, [*rule.writes(), (addr("STRATEGY.RULE_SEL"), idx)])
        assert not o.pulses[k:], "a shadow write reached net"
        for a, d in rule.writes():
            assert await rd(w, a) == d, f"shadow {a:#06x}"
        o.next_error = w.rng.getrandbits(8)
        await wr(w, COMMANDS["RULE_COMMIT"], 1)
        b = w.drv.hs_time["b"][-1]
        assert [(p.name, p.data) for p in o.pulses[k:]] == [("RULE_COMMIT", (idx, rule.packed()))]
        assert o.pulses[k].edge < b, "the write response came before the rule was in strategy_engine"
        assert await rd(w, RULE_STATUS) == o.next_error
        await wr(w, COMMANDS["RULE_COMMIT"], 0)  # commit bit 0
        await wr(w, COMMANDS["RULE_COMMIT"], 1, strb=0b1110)  # byte 0 disabled
        assert len(o.pulses) == k + 1, "a no-op commit reached net"
    assert [n for _, n in o.acks].count("RULE_COMMIT") == 120


def random_command(w: Wrap) -> tuple[str, list[tuple[int, int]], int, tuple]:
    """(command, shadow writes before it, written value, expected pulse data) of a random command."""
    rng = w.rng
    name = rng.choice(list(COMMANDS))
    if name == "RULE_COMMIT":
        rule, idx = RuleCfg.random(rng), rng.randrange(8)
        return name, [*rule.writes(), (addr("STRATEGY.RULE_SEL"), idx)], 1, (idx, rule.packed())
    if name in ("RULE_ARM", "RULE_DISARM"):
        mask = rng.getrandbits(32)
        return name, [], mask, (mask & 0xFF,)
    if name == "RISK_COMMIT":
        lim = LimCfg.random(rng)
        return name, lim.writes(), 1, (lim.max_qty, lim.max_notional, lim.refill, lim.period, lim.burst)
    if name == "TX_COMMIT":
        tx = TxCfg.random(rng)
        return name, tx.writes(), 1, (le_int(tx.template), tx.display, tx.capacity, tx.ise, tx.cross_type)
    value = rng.getrandbits(32)  # BUDGET_ADD, USERREF
    return name, [], value, (value,)


@cocotb.test(timeout_time=60, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_cmd_order(dut):
    """Decision 33: 150 random commands of every kind, with their shadow writes, LOCATE_ALLOW accesses and no-op
    commits mixed in, all issued without waiting (the bus queues them): every command reaches net once, in bus
    order, whole (the shadow of its own moment), and each answer comes after its pulse; the allow bitmap
    accesses take their place in that order. One external access at a time: no pulse while another command's
    response is pending."""
    w = await start(dut)
    o = w.ord
    o.loc_p_ready = 0.5
    want: list[tuple[str, tuple]] = []
    pend = []
    loc_want: dict[int, int] = {}
    for _ in range(150):
        kind = w.rng.random()
        if kind < 0.15:
            word, data = w.rng.randrange(LOCATE_ALLOW.entries), w.rng.getrandbits(32)
            pend.append(w.drv.write_nowait(LOCATE_ALLOW.addr + 4 * word, data))
            loc_want[word] = data
            want.append(("LOC", (1, word, data, 0xF)))
            continue
        if kind < 0.2:
            pend.append(w.drv.write_nowait(COMMANDS[w.rng.choice(["RULE_COMMIT", "RISK_COMMIT", "TX_COMMIT"])], 0))
            continue
        name, writes, value, data = random_command(w)
        for a, d in writes:
            pend.append(w.drv.write_nowait(a, d))
        o.ignore_userref = w.rng.random() < 0.5
        o.refuse_tx = w.rng.random() < 0.5
        pend.append(w.drv.write_nowait(COMMANDS[name], value))
        want.append((name, data))
    for q in pend:
        assert (await q.wait())[1] == OKAY
    await o.idle()
    got = [(p.edge, p.name, p.data) for p in o.pulses]
    got += [(e, "LOC", (c.wr, c.addr, c.wdata, c.wstrb)) for e, c in o.loc_cmds]
    got.sort()
    assert [(n, d) for _, n, d in got] == want, "commands at net differ from the bus order"
    # One answer per external write (commands, no-ops, LOCATE_ALLOW), and between two pulses an answer.
    n_ext = sum(1 for q in pend if q.addr in COMMANDS.values() or LOCATE_ALLOW.word(q.addr) is not None)
    assert len(o.acks) == n_ext, f"{len(o.acks)} answers for {n_ext} external writes"
    pulse_edges = [e for e, _, _ in got]
    ack_edges = sorted(e for e, n in o.acks)
    for p, nxt in zip(pulse_edges, pulse_edges[1:]):
        assert any(p < a < nxt for a in ack_edges), "a command reached net before the previous one was answered"
    assert {k: o.loc_ram.get(k, 0) for k in loc_want} == loc_want


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_cmd_latency(dut):
    """One command at a time, channel free, mailbox empty, controls settled; each step in its own clock domain:
    external request -> command handshake acceptance exactly 3 host edges (H_FENCE, H_SEND); acceptance -> pulse
    at net STAGES + 4 net edges (+1 with the CDC jitter model: STAGES + 1 to dst_valid, N_FENCE, the pulse
    register); pulse -> response handshake acceptance exactly CMD_SETTLE + 1 = 4 net edges; acceptance -> answer
    STAGES + 1 host edges (+1 with jitter). 30 commands of every kind at random phases; the write -> answer time
    is logged for the README."""
    w = await start(dut)
    o, r = w.ord, w.root
    req_edges: list[int] = []  # host edges after which a command register's req is high
    cmd_acc: list[int] = []  # host edges that accept a command transfer
    trace: dict[int, tuple] = {}  # host edge -> (h_state, mbox wr_count, mbox busy, its count_q, src_ready) after it
    rsp_acc: list[int] = []  # net edges that accept a response transfer

    async def host_mon():
        reqs = [r.hwif_out.STRATEGY.RULE_COMMIT, r.hwif_out.STRATEGY.RULE_ARM, r.hwif_out.STRATEGY.RULE_DISARM,
                r.hwif_out.RISK.RISK_COMMIT, r.hwif_out.RISK.BUDGET_ADD, r.hwif_out.TX.TX_COMMIT,
                r.hwif_out.TX.TX_USERREF_NEXT]  # fmt: skip
        accept = False
        while True:
            await RisingEdge(r.host_clk)
            edge = get_sim_time()
            if accept:
                cmd_acc.append(edge)
            await ReadOnly()
            fifo = r.u_mbox_fifo.g_portable
            trace[edge] = (int(r.h_state_q.value), int(r.mbox_count.value), int(r.mbox_wr_busy.value),
                           int(fifo.g_count.count_q.value), int(r.u_cmd_cdc.src_ready.value))
            if any(h.req.value for h in reqs):
                req_edges.append(edge)
            accept = bool(r.u_cmd_cdc.src_valid.value and r.u_cmd_cdc.src_ready.value)

    async def net_mon():
        accept = False
        while True:
            await RisingEdge(r.net_clk)
            if accept:
                rsp_acc.append(get_sim_time())
            await ReadOnly()
            accept = bool(r.u_rsp_cdc.src_valid.value and r.u_rsp_cdc.src_ready.value)

    cocotb.start_soon(host_mon())
    cocotb.start_soon(net_mon())
    host, to_net, settle, back, total = set(), set(), set(), set(), []
    for _ in range(30):
        name, writes, value, _ = random_command(w)
        await write_all(w, writes)
        await o.idle()
        for _ in range(w.rng.randint(0, 7)):
            await RisingEdge(dut.host_clk)
        n = len(o.pulses)
        t0 = get_sim_time()
        await wr(w, COMMANDS[name], value)
        p, ack, req, acc, racc = o.pulses[n], o.acks[-1][0], req_edges[-1], cmd_acc[-1], rsp_acc[-1]
        steps = sum(req < e <= acc for e in w.host_edges)
        assert steps == 3, f"{name}: host edges request -> acceptance {steps}: " + str(
            [(e, trace.get(e)) for e in w.host_edges if req <= e <= acc])
        host.add(steps)
        to_net.add(sum(acc < e <= p.edge for e in w.net_edges))
        settle.add(sum(p.edge < e <= racc for e in w.net_edges))
        back.add(sum(racc < e <= ack for e in w.host_edges))
        total.append(ack - t0)
    dut._log.info(f"write -> answer {min(total)}..{max(total)} ps")
    j = {0, 1} if jitter() else {0}
    assert host == {3}, f"host edges request -> acceptance: {sorted(host)}"
    assert settle == {4}, f"net edges pulse -> response acceptance: {sorted(settle)}"
    assert to_net <= {STAGES + 4 + d for d in j}, f"net edges acceptance -> pulse: {sorted(to_net)}"
    assert back <= {STAGES + 1 + d for d in j}, f"host edges response acceptance -> answer: {sorted(back)}"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_cmd_during_mailbox_reset(dut):
    """Found by tc_cmd_latency (S7): right after reset the mailbox FIFO is in its reset handshake (wr_rst_busy, so
    wr_count reads 16) and the fence holds a command until it is out; the command then goes through once. Each
    round: both resets, a command at once, its pulse after the host edge where the mailbox busy fell."""
    w = await start(dut)
    o, r = w.ord, w.root
    busy_fell: list[int] = []

    async def watch():
        last = 1
        while True:
            await RisingEdge(r.host_clk)
            edge = get_sim_time()
            await ReadOnly()
            now = int(r.mbox_wr_busy.value)
            if last and not now:
                busy_fell.append(edge)
            last = now

    cocotb.start_soon(watch())
    held = 0
    for _ in range(6):
        w.drv.reset(dut.host_rst)
        await reset(dut)
        await ReadOnly()
        held += int(r.mbox_wr_busy.value)
        await RisingEdge(dut.host_clk)
        n, falls = len(o.pulses), len(busy_fell)
        await wr(w, COMMANDS["RULE_DISARM"], 0xA5)
        assert [(p.name, p.data) for p in o.pulses[n:]] == [("RULE_DISARM", (0xA5,))]
        assert len(busy_fell) > falls and o.pulses[n].edge > busy_fell[falls], "a command passed the mailbox reset"
    assert held, "the mailbox was never still busy when the command was written"


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_tx_commit(dut):
    """Decisions 26, 34, 37: with the path closed (kill 1 after reset) TX_COMMIT sends the template in wire order
    (byte i in [8i+7:8i]; hand-checked) and the OUCH constants; TX_STATUS.cfg_refused is the last commit's result
    (cleared by an accepted one), cfg_valid sticks once one was accepted. With the path open (kill 0, tx_en 1, a
    valid config) TX_COMMIT is refused without a cfg_tx_wr; closed again by tx_en = 0 or kill = 1, it goes."""
    w = await start(dut)
    o = w.ord
    hand = TxCfg(bytes(range(42)), 0x59, 0x41, 0x4E, 0x4E)
    await write_all(w, hand.writes())
    o.refuse_tx = True
    await wr(w, COMMANDS["TX_COMMIT"], 1)
    p = o.pulses[-1]
    assert p.name == "TX_COMMIT" and p.data[0] & 0xFFFF == 0x0100 and p.data[0] >> (8 * 41) == 41
    assert p.data == (le_int(bytes(range(42))), 0x59, 0x41, 0x4E, 0x4E)
    assert await rd(w, TX_STATUS) == 0b010, "refused, never valid"
    valid = 0
    for k in range(20):
        cfg = TxCfg.random(w.rng)
        await write_all(w, cfg.writes())
        o.refuse_tx = w.rng.random() < 0.4
        await wr(w, COMMANDS["TX_COMMIT"], 1)
        assert o.pulses[-1].name == "TX_COMMIT" and o.pulses[-1].data[0] == le_int(cfg.template)
        valid |= not o.refuse_tx
        assert await rd(w, TX_STATUS) == valid | int(o.refuse_tx) << 1, f"commit {k}"
    o.refuse_tx = False
    await open_path(w)
    for closer in ("open", "tx_en", "open", "kill", "open"):
        if closer == "tx_en":
            await wr(w, TX_CTRL, 0)
        elif closer == "kill":
            await wr(w, RISK_CTRL, 1)
        n = len(o.named("TX_COMMIT"))
        await write_all(w, TxCfg.random(w.rng).writes())
        await wr(w, COMMANDS["TX_COMMIT"], 1)
        if closer == "open":
            assert o.pulses[-1].name == "TX_REFUSED" and len(o.named("TX_COMMIT")) == n, "a commit with the path open"
            assert o.pulses[-1].kill == 0 and o.pulses[-1].tx_en == 1
            assert await rd(w, TX_STATUS) == 0b011
        else:
            assert o.pulses[-1].name == "TX_COMMIT", f"no commit with the path closed by {closer}"
            assert await rd(w, TX_STATUS) == 0b001
            await open_path(w)


@cocotb.test(timeout_time=10, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_userref(dut):
    """Decisions 12, 22, 34: TX_USERREF_NEXT sends its value (cmd_userref_wr); TX_STATUS.userref_ignored is the last
    write's result (risk_check's ev_userref_ignored); TX_USERREF reads risk_check's counter through the channel,
    in bus order (after a write it reads the new value or the old one when ignored)."""
    w = await start(dut)
    o = w.ord
    current = 1
    for _ in range(40):
        value = w.rng.choice([0, 1, 0xFFFF_FFFF, w.rng.getrandbits(32)])
        o.ignore_userref = w.rng.random() < 0.5
        await wr(w, COMMANDS["USERREF"], value)
        assert (o.pulses[-1].name, o.pulses[-1].data) == ("USERREF", (value,))
        current = current if o.ignore_userref else value
        assert await rd(w, TX_STATUS) >> 2 & 1 == int(o.ignore_userref)
        assert await rd(w, TX_USERREF) == current
        nxt = w.rng.getrandbits(32)  # the counter moves (orders sent)
        w.root.st_userref.value = nxt
        current = nxt
        assert await rd(w, TX_USERREF) == current


async def send(w: Wrap, order: MboxOrder) -> None:
    await write_all(w, order.writes())
    await wr(w, MBOX_SEND, 1)


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_mailbox(dut):
    """P5, decisions 34, 35: MBOX_SEND copies the fields (hand-checked byte order of stock and ClOrdID); 16 fit, each
    lowering MBOX_SPACE by 1 (live); the 17th is dropped: MBOX_STATUS.dropped right after it, MBOX_DROP counts it,
    and the next send that fits clears the bit. Orders leave in order, whole. Two SEND writes back to back send
    two; a write with byte 0 disabled right after a SEND sends nothing more."""
    w = await start(dut)
    o = w.ord
    hand = MboxOrder(0x53, 0x0102_0304, 0x0506_0708, 0xBEEF, b"AAPL    ", 0x33, b"ABCxyz 0123   ")
    port = hand.at_port()
    assert port[4] & 0xFF == 0x41 and port[4] >> 56 == 0x20, "stock byte 0 in [7:0]"
    assert port[6] & 0xFF == 0x41 and port[6] >> 104 == 0x20, "ClOrdID byte 0 in [7:0], byte 13 in [111:104]"
    for _ in range(20):
        await RisingEdge(dut.host_clk)
    sent = [hand] + [MboxOrder.random(w.rng) for _ in range(15)]
    for k, order in enumerate(sent):
        await send(w, order)
        assert await rd(w, MBOX_STATUS) == 0
        assert await rd(w, MBOX_SPACE) == 15 - k
    extra = MboxOrder.random(w.rng)
    await send(w, extra)
    assert await rd(w, MBOX_STATUS) == 1, "the 17th send was not reported dropped"
    assert await rd(w, addr("TX.MBOX_DROP.LO")) == 1 and await rd(w, addr("TX.MBOX_DROP.HI")) == 0
    o.host_p_ready = 0.5
    while len(o.taken) < 16:
        await RisingEdge(dut.net_clk)
    for _ in range(40):
        await RisingEdge(dut.net_clk)
    assert [f for _, f in o.taken] == [s.at_port() for s in sent], "mailbox orders differ or were reordered"
    assert await rd(w, MBOX_SPACE) == 16
    await send(w, extra)
    assert await rd(w, MBOX_STATUS) == 0, "a send that fits did not clear dropped"
    pend = [w.drv.write_nowait(MBOX_SEND, 1), w.drv.write_nowait(MBOX_SEND, 1), w.drv.write_nowait(MBOX_SEND, 0, 0b1110)]
    for q in pend:
        assert (await q.wait())[1] == OKAY
    for _ in range(60):
        await RisingEdge(dut.net_clk)
    assert [f for _, f in o.taken[16:]] == [extra.at_port()] * 3, "back-to-back sends: two, plus the earlier one"
    for _ in range(50):
        order = MboxOrder.random(w.rng)
        o.host_p_ready = w.rng.choice([0.3, 1.0])  # drains faster than a send's 13 bus writes fill it
        await send(w, order)
        sent.append(order)
    o.host_p_ready = 1.0
    for _ in range(80):
        await RisingEdge(dut.net_clk)
    assert [f for _, f in o.taken[19:]] == [s.at_port() for s in sent[16:]]
    assert await rd(w, addr("TX.MBOX_DROP.LO")) == 1


@cocotb.test(timeout_time=30, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_fence_mailbox(dut):
    """Decision 36: a command written after MBOX_SENDs reaches net only after the last of those orders was taken
    and decided (at least 3 net edges after the edge that takes it: risk_check decides in the cycle before edge
    k+3). While the orders wait, the command waits too (no pulse, no answer). Also with the reader always
    ready and the command right behind the send."""
    w = await start(dut)
    o = w.ord
    for _ in range(20):
        await RisingEdge(dut.host_clk)
    margins = []
    for k in range(20):
        for _ in range(w.rng.randint(1, 6)):
            await send(w, MboxOrder.random(w.rng))
        name, writes, value, _ = random_command(w)
        await write_all(w, writes)
        n = len(o.pulses)
        if k % 2:
            o.host_p_ready = 1.0
            await wr(w, COMMANDS[name], value)
        else:
            q = w.drv.write_nowait(COMMANDS[name], value)
            for _ in range(60):
                await RisingEdge(dut.net_clk)
            assert len(o.pulses) == n and q.result is None, "a command passed queued mailbox orders"
            o.host_p_ready = 0.5
            await q.wait()
        p = o.pulses[n]
        last = o.taken[-1][0]
        margin = sum(last < e <= p.edge for e in w.net_edges)
        assert margin >= 3, f"command {k} at net {margin} edges after the last order was taken"
        margins.append(margin)
        o.host_p_ready = 0.0
    dut._log.info(f"take -> command pulse net edges: {sorted(set(margins))}")


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_fence_controls(dut):
    """Decision 36: a command written right after a RISK_CTRL.kill or TX_CTRL.tx_en write (no wait) reaches net with
    that value already in ctl_kill / ctl_tx_en (the levels sync in STAGES edges, the command waits for them)."""
    w = await start(dut)
    o = w.ord
    await open_path(w)
    kill, tx_en = 0, 1
    for k in range(40):
        if w.rng.random() < 0.5:
            kill ^= 1
            q0 = w.drv.write_nowait(RISK_CTRL, kill)
        else:
            tx_en ^= 1
            q0 = w.drv.write_nowait(TX_CTRL, tx_en)
        name = w.rng.choice(["USERREF", "BUDGET_ADD", "RULE_ARM"])
        n = len(o.pulses)
        q1 = w.drv.write_nowait(COMMANDS[name], w.rng.getrandbits(32))
        assert (await q0.wait())[1] == OKAY and (await q1.wait())[1] == OKAY
        p = o.pulses[n]
        assert (p.kill, p.tx_en) == (kill, tx_en), f"command {k} at net before the control write (kill {kill}, tx_en {tx_en})"


@cocotb.test(timeout_time=30, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_kill_latency(dut):
    """Decision 38 (user, 2026-11-11): RISK_CTRL.kill = 1 written live (kill 0) reaches ctl_kill STAGES net edges
    after the register changes (+1 with jitter); measured from the write's W handshake on an idle bus, behind a
    command in flight (mailbox empty), and behind a fenced command waiting for a full mailbox (16 orders, reader
    always ready, no hardware order taking the free cycles). One more round holds the reader for 100 net cycles:
    the command and the kill write behind it wait, and ctl_kill stays 0. The logged times are the README values."""
    w = await start(dut)
    o = w.ord
    r = w.root
    kill_reg = r.hwif_out.RISK.RISK_CTRL.kill.value  # struct member `value` (a handle)
    await open_path(w)
    results: dict[str, list[int]] = {"idle": [], "behind a command": [], "behind a full mailbox": []}
    sync = set()

    async def rise() -> tuple[int, int]:
        """(time the register changes, time ctl_kill rises)."""
        t_reg = None
        while True:
            await RisingEdge(r.net_clk)
            await ReadOnly()
            if t_reg is None and kill_reg.value:
                t_reg = get_sim_time()
            if r.ctl_kill.value:
                return t_reg, get_sim_time()

    for case in [*results, "held"]:
        for _ in range(1 if case == "held" else 8):
            if case in ("behind a full mailbox", "held"):
                for _ in range(16):
                    await send(w, MboxOrder.random(w.rng))
            q_cmd = None
            if case != "idle":
                q_cmd = w.drv.write_nowait(COMMANDS["BUDGET_ADD"], 1)
                while int(r.h_state_q.value) == 0:  # the command is in the channel
                    await RisingEdge(r.host_clk)
            t0_w = len(w.drv.hs_time["w"])
            q = w.drv.write_nowait(RISK_CTRL, 1)
            watch = cocotb.start_soon(rise())
            if case == "behind a full mailbox":
                o.host_p_ready = 1.0
            if case == "held":
                for _ in range(100):
                    await RisingEdge(r.net_clk)
                await ReadOnly()
                assert r.ctl_kill.value == 0 and not q_cmd.done.is_set(), "kill passed the waiting command"
                await RisingEdge(r.host_clk)
                o.host_p_ready = 1.0
            t_reg, t_kill = await watch  # resumes in the ReadOnly phase of that edge
            await RisingEdge(r.host_clk)
            await q.wait()
            if q_cmd is not None:
                await q_cmd.wait()
                assert o.acks and t_reg > [e for e, n in o.acks if n == "BUDGET_ADD"][-1] - 1
            t_w = w.drv.hs_time["w"][t0_w]
            sync.add(sum(t_reg < e <= t_kill for e in w.net_edges))
            if case != "held":
                results[case].append(t_kill - t_w)
            o.host_p_ready = 0.0
            await open_path(w)
    for case, ts in results.items():
        dut._log.info(f"kill write (W handshake) -> ctl_kill, {case}: {min(ts)}..{max(ts)} ps")
    j = {0, 1} if jitter() else {0}
    assert sync <= {STAGES - 1 + d for d in j} | {STAGES + d for d in j}, f"register -> ctl_kill net edges {sorted(sync)}"
    assert max(results["idle"]) < min(results["behind a full mailbox"])


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_ctl_tx_en_needs_cfg(dut):
    """Decision 26: TX_CTRL.tx_en = 1 does not open ctl_tx_en before an accepted TX_COMMIT (st_tx_cfg_valid); a refused
    one does not either; kill does not gate ctl_tx_en (each is its own level)."""
    w = await start(dut)
    o = w.ord
    await wr(w, TX_CTRL, 1)
    o.refuse_tx = True
    await write_all(w, TxCfg.random(w.rng).writes())
    await wr(w, COMMANDS["TX_COMMIT"], 1)
    for _ in range(STAGES + 6):
        await RisingEdge(dut.net_clk)
        await ReadOnly()
        assert dut.ctl_tx_en.value == 0, "ctl_tx_en without a valid TX config"
    await RisingEdge(dut.host_clk)
    o.refuse_tx = False
    await wr(w, COMMANDS["TX_COMMIT"], 1)
    for _ in range(4):
        await RisingEdge(dut.net_clk)
    await ReadOnly()
    assert dut.ctl_tx_en.value == 1 and dut.ctl_kill.value == 1


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_locate_allow(dut):
    """LOCATE_ALLOW through the channel: every WSTRB on the first, the last and random words, read back, with random
    backpressure at risk_check's port; every access reaches the port once with its word, data and strobes; the
    write response comes after the port took it."""
    w = await start(dut)
    o = w.ord
    o.loc_p_ready = 0.4
    words = [0, LOCATE_ALLOW.entries - 1] + w.rng.sample(range(1, LOCATE_ALLOW.entries - 1), 6)
    want: dict[int, int] = {}
    for strb in range(16):
        for word in words:
            data = w.rng.getrandbits(32)
            await wr(w, LOCATE_ALLOW.addr + 4 * word, data, strb)
            c = o.loc_cmds[-1]
            assert (c[1].wr, c[1].addr, c[1].wstrb) == (1, word, strb) and c[0] < o.acks[-1][0], (
                "not this write at the port, or answered before the port took it")
            bits = wstrb_mask(strb)
            want[word] = (want.get(word, 0) & ~bits) | (data & bits)
        for word in words:
            assert await rd(w, LOCATE_ALLOW.addr + 4 * word) == want[word], f"word {word}"
    assert {k: o.loc_ram.get(k, 0) for k in want} == want
    assert len(o.loc_cmds) == 16 * len(words) * 2
    assert {c.wstrb for _, c in o.loc_cmds if c.wr} == set(range(16))


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_order_reset_in_flight(dut):
    """Both resets at 10 points spread over a command's round trip (fence, request handshake, pulse, settle, response
    handshake): no pulse and no answer from before the reset come afterwards; kill is 1 and tx_en 0 again; a fresh
    command works once."""
    w = await start(dut)
    o = w.ord
    phases = set()
    for k in range(10):
        n = len(o.pulses)
        w.drv.write_nowait(COMMANDS["BUDGET_ADD"], 5)
        while int(w.root.h_state_q.value) == 0:
            await RisingEdge(dut.host_clk)
        for _ in range(k * (4 * STAGES + 12) // 9):
            await RisingEdge(dut.host_clk)
        phases.add("before net" if len(o.pulses) == n else "at net")
        acks = len(o.acks)
        w.drv.reset(dut.host_rst)
        await reset(dut)
        for _ in range(4 * STAGES + 30):
            await RisingEdge(dut.net_clk)
        pulses = len(o.pulses)
        assert len(o.acks) == acks, "an answer from before the reset"
        await ReadOnly()
        assert dut.ctl_kill.value == 1 and dut.ctl_tx_en.value == 0
        await RisingEdge(dut.host_clk)
        await wr(w, COMMANDS["RULE_ARM"], 0x5A)
        assert [(p.name, p.data) for p in o.pulses[pulses:]] == [("RULE_ARM", (0x5A,))]
    assert phases == {"before net", "at net"}, f"reset phases: {sorted(phases)}"


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_snapshot_order_counters(dut):
    """The order counters and levels in the snapshot (decision 30): random order events of every rule, source and
    reason (out-of-enum codes too), ord_queued, st_armed and st_budget, against the model exactly; every order
    counter moved."""
    w = await start(dut)
    w.snap.p_valid = 0.5
    got = None
    for _ in range(12):
        seq, got = await w.snap.snapshot()
        await w.snap.check(seq, got)
    named = dict(zip(SNAP_NAMES, got, strict=True))
    assert all(named[n] for n in ORD_NAMES), {n: named[n] for n in ORD_NAMES}


# The long test runs as its own pytest entry, at one profile (it checks the counter width, not CDC).
LONG_TESTS = ("tc_hi_lo_read",)


def directed_tests() -> list[str]:
    return [name for name in globals() if name.startswith("tc_") and name not in LONG_TESTS]


# Clock profiles: (net period, host period); jitter = the CDC jitter model in the build.
@pytest.mark.parametrize(
    "group, jit, ratio",
    [("directed", 0, "net2host"), ("directed", 0, "host2net"), ("directed", 0, "jitter"),
     ("directed", 1, "net2host"), ("directed", 1, "jitter"), *((n, 0, "net2host") for n in LONG_TESTS)],
)  # fmt: skip
def test_hft_csr_wrap(group, jit, ratio):
    assert ratio in PROFILES
    run_cocotb(
        toplevel="hft_csr_wrap",
        filelists=["common.f", "core.f", "csr.f"],
        test_module="test_hft_csr_wrap",
        defines={"HFT_SIM_CDC_JITTER": 1} if jit else {},
        testcase=directed_tests() if group == "directed" else group,
        extra_env={"GROUP": group, "JITTER": jit, "RATIO": ratio},
    )
