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

Counters: a generator drives stat_* and mpkt_* on net_clk and moves feed_*; a monitor records the edge that samples each
event. A snapshot taken on acceptance edge E must equal the sum of the events sampled before E,
exactly (the event sampled on E belongs to the next snapshot), for the rx_stats and the mold_stats
counters, and the feed state must be the one sampled on E. The acceptance and landing edges are
observed on the snapshot handshake inside the wrapper (grey box). The bus side is the generated block
(tested in test_hft_csr.py); axil_props is bound to it here too. The harness (Wrap, Snap, NetCfg)
is in common/csr_env.py, shared with the system test tb/system/test_rx_csr.py.
"""

import os
from dataclasses import replace

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, gather

from common.clocks import PROFILES, profile, start_clock
from common.csr_env import (
    FEED_COMMIT,
    N_CNT,
    SCRATCH,
    STATS_CTRL,
    FeedCfg,
    NetCfg,
    Snap,
    Wrap,
)
from common.runner import run_cocotb
from model.csr import OKAY, CsrModel, addr
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
                 "feed_expected_seq", "feed_synced", "feed_eos"):  # fmt: skip
        getattr(dut, name).value = 0
    dut.host_rst.value = 1
    dut.net_rst.value = 1
    start_clock(dut.net_clk, net_ps, jit)
    start_clock(dut.host_clk, host_ps, jit)
    await reset(dut)
    w = Wrap(dut)
    w.snap = Snap(w)
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
    counters of one net cycle (every event sampled before the acceptance edge), all eleven."""
    w = await start(dut)
    for _ in range(40):
        w.snap.p_valid = w.rng.choice([0.05, 0.5, 1.0])
        for _ in range(w.rng.randint(0, 40)):
            await RisingEdge(dut.net_clk)
        seq, got = await w.snap.snapshot()
        await w.snap.check(seq, got)
    assert {r for _, r, _ in w.snap.events} == set(RxReason), "every counter moved"


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
    """After reset: cfg_feed_en, cfg_session_check and cfg_resync = 0 on every net edge (the Feed
    monitor checks it until the first transfer), FEED_STATUS.commit_busy = 0, and FEED writes
    without a commit change nothing in the net domain."""
    w = await start(dut)
    assert await w.feed.busy() == 0
    await w.feed.write_cfg(replace(FeedCfg.random(w.rng), enable=1, session_check=1))
    for _ in range(50):
        await RisingEdge(dut.net_clk)
        await ReadOnly()
        assert dut.cfg_feed_en.value == 0, "feed_en reached moldudp64_rx without a commit"
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
