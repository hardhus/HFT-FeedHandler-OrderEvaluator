"""hft_csr_wrap: config commit from the host CSR block to the net clock domain (audit/10 §2.3, S4)
and the counter snapshot back (§2.4, S5).

Clocks: the RATIO profile gives (net period, host period): net2host = 156.25 / 250 MHz (the real
pair), host2net = net faster than host (e.g. a 125 MHz host on the A7 profile is still slower than
net; this checks the other order), jitter = both clocks drifting. Both resets are asserted together
and released in their own domain (one source, one reset_sync per domain).

A monitor samples every cfg_* output on every net_clk edge. The receiver may only ever see the
configurations software committed, each whole, in commit order (a coalesced commit may skip
older ones), never a shadow write that no commit covered.

Counters: a generator drives stat_* on net_clk and records the edge that samples each event. A
snapshot taken on acceptance edge E must equal the sum of the events sampled before E, exactly
(the event sampled on E belongs to the next snapshot). The acceptance and landing edges are
observed on the snapshot handshake inside the wrapper (grey box). The bus side is the generated block
(tested in test_hft_csr.py); axil_props is bound to it here too.
"""

import os
import random
from dataclasses import dataclass, replace

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, gather
from cocotb.utils import get_sim_time

from common.axil import AxilDriver
from common.clocks import PROFILES, profile, start_clock
from common.runner import run_cocotb
from model.csr import OKAY, CsrModel, addr
from model.net import RxReason

RUNTIME_PARAMS = ("ratio", "group")
N_FILTERS = 8
STAGES = 2

NET_CTRL = addr("NET.NET_CTRL")
LOCAL_MAC_LO = addr("NET.LOCAL_MAC_LO")
LOCAL_MAC_HI = addr("NET.LOCAL_MAC_HI")
NET_COMMIT = addr("NET.NET_COMMIT")
NET_STATUS = addr("NET.NET_STATUS")
STATS_CTRL = addr("STATS.STATS_CTRL")
STATS_SEQ = addr("STATS.STATS_SEQ")
SCRATCH = addr("GLOBAL.SCRATCH")
# Counter registers in rx_stats order (RX_PKTS, RX_BYTES, DROP_<reason> by reason code).
COUNTERS = ["RX_PKTS", "RX_BYTES"] + [f"DROP_{r.name}" for r in list(RxReason)[1:]]
N_CNT = len(COUNTERS)
MASK64 = (1 << 64) - 1


def jitter() -> bool:
    """CDC jitter model in the build, or drifting clocks: latencies may be one edge longer."""
    return os.environ["JITTER"] == "1" or os.environ["RATIO"] == "jitter"


@dataclass(frozen=True)
class NetCfg:
    rx_en: int = 0
    accept_bcast: int = 0
    mac: int = 0
    ip: tuple[int, ...] = (0,) * N_FILTERS
    port: tuple[int, ...] = (0,) * N_FILTERS
    en: tuple[int, ...] = (0,) * N_FILTERS

    @staticmethod
    def random(rng: random.Random) -> "NetCfg":
        return NetCfg(
            rx_en=rng.getrandbits(1),
            accept_bcast=rng.getrandbits(1),
            mac=rng.getrandbits(48),
            ip=tuple(rng.getrandbits(32) for _ in range(N_FILTERS)),
            port=tuple(rng.getrandbits(16) for _ in range(N_FILTERS)),
            en=tuple(rng.getrandbits(1) for _ in range(N_FILTERS)),
        )

    @staticmethod
    def from_regs(regs: dict[int, int]) -> "NetCfg":
        """The configuration the NET shadow registers hold (missing registers: reset value 0)."""
        r = lambda a: regs.get(a, 0)  # noqa: E731
        return NetCfg(
            rx_en=r(NET_CTRL) & 1,
            accept_bcast=r(NET_CTRL) >> 1 & 1,
            mac=(r(LOCAL_MAC_HI) & 0xFFFF) << 32 | r(LOCAL_MAC_LO),
            ip=tuple(r(addr(f"NET.FILTER[{i}].IP")) for i in range(N_FILTERS)),
            port=tuple(r(addr(f"NET.FILTER[{i}].PORT_EN")) & 0xFFFF for i in range(N_FILTERS)),
            en=tuple(r(addr(f"NET.FILTER[{i}].PORT_EN")) >> 16 & 1 for i in range(N_FILTERS)),
        )

    def writes(self) -> list[tuple[int, int]]:
        """(address, data) of every NET shadow register."""
        out = [
            (NET_CTRL, self.rx_en | self.accept_bcast << 1),
            (LOCAL_MAC_LO, self.mac & 0xFFFF_FFFF),
            (LOCAL_MAC_HI, self.mac >> 32),
        ]
        for i in range(N_FILTERS):
            out.append((addr(f"NET.FILTER[{i}].IP"), self.ip[i]))
            out.append((addr(f"NET.FILTER[{i}].PORT_EN"), self.port[i] | self.en[i] << 16))
        return out


def read_cfg(dut) -> NetCfg:
    ip, port, en = int(dut.cfg_filter_ip.value), int(dut.cfg_filter_port.value), int(dut.cfg_filter_en.value)
    return NetCfg(
        rx_en=int(dut.cfg_rx_en.value),
        accept_bcast=int(dut.cfg_accept_bcast.value),
        mac=int(dut.cfg_local_mac.value),
        ip=tuple(ip >> (32 * i) & 0xFFFF_FFFF for i in range(N_FILTERS)),
        port=tuple(port >> (16 * i) & 0xFFFF for i in range(N_FILTERS)),
        en=tuple(en >> i & 1 for i in range(N_FILTERS)),
    )


class Wrap:
    """Harness: bus driver, committed configurations and the net-side cfg monitor."""

    def __init__(self, dut):
        self.dut = dut
        self.drv = AxilDriver(dut, dut.host_clk)
        self.rng = random.Random(random.getrandbits(32))
        self.regs: dict[int, int] = {}  # NET shadow registers written since reset
        self.committed: list[NetCfg] = []  # every commit, in order
        self.seen: list[tuple[int, int, NetCfg]] = []  # (time, commit index, cfg) at each change
        self.net_edges: list[int] = []  # time of every net_clk rising edge
        self.accepts: list[int] = []  # time of every commit handshake acceptance (host edge)
        self.pulses: list[int] = []  # time of the host edge after which NET_COMMIT is high
        self.host_edges: list[int] = []
        self.snap: Snap  # set by start()
        cocotb.start_soon(self._net_monitor())
        cocotb.start_soon(self._host_monitor())

    async def _net_monitor(self) -> None:
        """Before the first transfer after reset only cfg_rx_en is defined (0); from the cycle the
        first transfer lands (the handshake's dst_valid) every field must be a committed config."""
        dut = self.dut
        last_idx = -1
        last: NetCfg | None = None
        loaded = False
        while True:
            await RisingEdge(dut.net_clk)
            self.net_edges.append(get_sim_time())
            await ReadOnly()
            if dut.net_rst.value:
                last_idx, last, loaded = -1, None, False
                continue
            cfg = read_cfg(dut)
            loaded = loaded or bool(dut.u_cfg_cdc.dst_valid.value)
            if not loaded:
                assert cfg.rx_en == 0, "cfg_rx_en = 1 before any commit was applied"
                continue
            if cfg == last:
                continue
            matches = [i for i, c in enumerate(self.committed) if c == cfg and i >= last_idx]
            assert matches, f"cfg_* is not a committed configuration (or went back): {cfg}"
            last_idx, last = matches[0], cfg
            self.seen.append((get_sim_time(), last_idx, cfg))

    async def _host_monitor(self) -> None:
        """Host edges; the edge after which NET_COMMIT is high; the edge that accepts a transfer
        (src_valid and src_ready in the cycle before it)."""
        dut = self.dut
        commit = dut.hwif_out.NET.NET_COMMIT.commit.value  # struct member `value` (a handle)
        cdc = dut.u_cfg_cdc
        await RisingEdge(dut.host_clk)
        self.host_edges.append(get_sim_time())
        while True:
            await ReadOnly()
            accept = bool(cdc.src_valid.value and cdc.src_ready.value)
            if commit.value:
                self.pulses.append(self.host_edges[-1])
            await RisingEdge(dut.host_clk)
            self.host_edges.append(get_sim_time())
            if accept:
                self.accepts.append(self.host_edges[-1])

    async def write(self, a: int, data: int) -> None:
        assert await self.drv.write(a, data) == OKAY, f"write {a:#06x}"
        if a != NET_COMMIT:
            self.regs[a] = data

    async def write_cfg(self, cfg: NetCfg) -> None:
        """Write every NET shadow register, in random order."""
        writes = cfg.writes()
        self.rng.shuffle(writes)
        for a, d in writes:
            await self.write(a, d)

    async def commit(self) -> None:
        """NET_COMMIT; the expected configuration is the shadow at this moment."""
        self.committed.append(NetCfg.from_regs(self.regs))
        await self.write(NET_COMMIT, 1)

    async def busy(self) -> int:
        data, resp = await self.drv.read(NET_STATUS)
        assert resp == OKAY
        return data & 1

    async def wait_idle(self, limit: int = 200) -> None:
        for _ in range(limit):
            if not await self.busy():
                return
        raise AssertionError("commit_busy never fell")

    async def applied(self) -> NetCfg:
        """cfg_* now (after commit_busy read 0, the last commit must be in it)."""
        await RisingEdge(self.dut.net_clk)
        await ReadOnly()
        cfg = read_cfg(self.dut)
        await RisingEdge(self.dut.host_clk)
        return cfg


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


class Snap:
    """Counter side: stat_* generator with its event log, snapshot handshake monitors and the
    software snapshot protocol."""

    def __init__(self, w: Wrap):
        self.w, self.dut = w, w.dut
        self.events: list[tuple[int, int, int]] = []  # (sampling net edge time, reason, bytes)
        self.accepts: list[int] = []  # net edges that take a snapshot (data handshake accepts)
        self.req_valid: list[int] = []  # net edges after which the request is at net (dst_valid)
        self.req_accepts: list[int] = []  # host edges that accept a request
        self.pulses: list[int] = []  # host edges after which STATS_CTRL.snapshot is high
        self.landed: list[int] = []  # host edges after which a snapshot has landed (dst_valid)
        self.p_valid = 0.0  # probability of a stat event per net cycle
        self.drive = True  # False: the test drives stat_* itself
        self.pending_cycles = {"host": 0, "net": 0}  # cycles with a coalescing request pending
        cocotb.start_soon(self._generator())
        cocotb.start_soon(self._net_monitor())
        cocotb.start_soon(self._host_monitor())

    async def _generator(self) -> None:
        dut, rng = self.dut, self.w.rng
        reasons = list(RxReason)
        while True:
            if not self.drive:
                await RisingEdge(dut.net_clk)
                continue
            valid = rng.random() < self.p_valid
            reason = rng.choice(reasons)
            nbytes = rng.choice([0, 0xFFFF, rng.randint(0, 0xFFFF)])
            dut.stat_valid.value = int(valid)
            dut.stat_reason.value = int(reason)
            dut.stat_bytes.value = nbytes
            await RisingEdge(dut.net_clk)
            if valid and not dut.net_rst.value:
                self.events.append((get_sim_time(), reason, nbytes))

    async def _net_monitor(self) -> None:
        """Net edges that accept a snapshot (src_valid and src_ready in the cycle before them) and
        net edges after which the request is at net (the request handshake's dst_valid)."""
        dut, cdc, req = self.dut, self.dut.u_snap_cdc, self.dut.u_snap_req_cdc
        await RisingEdge(dut.net_clk)
        while True:
            edge = get_sim_time()
            await ReadOnly()
            if req.dst_valid.value:
                self.req_valid.append(edge)
            self.pending_cycles["net"] += int(dut.snap_net_pending_q.value)
            accept = bool(cdc.src_valid.value and cdc.src_ready.value)
            await RisingEdge(dut.net_clk)
            if accept:
                self.accepts.append(get_sim_time())

    async def _host_monitor(self) -> None:
        dut = self.dut
        pulse = dut.hwif_out.STATS.STATS_CTRL.snapshot.value  # struct member `value` (a handle)
        req = dut.u_snap_req_cdc
        await RisingEdge(dut.host_clk)
        while True:
            edge = get_sim_time()
            await ReadOnly()
            if pulse.value:
                self.pulses.append(edge)
            if dut.u_snap_cdc.dst_valid.value:
                self.landed.append(edge)
            self.pending_cycles["host"] += int(dut.snap_pending_q.value)
            accept = bool(req.src_valid.value and req.src_ready.value)
            await RisingEdge(dut.host_clk)
            if accept:
                self.req_accepts.append(get_sim_time())

    def model_at(self, edge: int) -> list[int]:
        """Counters of every event sampled before `edge` (the snapshot rule)."""
        c = [0] * N_CNT
        for t, reason, nbytes in self.events:
            if t >= edge:
                break
            c[0] += 1
            c[1] += nbytes
            if reason:
                c[1 + reason] += 1
        return [v & MASK64 for v in c]

    async def read_counters(self) -> list[int]:
        out = []
        for name in COUNTERS:
            lo, r1 = await self.w.drv.read(addr(f"STATS.{name}.LO"))
            hi, r2 = await self.w.drv.read(addr(f"STATS.{name}.HI"))
            assert r1 == r2 == OKAY
            out.append(hi << 32 | lo)
        return out

    async def seq(self) -> int:
        data, resp = await self.w.drv.read(STATS_SEQ)
        assert resp == OKAY
        return data

    async def snapshot(self, limit: int = 300) -> tuple[int, list[int]]:
        """The documented software protocol: read STATS_SEQ, request, wait for a new STATS_SEQ,
        read. Returns (STATS_SEQ, counters)."""
        before = await self.seq()
        assert await self.w.drv.write(STATS_CTRL, 1) == OKAY
        for _ in range(limit):
            now = await self.seq()
            if now != before:
                return now, await self.read_counters()
        raise AssertionError("STATS_SEQ never changed after a snapshot request")

    async def quiet(self, limit: int = 400) -> None:
        """Wait until no snapshot request or transfer is anywhere in the wrapper (grey box)."""
        d = self.dut
        for _ in range(limit):
            await ReadOnly()
            idle = not (d.snap_pending_q.value or d.snap_net_pending_q.value)
            idle = idle and d.u_snap_req_cdc.src_ready.value and d.u_snap_cdc.src_ready.value
            if idle and len(self.landed) == len(self.accepts):
                break
            await RisingEdge(d.host_clk)
        else:
            raise AssertionError("the snapshot path never became idle")
        for _ in range(2):  # STATS_SEQ counts the landing one edge later
            await RisingEdge(d.host_clk)

    async def check(self, seq: int, got: list[int]) -> None:
        """Counters read after STATS_SEQ became `seq` are the snapshot taken on accepts[seq-1]."""
        taken, landed = len(self.accepts), len(self.landed)
        assert seq == landed == taken, f"STATS_SEQ {seq}, {landed} landed, {taken} taken"
        want = self.model_at(self.accepts[seq - 1])
        assert got == want, f"snapshot {seq}: {dict(zip(COUNTERS, got))} != model {dict(zip(COUNTERS, want))}"


async def start(dut) -> Wrap:
    """Idle bus and stat inputs, both resets, then both clocks (no edge with undriven inputs)."""
    net_ps, host_ps, jit = profile()
    for name in ("awvalid", "wvalid", "bready", "arvalid", "rready"):
        getattr(dut, f"s_axil_{name}").value = 0
    dut.stat_valid.value = 0
    dut.stat_reason.value = 0
    dut.stat_bytes.value = 0
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
