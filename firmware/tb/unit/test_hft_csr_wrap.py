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
must be the one sampled FEED_DELAY = 4 net edges before E (it then belongs to the same packet
events as the counters). The acceptance and landing edges are observed on the snapshot handshake
inside the wrapper (grey box). The bus side is the generated block (tested in test_hft_csr.py);
axil_props is bound to it here too. The harness (Wrap, Snap, Sym, NetCfg, FeedCfg) is in
common/csr_env.py, shared with the system test tb/system/test_rx_csr.py.

ITCH_CTRL is part of the FEED configuration (FeedCfg.class_en/class_filter), so every FEED test
also checks that it moves with FEED_COMMIT, whole. SYMBOL: a model of itch_decoder's command port
(csr_env.Sym) answers with random backpressure; every access must reach the port once, in order and
unchanged, and its ack must come after the command was taken (BRESP = in effect).
"""

import os
from dataclasses import replace

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, gather
from cocotb.utils import get_sim_time

from common.clocks import PROFILES, profile, start_clock
from common.csr_env import (
    FEED_COMMIT,
    ITCH_CTRL,
    MASK64,
    N_CNT,
    SNAP_NAMES,
    SCRATCH,
    STATS_CTRL,
    SYMBOL,
    FeedCfg,
    NetCfg,
    Snap,
    Sym,
    Wrap,
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
                 "sym_req_ready", "sym_rsp_valid", "sym_rsp_rdata"):  # fmt: skip
        getattr(dut, name).value = 0
    dut.host_rst.value = 1
    dut.net_rst.value = 1
    start_clock(dut.net_clk, net_ps, jit)
    start_clock(dut.host_clk, host_ps, jit)
    await reset(dut)
    w = Wrap(dut)
    w.snap = Snap(w)
    w.sym = Sym(w)
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
            if -1 <= edge - w.snap.edge_index[u] <= 4:
                phases.add(edge - w.snap.edge_index[u])
        e = got[idx["FEED.EXPECTED_SEQ"]]
        moved = sum(got[idx[f"FEED.MOLD_{n}"]] for n in ("MSGS", "OVERSIZE_MSGS", "GAP_MSGS"))
        assert (e - e0) & MASK64 == moved & MASK64, (
            f"snapshot {seq}: EXPECTED_SEQ - E0 = {(e - e0) & MASK64}, counters account for {moved & MASK64}")
        assert got[idx["FEED.FEED_STATE"]] == 1, "synced"
        if phases == set(range(-1, 5)):
            break
        for _ in range(w.rng.randint(0, 3)):
            await RisingEdge(dut.net_clk)
    assert phases == set(range(-1, 5)), f"snapshot phases around a feed update: {sorted(phases)}"


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
    words = [0, SYMBOL.entries - 1] + [w.rng.randrange(SYMBOL.entries) for _ in range(6)]
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
