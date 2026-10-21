"""Test environment of hft_csr_wrap, shared by its unit test (tb/unit/test_hft_csr_wrap.py, the
wrapper is the DUT) and the system test (tb/system/test_rx_csr.py, the wrapper is u_wrap next to
eth_ip_udp_rx): NET and FEED configurations and their commit, the cfg_* monitors, the resync
requests, and the snapshot of the rx_stats and mold_stats counters and the feed state with its
model (audit/10 §2.3, §2.4; audit/11 §2.7).

`root` is the hft_csr_wrap instance; the bus is driven at the DUT's s_axil_* ports. The monitors
read signals inside the wrapper (grey box): the handshakes' acceptance and landing edges.
"""

import random
from dataclasses import dataclass

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

from common.axil import AxilDriver
from model.csr import OKAY, addr
from model.mold import ACCEPTED, MoldStatus
from model.net import RxReason

N_FILTERS = 8

NET_CTRL = addr("NET.NET_CTRL")
LOCAL_MAC_LO = addr("NET.LOCAL_MAC_LO")
LOCAL_MAC_HI = addr("NET.LOCAL_MAC_HI")
NET_COMMIT = addr("NET.NET_COMMIT")
NET_STATUS = addr("NET.NET_STATUS")
STATS_CTRL = addr("STATS.STATS_CTRL")
STATS_SEQ = addr("STATS.STATS_SEQ")
SCRATCH = addr("GLOBAL.SCRATCH")
FEED_CTRL = addr("FEED.FEED_CTRL")
SESSION = [addr(f"FEED.SESSION_{i}") for i in range(3)]
RESYNC_SEQ_LO = addr("FEED.RESYNC_SEQ_LO")
RESYNC_SEQ_HI = addr("FEED.RESYNC_SEQ_HI")
FEED_COMMIT = addr("FEED.FEED_COMMIT")
FEED_STATUS = addr("FEED.FEED_STATUS")
# Snapshot values, in register order: rx_stats (RX_PKTS, RX_BYTES, DROP_<reason> by reason code),
# then mold_stats (MOLD_<status> by status code, then the message and gap counters), then the feed
# state (EXPECTED_SEQ, FEED_STATE).
COUNTERS = ["RX_PKTS", "RX_BYTES"] + [f"DROP_{r.name}" for r in list(RxReason)[1:]]
MOLD_COUNTERS = [f"MOLD_{s.name}" for s in MoldStatus]
MOLD_COUNTERS += [f"MOLD_{n}" for n in ("MSGS", "DUP_MSGS", "OVERSIZE_MSGS", "GAP_EVENTS", "GAP_MSGS")]
SNAP_NAMES = [f"STATS.{n}" for n in COUNTERS] + [f"FEED.{n}" for n in MOLD_COUNTERS]
SNAP_NAMES += ["FEED.EXPECTED_SEQ", "FEED.FEED_STATE"]
N_CNT = len(SNAP_NAMES)
MASK64 = (1 << 64) - 1


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


@dataclass(frozen=True)
class FeedCfg:
    enable: int = 0
    session_check: int = 0
    resync_learn: int = 0
    filter_mask: int = 0
    session: int = 0  # 10 bytes, byte 0 = [79:72]
    resync_seq: int = 0

    @staticmethod
    def random(rng: random.Random) -> "FeedCfg":
        return FeedCfg(
            enable=rng.getrandbits(1),
            session_check=rng.getrandbits(1),
            resync_learn=rng.getrandbits(1),
            filter_mask=rng.getrandbits(8),
            session=rng.getrandbits(80),
            resync_seq=rng.getrandbits(64),
        )

    @staticmethod
    def from_regs(regs: dict[int, int]) -> "FeedCfg":
        """The configuration the FEED shadow registers hold (missing registers: reset value 0)."""
        r = lambda a: regs.get(a, 0)  # noqa: E731
        return FeedCfg(
            enable=r(FEED_CTRL) & 1,
            session_check=r(FEED_CTRL) >> 1 & 1,
            resync_learn=r(FEED_CTRL) >> 2 & 1,
            filter_mask=r(FEED_CTRL) >> 8 & 0xFF,
            session=r(SESSION[0]) << 48 | r(SESSION[1]) << 16 | r(SESSION[2]) >> 16,
            resync_seq=r(RESYNC_SEQ_HI) << 32 | r(RESYNC_SEQ_LO),
        )

    def writes(self) -> list[tuple[int, int]]:
        """(address, data) of every FEED shadow register."""
        ctrl = self.enable | self.session_check << 1 | self.resync_learn << 2 | self.filter_mask << 8
        return [
            (FEED_CTRL, ctrl),
            (SESSION[0], self.session >> 48),
            (SESSION[1], self.session >> 16 & 0xFFFF_FFFF),
            (SESSION[2], (self.session & 0xFFFF) << 16),
            (RESYNC_SEQ_LO, self.resync_seq & 0xFFFF_FFFF),
            (RESYNC_SEQ_HI, self.resync_seq >> 32),
        ]


def read_feed_cfg(root) -> FeedCfg:
    return FeedCfg(
        enable=int(root.cfg_feed_en.value),
        session_check=int(root.cfg_session_check.value),
        resync_learn=int(root.cfg_resync_learn.value),
        filter_mask=int(root.cfg_filter_mask.value),
        session=int(root.cfg_session.value),
        resync_seq=int(root.cfg_resync_seq.value),
    )


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

    def __init__(self, dut, root=None):
        self.dut = dut
        self.root = root if root is not None else dut  # the hft_csr_wrap instance
        self.drv = AxilDriver(dut, self.root.host_clk)
        self.rng = random.Random(random.getrandbits(32))
        self.regs: dict[int, int] = {}  # NET shadow registers written since reset
        self.committed: list[NetCfg] = []  # every commit, in order
        self.seen: list[tuple[int, int, NetCfg]] = []  # (time, commit index, cfg) at each change
        self.net_edges: list[int] = []  # time of every net_clk rising edge
        self.accepts: list[int] = []  # time of every commit handshake acceptance (host edge)
        self.pulses: list[int] = []  # time of the host edge after which NET_COMMIT is high
        self.host_edges: list[int] = []
        self.snap: Snap  # set by start()
        self.feed = Feed(self)
        cocotb.start_soon(self._net_monitor())
        cocotb.start_soon(self._host_monitor())

    async def _net_monitor(self) -> None:
        """Before the first transfer after reset only cfg_rx_en is defined (0); from the cycle the
        first transfer lands (the handshake's dst_valid) every field must be a committed config."""
        dut = self.root
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
        dut = self.root
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
        await RisingEdge(self.root.net_clk)
        await ReadOnly()
        cfg = read_cfg(self.root)
        await RisingEdge(self.root.host_clk)
        return cfg


class Feed:
    """FEED side: shadow writes, FEED_COMMIT requests, and two monitors.

    Host: the request rule of FEED_COMMIT.resync is the rising edge of its field (a pulse held one
    more cycle by regblock is one request). Each handshake acceptance takes the resync requests seen
    in the cycles since the previous acceptance (a request in the acceptance cycle goes with the
    next transfer); expected[k] is whether transfer k must resync.
    Net: before the first transfer cfg_feed_en, cfg_session_check and cfg_resync are 0. From the
    cycle it lands, the FEED cfg_* are one committed configuration, whole, in commit order; and
    cfg_resync is 1 exactly in the landing cycle of a transfer that must resync."""

    def __init__(self, w: Wrap):
        self.w, self.root = w, w.root
        self.regs: dict[int, int] = {}  # FEED shadow registers written since reset
        self.committed: list[FeedCfg] = []  # the shadow at every FEED_COMMIT write, in order
        self.resync_requests = 0  # writes with FEED_COMMIT.resync = 1
        self.request_edges = 0  # rising edges of the resync field (one per request, held or not)
        self.held = 0  # cycles in which the resync field stayed high from the cycle before
        self.expected: list[bool] = []  # per accepted transfer: carries a resync
        self.landed = 0  # transfers landed at net
        self.resyncs = 0  # cfg_resync pulses
        self.seen: list[FeedCfg] = []  # cfg_* after every change
        self.pending = False  # host monitor: a resync request since the last acceptance
        self.pulse_times: list[int] = []  # host edges after which a request field is high
        self.accept_times: list[int] = []  # host edges that accept a transfer
        self.land_times: list[int] = []  # net edges after which a transfer has landed
        cocotb.start_soon(self._host_monitor())
        cocotb.start_soon(self._net_monitor())

    async def _host_monitor(self) -> None:
        root = self.root
        commit_bit = root.hwif_out.FEED.FEED_COMMIT
        cdc = root.u_feed_cdc
        prev = False  # the resync field in the cycle before
        accept = False  # the transfer is accepted on the next edge
        while True:
            await RisingEdge(root.host_clk)
            edge = get_sim_time()
            if accept:
                self.accept_times.append(edge)
            await ReadOnly()
            if root.host_rst.value:
                prev = self.pending = accept = False
                self.expected.clear()
                continue
            resync = bool(commit_bit.resync.value.value)
            if resync or commit_bit.commit.value.value:
                self.pulse_times.append(edge)
            accept = bool(cdc.src_valid.value and cdc.src_ready.value)
            if accept:
                self.expected.append(self.pending)
                self.pending = False
            if resync and prev:
                self.held += 1
            if resync and not prev:
                self.request_edges += 1
                self.pending = True
            prev = resync

    async def _net_monitor(self) -> None:
        root = self.root
        last_idx = -1
        loaded = False
        while True:
            await RisingEdge(root.net_clk)
            edge = get_sim_time()
            await ReadOnly()
            if root.net_rst.value:
                last_idx, loaded = -1, False
                self.landed = self.resyncs = 0
                self.seen.clear()
                continue
            landing = bool(root.u_feed_cdc.dst_valid.value)
            resync = bool(root.cfg_resync.value)
            loaded = loaded or landing
            if not loaded:
                assert not (root.cfg_feed_en.value or root.cfg_session_check.value or resync), (
                    "cfg_feed_en, cfg_session_check or cfg_resync = 1 before any FEED commit was applied")
                continue
            if landing:
                self.land_times.append(edge)
                assert self.landed < len(self.expected), "a FEED transfer landed that was never accepted"
                want = self.expected[self.landed]
                self.landed += 1
                assert resync == want, f"FEED transfer {self.landed}: cfg_resync {int(resync)}, expected {int(want)}"
            else:
                assert not resync, "cfg_resync outside the landing cycle of a transfer"
            self.resyncs += int(resync)
            cfg = read_feed_cfg(root)
            if self.seen and cfg == self.seen[-1]:
                continue
            matches = [i for i, c in enumerate(self.committed) if c == cfg and i >= last_idx]
            assert matches, f"FEED cfg_* is not a committed configuration (or went back): {cfg}"
            last_idx = matches[0]
            self.seen.append(cfg)

    async def write(self, a: int, data: int) -> None:
        assert await self.w.drv.write(a, data) == OKAY, f"write {a:#06x}"
        if a != FEED_COMMIT:
            self.regs[a] = data

    async def write_cfg(self, cfg: FeedCfg) -> None:
        writes = cfg.writes()
        self.w.rng.shuffle(writes)
        for a, d in writes:
            await self.write(a, d)

    async def commit(self, resync: bool = False) -> None:
        """FEED_COMMIT (commit, or resync which also commits): the shadow at this moment."""
        self.committed.append(FeedCfg.from_regs(self.regs))
        self.resync_requests += int(resync)
        await self.write(FEED_COMMIT, 2 if resync else 1)

    async def busy(self) -> int:
        data, resp = await self.w.drv.read(FEED_STATUS)
        assert resp == OKAY
        return data & 1

    async def wait_idle(self, limit: int = 200) -> None:
        for _ in range(limit):
            if not await self.busy():
                return
        raise AssertionError("FEED commit_busy never fell")

    async def applied(self) -> FeedCfg:
        await RisingEdge(self.root.net_clk)
        await ReadOnly()
        cfg = read_feed_cfg(self.root)
        await RisingEdge(self.root.host_clk)
        return cfg

    def check_done(self) -> None:
        """Call when FEED_STATUS reads idle: every resync request was taken by a transfer (none
        lost), every accepted transfer landed, and each one that carried a resync pulsed once."""
        assert not self.pending, "a resync request was never carried by a transfer"
        assert self.landed == len(self.expected), f"{len(self.expected)} FEED transfers accepted, {self.landed} landed"
        assert self.resyncs == sum(self.expected), f"{self.resyncs} cfg_resync pulses, {sum(self.expected)} expected"
        assert self.request_edges == self.resync_requests, (
            f"{self.resync_requests} resync writes, {self.request_edges} requests seen")


class Snap:
    """Counter side: stat_* and mpkt_* event logs and the feed_* state at every net edge (from the
    wrapper's inputs), an optional generator of them (unit test only), snapshot handshake monitors
    and the software snapshot protocol. A snapshot taken on acceptance edge E holds the counters of
    every event sampled before E and the feed state sampled on E."""

    def __init__(self, w: Wrap, drive: bool = True):
        self.w, self.dut = w, w.root
        self.events: list[tuple[int, int, int]] = []  # (sampling net edge time, reason, bytes)
        # (sampling net edge time, status, gap, msgs, dup_msgs, oversize)
        self.mold_events: list[tuple[int, int, int, int, int, int]] = []
        self.feed_at: dict[int, tuple[int, int, int]] = {}  # net edge -> (E, synced, eos) it sampled
        self.accepts: list[int] = []  # net edges that take a snapshot (data handshake accepts)
        self.req_valid: list[int] = []  # net edges after which the request is at net (dst_valid)
        self.req_accepts: list[int] = []  # host edges that accept a request
        self.pulses: list[int] = []  # host edges after which STATS_CTRL.snapshot is high
        self.landed: list[int] = []  # host edges after which a snapshot has landed (dst_valid)
        self.p_valid = 0.0  # probability of a stat event per net cycle
        self.drive = drive  # False: stat_* is driven by the receiver or by the test itself
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
            # A packet event with arbitrary fields (mold_stats adds them as given) and a feed state
            # that moves now and then.
            dut.mpkt_valid.value = int(rng.random() < self.p_valid)
            dut.mpkt_status.value = int(rng.choice(list(MoldStatus)))
            dut.mpkt_gap.value = rng.choice([0, MASK64, rng.getrandbits(64)])
            for name in ("mpkt_msgs", "mpkt_dup_msgs", "mpkt_oversize"):
                getattr(dut, name).value = rng.choice([0, 0xFFFF, rng.getrandbits(16)])
            if rng.random() < 0.1:
                dut.feed_expected_seq.value = rng.getrandbits(64)
                dut.feed_synced.value = rng.getrandbits(1)
                dut.feed_eos.value = rng.getrandbits(1)
            await RisingEdge(dut.net_clk)

    async def _net_monitor(self) -> None:
        """Net edges that accept a snapshot (src_valid and src_ready in the cycle before them), net
        edges after which the request is at net (the request handshake's dst_valid), and every stat
        event with the edge that samples it (stat_valid in the cycle before it, not in reset)."""
        dut, cdc, req = self.dut, self.dut.u_snap_cdc, self.dut.u_snap_req_cdc
        edge = None  # starts mid-cycle: the stat event presented now is sampled by the next edge
        while True:
            await ReadOnly()
            if req.dst_valid.value and edge is not None:
                self.req_valid.append(edge)
            self.pending_cycles["net"] += int(dut.snap_net_pending_q.value)
            accept = bool(cdc.src_valid.value and cdc.src_ready.value)
            stat = mpkt = None
            if dut.stat_valid.value and not dut.net_rst.value:
                stat = (int(dut.stat_reason.value), int(dut.stat_bytes.value))
            if dut.mpkt_valid.value and not dut.net_rst.value:
                mpkt = tuple(int(getattr(dut, n).value) for n in
                             ("mpkt_status", "mpkt_gap", "mpkt_msgs", "mpkt_dup_msgs", "mpkt_oversize"))  # fmt: skip
            feed = (int(dut.feed_expected_seq.value), int(dut.feed_synced.value), int(dut.feed_eos.value))
            await RisingEdge(dut.net_clk)
            edge = get_sim_time()
            self.feed_at[edge] = feed
            if accept:
                self.accepts.append(edge)
            if stat:
                self.events.append((edge, *stat))
            if mpkt:
                self.mold_events.append((edge, *mpkt))

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
        """Snapshot values taken on `edge`, in SNAP_NAMES order: the counters of every event sampled
        before it, then the feed state sampled on it."""
        c = [0] * len(COUNTERS)
        for t, reason, nbytes in self.events:
            if t >= edge:
                break
            c[0] += 1
            c[1] += nbytes
            if reason:
                c[1 + reason] += 1
        m = [0] * len(MOLD_COUNTERS)
        n_status = len(MoldStatus)
        for t, status, gap, msgs, dups, over in self.mold_events:
            if t >= edge:
                break
            m[status] += 1
            if MoldStatus(status) in ACCEPTED:
                m[n_status] += msgs
            m[n_status + 1] += dups
            m[n_status + 2] += over
            m[n_status + 3] += int(gap != 0)
            m[n_status + 4] += gap
        e, synced, eos = self.feed_at[edge]
        return [v & MASK64 for v in c + m] + [e, synced | eos << 1]

    async def read_counters(self) -> list[int]:
        """Every snapshot register, in SNAP_NAMES order (64-bit values from LO and HI)."""
        out = []
        for name in SNAP_NAMES:
            if name == "FEED.FEED_STATE":
                data, resp = await self.w.drv.read(addr(name))
                assert resp == OKAY
                out.append(data)
                continue
            lo, r1 = await self.w.drv.read(addr(f"{name}.LO"))
            hi, r2 = await self.w.drv.read(addr(f"{name}.HI"))
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
        diff = {n: (g, x) for n, g, x in zip(SNAP_NAMES, got, want) if g != x}
        assert got == want, f"snapshot {seq}: (read, model) differ: {diff}"
