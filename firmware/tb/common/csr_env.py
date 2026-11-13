"""Test environment of hft_csr_wrap, shared by its unit test (tb/unit/test_hft_csr_wrap.py, the
wrapper is the DUT) and the system test (tb/system/test_rx_csr.py, the wrapper is u_wrap next to
eth_ip_udp_rx): NET and FEED configurations (FEED with ITCH_CTRL) and their commit, the cfg_*
monitors, the resync requests, the snapshot of the rx_stats, mold_stats, itch_stats and order_stats
counters and the feed state with its model, the SYMBOL accesses at the wrapper's sym_* port (audit/10
§2.3, §2.4; audit/11 §2.7; audit/12 §2.7), and the order path blocks (audit/14 S7: RuleCfg, LimCfg, TxCfg,
MboxOrder and the unit test's net-side models in Ord).

`root` is the hft_csr_wrap instance; the bus is driven at the DUT's s_axil_* ports. The monitors
read signals inside the wrapper (grey box): the handshakes' acceptance and landing edges.
"""

import random
from dataclasses import dataclass

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

from common.axil import AxilDriver
from model.csr import OKAY, addr, mem, regs_by_path
from model.itch import ItchClass, Outcome
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
ITCH_CTRL = addr("ITCH.ITCH_CTRL")
SYMBOL = mem("SYMBOL")
# Snapshot values, in register order: rx_stats (RX_PKTS, RX_BYTES, DROP_<reason> by reason code),
# then mold_stats (MOLD_<status> by status code, then the message and gap counters), then
# itch_stats (ITCH_<class> by class code, then one per outcome), then event_packer's ring counters, then the
# feed state (EXPECTED_SEQ, FEED_STATE).
COUNTERS = ["RX_PKTS", "RX_BYTES"] + [f"DROP_{r.name}" for r in list(RxReason)[1:]]
MOLD_COUNTERS = [f"MOLD_{s.name}" for s in MoldStatus]
MOLD_COUNTERS += [f"MOLD_{n}" for n in ("MSGS", "DUP_MSGS", "OVERSIZE_MSGS", "GAP_EVENTS", "GAP_MSGS")]
ITCH_COUNTERS = [f"ITCH_{c.name}" for c in ItchClass] + [f"ITCH_{o.name}" for o in Outcome]
SNAP_NAMES = [f"STATS.{n}" for n in COUNTERS] + [f"FEED.{n}" for n in MOLD_COUNTERS]
SNAP_NAMES += [f"ITCH.{n}" for n in ITCH_COUNTERS]
SNAP_NAMES += [f"RING.RING_{n}" for n in ("COMMIT_ENTRIES", "DROP_PKTS", "DROP_ENTRIES")]
SNAP_NAMES += ["FEED.EXPECTED_SEQ", "FEED.FEED_STATE"]
# order_stats (order_stats.sv index order), then the levels the snapshot takes with it.
REASONS = ("KILL", "TX_OFF", "INVALID", "LOCATE", "QTY", "NOTIONAL", "BUDGET", "RATE", "TX_BUSY", "RING_FULL")
ORD_NAMES = [f"STRATEGY.RULE_FIRED_{n}" for n in range(8)] + ["RISK.ORDERS_SENT_HW", "RISK.ORDERS_SENT_HOST"]
ORD_NAMES += [f"RISK.RISK_REJECT_{r}" for r in REASONS] + ["RING.RING_DROP_ORDERS"]
SNAP_NAMES += ORD_NAMES + ["RING.RING_ORDER_QUEUED", "STRATEGY.RULE_ARMED", "RISK.BUDGET"]
# Snapshot registers of one word (the others are 64-bit LO/HI pairs).
SINGLE = {"FEED.FEED_STATE", "RING.RING_ORDER_QUEUED", "STRATEGY.RULE_ARMED", "RISK.BUDGET"}
N_CNT = len(SNAP_NAMES)
MASK64 = (1 << 64) - 1
# hft_csr_wrap delays mpkt_* and mres_* by this many net cycles before the mold and ITCH counters, so that a
# packet event moves them in the same cycle as event_packer's ring counters: 3 cycles after it (audit/13 S6).
STATS_DELAY = 2
# It delays feed_* by this many net cycles before the snapshot, so that the feed state belongs to the
# same packet events as the counters (audit/11 S8 notes): moldudp64_rx's update 1 cycle before its
# packet event, itch_decoder forwards the event 2 cycles later, the counter banks count it 3 cycles
# after that (audit/12 §2.7, audit/13 S6).
FEED_DELAY = 6
N_CLASS = len(ItchClass)


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
    class_en: int = 0  # ITCH_CTRL, carried by FEED_COMMIT
    class_filter: int = 0

    @staticmethod
    def random(rng: random.Random) -> "FeedCfg":
        return FeedCfg(
            enable=rng.getrandbits(1),
            session_check=rng.getrandbits(1),
            resync_learn=rng.getrandbits(1),
            filter_mask=rng.getrandbits(8),
            session=rng.getrandbits(80),
            resync_seq=rng.getrandbits(64),
            class_en=rng.getrandbits(N_CLASS),
            class_filter=rng.getrandbits(N_CLASS),
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
            class_en=r(ITCH_CTRL) & 0x3F,
            class_filter=r(ITCH_CTRL) >> 8 & 0x3F,
        )

    def writes(self) -> list[tuple[int, int]]:
        """(address, data) of every FEED shadow register, ITCH_CTRL included."""
        ctrl = self.enable | self.session_check << 1 | self.resync_learn << 2 | self.filter_mask << 8
        return [
            (FEED_CTRL, ctrl),
            (SESSION[0], self.session >> 48),
            (SESSION[1], self.session >> 16 & 0xFFFF_FFFF),
            (SESSION[2], (self.session & 0xFFFF) << 16),
            (RESYNC_SEQ_LO, self.resync_seq & 0xFFFF_FFFF),
            (RESYNC_SEQ_HI, self.resync_seq >> 32),
            (ITCH_CTRL, self.class_en | self.class_filter << 8),
        ]


def read_feed_cfg(root) -> FeedCfg:
    return FeedCfg(
        enable=int(root.cfg_feed_en.value),
        session_check=int(root.cfg_session_check.value),
        resync_learn=int(root.cfg_resync_learn.value),
        filter_mask=int(root.cfg_filter_mask.value),
        session=int(root.cfg_session.value),
        resync_seq=int(root.cfg_resync_seq.value),
        class_en=int(root.cfg_class_en.value),
        class_filter=int(root.cfg_class_filter.value),
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

    def commit_nowait(self):
        """NET_COMMIT queued without waiting for its response (the next one may follow at once); returns the
        pending write. The expected configuration is the shadow at this moment."""
        self.committed.append(NetCfg.from_regs(self.regs))
        return self.drv.write_nowait(NET_COMMIT, 1)

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
    Net: before the first transfer cfg_feed_en, cfg_session_check, cfg_resync and cfg_class_en are 0. From the
    cycle it lands, the FEED cfg_* are one committed configuration, whole, in commit order; and
    cfg_resync is 1 exactly in the landing cycle of a transfer that must resync."""

    def __init__(self, w: Wrap):
        self.w, self.root = w, w.root
        self.regs: dict[int, int] = {}  # FEED shadow registers written since reset
        self.committed: list[FeedCfg] = []  # the shadow at every FEED_COMMIT write, in order
        self.resync_requests = 0  # writes with FEED_COMMIT.resync = 1
        self.commit_base = 0  # len(committed) at the last reset: cfg_epoch must reach the rest, mod 256
        self.back_to_back = 0  # FEED_COMMIT writes in the cycle right after another one
        self.epoch: int | None = None  # cfg_epoch in the net cycle before, once defined
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
        prev_write = False  # a FEED_COMMIT write in the cycle before
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
                self.commit_base = len(self.committed)
                prev_write = False
                continue
            resync = bool(commit_bit.resync.value.value)
            if resync or commit_bit.commit.value.value:
                self.pulse_times.append(edge)
            write = bool(commit_bit.commit.swmod.value)
            self.back_to_back += int(write and prev_write)
            prev_write = write
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
                self.epoch = None
                self.landed = self.resyncs = 0
                self.seen.clear()
                continue
            landing = bool(root.u_feed_cdc.dst_valid.value)
            resync = bool(root.cfg_resync.value)
            loaded = loaded or landing
            if not loaded:
                assert not (root.cfg_feed_en.value or root.cfg_session_check.value or resync or root.cfg_class_en.value), (
                    "cfg_feed_en, cfg_session_check, cfg_resync or cfg_class_en set before any FEED commit was applied")
                continue
            epoch = int(root.cfg_epoch.value)
            assert landing or epoch == self.epoch, f"cfg_epoch {epoch} changed outside the landing of a transfer"
            self.epoch = epoch
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
        commits = len(self.committed) - self.commit_base
        if commits:
            assert self.epoch == commits % 256, f"{commits} FEED commit writes, cfg_epoch {self.epoch}"


class Snap:
    """Counter side: stat_*, mpkt_* and mres_* event logs and the feed_* state at every net edge
    (from the wrapper's inputs), an optional generator of them (unit test only), snapshot handshake
    monitors and the software snapshot protocol. A snapshot taken on acceptance edge E holds the
    rx_stats counters of every event sampled before E, the mold and ITCH counters of every event
    sampled more than STATS_DELAY net edges before E, the ring counters sampled on E and the feed state
    sampled FEED_DELAY net edges before E. ITCH counters: a result waits for its packet event, which adds it for OK, HEARTBEAT and EOS
    and drops it otherwise; a result on the edge of a packet event belongs to the next packet. The order counters
    count the ord_* events sampled before E (order_stats, no delay), and ord_queued, st_armed and st_budget are
    the values sampled on E."""

    def __init__(self, w: Wrap, drive: bool = True):
        self.w, self.dut = w, w.root
        self.events: list[tuple[int, int, int]] = []  # (sampling net edge time, reason, bytes)
        # (sampling net edge time, status, gap, msgs, dup_msgs, oversize)
        self.mold_events: list[tuple[int, int, int, int, int, int]] = []
        # (sampling net edge time, (class, outcome) or None, packet status or None)
        self.itch_events: list[tuple[int, tuple[int, int] | None, int | None]] = []
        self.feed_at: dict[int, tuple[int, int, int]] = {}  # net edge -> (E, synced, eos) it sampled
        self.ring_at: dict[int, tuple[int, int, int]] = {}  # net edge -> the cnt_ring_* it sampled
        self.ord_events: list[tuple[int, list[int]]] = []  # (sampling net edge, order counter increments)
        self.levels_at: dict[int, tuple[int, int, int]] = {}  # net edge -> (ord_queued, st_armed, st_budget)
        self.edges: list[int] = []  # every net edge, in order
        self.edge_index: dict[int, int] = {}  # net edge -> its index in edges
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
            # Message results of any class and outcome (itch_stats counts them as given).
            dut.mres_valid.value = int(rng.random() < self.p_valid)
            dut.mres_class.value = int(rng.choice(list(ItchClass)))
            dut.mres_outcome.value = int(rng.choice(list(Outcome)))
            for name in ("cnt_ring_commit_entries", "cnt_ring_drop_pkts", "cnt_ring_drop_entries"):
                getattr(dut, name).value = rng.choice([0, MASK64, rng.getrandbits(64)])
            # Order events of any rule, source and reason, out-of-enum codes included (they count
            # nowhere), and the levels the snapshot takes.
            dut.ord_fire_valid.value = int(rng.random() < self.p_valid)
            dut.ord_fire_rule.value = rng.getrandbits(3)
            dut.ord_sent_valid.value = int(rng.random() < self.p_valid)
            dut.ord_sent_source.value = rng.getrandbits(2)
            dut.ord_rej_valid.value = int(rng.random() < self.p_valid)
            dut.ord_rej_reason.value = rng.getrandbits(4)
            dut.ord_lost_valid.value = int(rng.random() < self.p_valid)
            dut.ord_lost_reason.value = rng.getrandbits(4)
            dut.ord_queued.value = rng.randint(0, 16)
            dut.st_armed.value = rng.getrandbits(8)
            dut.st_budget.value = rng.choice([0, 0xFFFF_FFFF, rng.getrandbits(32)])
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
            stat = mpkt = res = None
            if dut.mres_valid.value and not dut.net_rst.value:
                res = (int(dut.mres_class.value), int(dut.mres_outcome.value))
            if dut.stat_valid.value and not dut.net_rst.value:
                stat = (int(dut.stat_reason.value), int(dut.stat_bytes.value))
            if dut.mpkt_valid.value and not dut.net_rst.value:
                mpkt = tuple(int(getattr(dut, n).value) for n in
                             ("mpkt_status", "mpkt_gap", "mpkt_msgs", "mpkt_dup_msgs", "mpkt_oversize"))  # fmt: skip
            feed = (int(dut.feed_expected_seq.value), int(dut.feed_synced.value), int(dut.feed_eos.value))
            ring = (int(dut.cnt_ring_commit_entries.value), int(dut.cnt_ring_drop_pkts.value),
                    int(dut.cnt_ring_drop_entries.value))  # fmt: skip
            levels = (int(dut.ord_queued.value), int(dut.st_armed.value), int(dut.st_budget.value))
            ord_inc = None if dut.net_rst.value else ord_increments(dut)
            await RisingEdge(dut.net_clk)
            edge = get_sim_time()
            self.feed_at[edge] = feed
            self.ring_at[edge] = ring
            self.levels_at[edge] = levels
            if ord_inc is not None and any(ord_inc):
                self.ord_events.append((edge, ord_inc))
            self.edge_index[edge] = len(self.edges)
            self.edges.append(edge)
            if accept:
                self.accepts.append(edge)
            if stat:
                self.events.append((edge, *stat))
            if mpkt:
                self.mold_events.append((edge, *mpkt))
            if mpkt or res:
                self.itch_events.append((edge, res, mpkt[0] if mpkt else None))

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
        """Snapshot values taken on `edge`, in SNAP_NAMES order (see the class)."""
        at = self.edge_index[edge]
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
            if self.edge_index[t] + STATS_DELAY >= at:
                break
            m[status] += 1
            if MoldStatus(status) in ACCEPTED:
                m[n_status] += msgs
            m[n_status + 1] += dups
            m[n_status + 2] += over
            m[n_status + 3] += int(gap != 0)
            m[n_status + 4] += gap
        n_itch = len(ITCH_COUNTERS)
        it, pend = [0] * n_itch, [0] * n_itch
        for t, res, status in self.itch_events:
            if self.edge_index[t] + STATS_DELAY >= at:
                break
            inc = [0] * n_itch
            if res:
                inc[res[0]] = inc[N_CLASS + res[1]] = 1
            if status is None:
                pend = [a + b for a, b in zip(pend, inc, strict=True)]
                continue
            if MoldStatus(status) in ACCEPTED:
                it = [a + b for a, b in zip(it, pend, strict=True)]
            pend = inc  # this edge's result starts the next packet
        i = at - FEED_DELAY
        assert i >= 0, "snapshot before the feed delay registers were loaded"
        e, synced, eos = self.feed_at[self.edges[i]]
        o = [0] * len(ORD_NAMES)
        for t, inc in self.ord_events:
            if t >= edge:
                break
            o = [a + b for a, b in zip(o, inc, strict=True)]
        base = [v & MASK64 for v in c + m + it] + list(self.ring_at[edge]) + [e, synced | eos << 1]
        return base + [v & MASK64 for v in o] + list(self.levels_at[edge])

    async def read_counters(self) -> list[int]:
        """Every snapshot register, in SNAP_NAMES order (64-bit values from LO and HI)."""
        out = []
        for name in SNAP_NAMES:
            if name in SINGLE:
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


def ord_increments(dut) -> list[int]:
    """The order counter increments of the inputs presented now (order_stats, hand-coded: rule n -> n;
    source RULE 1 / HOST 2 -> ORDERS_SENT_HW / HOST; reason 1..10 -> RISK_REJECT_<reason>, on both reject
    inputs; a lost reject -> RING_DROP_ORDERS). Codes outside the enums count nowhere."""
    inc = [0] * len(ORD_NAMES)
    if dut.ord_fire_valid.value:
        inc[int(dut.ord_fire_rule.value)] += 1
    if dut.ord_sent_valid.value and int(dut.ord_sent_source.value) in (1, 2):
        inc[7 + int(dut.ord_sent_source.value)] += 1
    for valid, reason in ((dut.ord_rej_valid, dut.ord_rej_reason), (dut.ord_lost_valid, dut.ord_lost_reason)):
        if valid.value and 1 <= int(reason.value) <= len(REASONS):
            inc[9 + int(reason.value)] += 1
    if dut.ord_lost_valid.value:
        inc[-1] += 1
    return inc


@dataclass(frozen=True)
class SymCmd:
    wr: int
    addr: int  # word address
    wdata: int  # 0 for a read (the port ignores it)
    wstrb: int

    @staticmethod
    def of(wr: int, addr: int, wdata: int, wstrb: int) -> "SymCmd":
        return SymCmd(wr, addr, wdata, wstrb) if wr else SymCmd(0, addr, 0, 0)


class Sym:
    """SYMBOL side, grey box at the wrapper: the host requests regblock makes (hwif_out.SYMBOL),
    the commands at the sym_* port with the edge that accepts them, the responses, the acks back
    in regblock, and the handshake acceptances in between (for the latency checks).

    drive=True (unit test): a model of itch_decoder's command port answers: sym_req_ready low while
    `hold` is set, else high with probability p_ready; an accepted write changes the model RAM at
    its accepting edge (byte strobes); a response 1 cycle after acceptance, with the word for a
    read (random data for a write). In the system test the real itch_decoder answers."""

    def __init__(self, w: Wrap, drive: bool = True):
        self.w, self.root, self.drive = w, w.root, drive
        self.ram: dict[int, int] = {}  # the model RAM (drive=True), word -> value
        self.hold = False
        self.p_ready = 1.0
        self.host_reqs: list[tuple[int, SymCmd]] = []  # (host edge after which req is high, request)
        self.host_accepts: list[int] = []  # host edges that accept a request (u_sym_req_cdc)
        self.acks: list[tuple[int, bool]] = []  # (host edge after which the ack is high, is_write)
        self.cmd_starts: list[int] = []  # net edges after which a new command is valid
        self.cmds: list[tuple[int, SymCmd]] = []  # (net edge that accepts it, command)
        self.rsps: list[int] = []  # net edges after which sym_rsp_valid is high
        self.rsp_accepts: list[int] = []  # net edges that accept a response (u_sym_rsp_cdc)
        self.waits = 0  # net cycles with a command valid and not ready
        cocotb.start_soon(self._host_monitor())
        cocotb.start_soon(self._net_monitor())
        if drive:
            r = self.root
            r.sym_req_ready.value = 0
            r.sym_rsp_valid.value = 0
            r.sym_rsp_rdata.value = 0
            cocotb.start_soon(self._responder())

    async def _host_monitor(self) -> None:
        r = self.root
        out, inp, cdc = r.hwif_out.SYMBOL, r.hwif_in.SYMBOL, r.u_sym_req_cdc
        accept = False
        while True:
            await RisingEdge(r.host_clk)
            edge = get_sim_time()
            if accept:
                self.host_accepts.append(edge)
            await ReadOnly()
            if r.host_rst.value:
                accept = False
                continue
            if out.req.value:
                biten = int(out.wr_biten.value)
                strb = sum(1 << b for b in range(4) if biten >> (8 * b) & 0xFF)
                is_wr = int(out.req_is_wr.value)
                cmd = SymCmd.of(is_wr, int(out.addr.value) >> 2, int(out.wr_data.value), strb)
                self.host_reqs.append((edge, cmd))
            if inp.rd_ack.value or inp.wr_ack.value:
                assert not (inp.rd_ack.value and inp.wr_ack.value), "rd_ack and wr_ack together"
                self.acks.append((edge, bool(inp.wr_ack.value)))
            accept = bool(cdc.src_valid.value and cdc.src_ready.value)

    async def _net_monitor(self) -> None:
        """Samples each net cycle, then records what the next edge does with it."""
        r = self.root
        cdc = r.u_sym_rsp_cdc
        await RisingEdge(r.net_clk)
        edge = get_sim_time()  # the edge that started this cycle
        waiting: SymCmd | None = None  # a command valid and not taken in the cycle before
        in_reset = False  # rst was high in the cycle before (synchronous reset has acted)
        while True:
            await ReadOnly()
            valid = bool(r.sym_req_valid.value) and not r.net_rst.value
            if r.net_rst.value:
                assert not (in_reset and r.sym_req_valid.value), "a SYMBOL command after a reset edge"
                waiting = None
            in_reset = bool(r.net_rst.value)
            cmd = SymCmd.of(int(r.sym_req_wr.value), int(r.sym_req_addr.value), int(r.sym_req_wdata.value),
                            int(r.sym_req_wstrb.value))  # fmt: skip
            if valid and waiting is None:
                self.cmd_starts.append(edge)
            if waiting is not None:
                assert valid and cmd == waiting, "a waiting SYMBOL command changed or vanished"
            if r.sym_rsp_valid.value and not r.net_rst.value:
                self.rsps.append(edge)
            take = valid and bool(r.sym_req_ready.value)
            accept_rsp = bool(cdc.src_valid.value and cdc.src_ready.value)
            self.waits += int(valid and not take)
            await RisingEdge(r.net_clk)
            edge = get_sim_time()
            if take:
                self.cmds.append((edge, cmd))
            if accept_rsp:
                self.rsp_accepts.append(edge)
            waiting = cmd if valid and not take else None

    async def _responder(self) -> None:
        r, rng = self.root, self.w.rng
        while True:
            ready = not self.hold and not r.net_rst.value and rng.random() < self.p_ready
            r.sym_req_ready.value = int(ready)
            await ReadOnly()
            take = ready and bool(r.sym_req_valid.value)
            cmd = None
            if take:
                cmd = SymCmd.of(int(r.sym_req_wr.value), int(r.sym_req_addr.value), int(r.sym_req_wdata.value),
                                int(r.sym_req_wstrb.value))  # fmt: skip
            await RisingEdge(r.net_clk)
            r.sym_rsp_valid.value = int(take)
            r.sym_rsp_rdata.value = rng.getrandbits(32)
            if cmd is not None:
                old = self.ram.get(cmd.addr, 0)
                if cmd.wr:
                    bits = sum(0xFF << (8 * b) for b in range(4) if cmd.wstrb >> b & 1)
                    self.ram[cmd.addr] = (old & ~bits) | (cmd.wdata & bits)
                else:
                    r.sym_rsp_rdata.value = old

    def check_order(self) -> None:
        """Call when idle: every host request reached the port once, in order and unchanged, got
        one response and one ack of its kind."""
        reqs = [c for _, c in self.host_reqs]
        cmds = [c for _, c in self.cmds]
        assert cmds == reqs, f"{len(reqs)} SYMBOL requests, {len(cmds)} commands (or they differ)"
        assert len(self.rsps) == len(cmds) and len(self.acks) == len(cmds), (
            f"{len(cmds)} commands, {len(self.rsps)} responses, {len(self.acks)} acks")
        assert [k for _, k in self.acks] == [bool(c.wr) for c in cmds], "ack kind differs from the request"


# ---- order path (audit/14 S7; decisions 32-38) ----

LOCATE_ALLOW = mem("LOCATE_ALLOW")
RISK_CTRL = addr("RISK.RISK_CTRL")
TX_CTRL = addr("TX.TX_CTRL")
MBOX_SEND = addr("TX.MBOX_SEND")
MBOX_SPACE = addr("TX.MBOX_SPACE")
MBOX_STATUS = addr("TX.MBOX_STATUS")
TX_STATUS = addr("TX.TX_STATUS")
RULE_STATUS = addr("STRATEGY.RULE_STATUS")
TX_USERREF = addr("TX.TX_USERREF")
# Commands (external registers) and their net-side pulse, as Ord records it.
COMMANDS = {
    "RULE_COMMIT": addr("STRATEGY.RULE_COMMIT"),
    "RULE_ARM": addr("STRATEGY.RULE_ARM"),
    "RULE_DISARM": addr("STRATEGY.RULE_DISARM"),
    "RISK_COMMIT": addr("RISK.RISK_COMMIT"),
    "BUDGET_ADD": addr("RISK.BUDGET_ADD"),
    "TX_COMMIT": addr("TX.TX_COMMIT"),
    "USERREF": addr("TX.TX_USERREF_NEXT"),
}
CMD_TX_COMMIT = 5  # hft_csr_wrap cmd_kind_e (grey box: a TX_COMMIT refused while the path is open)


def wire_word(b: bytes) -> int:
    """Register value of up to 4 bytes in wire order: the first byte in [31:24]."""
    return int.from_bytes(b.ljust(4, b"\0"), "big")


def le_int(b: bytes) -> int:
    """A byte string as an RTL vector with byte i in [8i+7:8i]."""
    return int.from_bytes(b, "little")


@dataclass(frozen=True)
class RuleCfg:
    """One rule as software writes it (RULE_CTRL .. RULE_OFFSET) and as strategy_engine gets it (strat_rule_t)."""

    enable: int = 0
    msg_mask: int = 0
    locate: int = 0
    side_match: int = 0
    price_ge: int = 0
    threshold: int = 0
    min_shares: int = 0
    action_side: int = 0
    qty_min: int = 0
    qty: int = 0
    price_mode: int = 0
    offset: int = 0
    tif: int = 0

    @staticmethod
    def random(rng: random.Random) -> "RuleCfg":
        return RuleCfg(rng.getrandbits(1), rng.getrandbits(3), rng.getrandbits(16), rng.getrandbits(2),
                       rng.getrandbits(1), rng.getrandbits(32), rng.getrandbits(32), rng.getrandbits(2),
                       rng.getrandbits(1), rng.getrandbits(32), rng.getrandbits(1), rng.getrandbits(32),
                       rng.getrandbits(8))  # fmt: skip

    def writes(self) -> list[tuple[int, int]]:
        ctrl = (self.enable | self.msg_mask << 1 | self.side_match << 4 | self.price_ge << 6 | self.action_side << 8
                | self.qty_min << 10 | self.price_mode << 11 | self.tif << 16)  # fmt: skip
        return [(addr("STRATEGY.RULE_CTRL"), ctrl), (addr("STRATEGY.RULE_LOCATE"), self.locate),
                (addr("STRATEGY.RULE_THRESHOLD"), self.threshold), (addr("STRATEGY.RULE_MIN_SHARES"), self.min_shares),
                (addr("STRATEGY.RULE_QTY"), self.qty), (addr("STRATEGY.RULE_OFFSET"), self.offset)]  # fmt: skip

    def packed(self) -> int:
        """strat_rule_t (hft_pkg.sv, first member most significant), hand-written."""
        fields = [(self.enable, 1), (self.msg_mask, 3), (self.locate, 16), (self.side_match, 2), (self.price_ge, 1),
                  (self.threshold, 32), (self.min_shares, 32), (self.action_side, 2), (self.qty_min, 1),
                  (self.qty, 32), (self.price_mode, 1), (self.offset, 32), (self.tif, 8)]  # fmt: skip
        v = 0
        for value, width in fields:
            v = v << width | value
        return v


@dataclass(frozen=True)
class LimCfg:
    max_qty: int = 0
    max_notional: int = 0
    refill: int = 0
    period: int = 0
    burst: int = 0

    @staticmethod
    def random(rng: random.Random) -> "LimCfg":
        return LimCfg(rng.getrandbits(32), rng.getrandbits(64), rng.getrandbits(32), rng.getrandbits(32),
                      rng.getrandbits(32))  # fmt: skip

    def writes(self) -> list[tuple[int, int]]:
        return [(addr("RISK.MAX_QTY"), self.max_qty), (addr("RISK.MAX_NOTIONAL_LO"), self.max_notional & 0xFFFF_FFFF),
                (addr("RISK.MAX_NOTIONAL_HI"), self.max_notional >> 32), (addr("RISK.RATE_REFILL"), self.refill),
                (addr("RISK.RATE_PERIOD"), self.period), (addr("RISK.RATE_BURST"), self.burst)]  # fmt: skip


@dataclass(frozen=True)
class TxCfg:
    template: bytes = bytes(42)
    display: int = 0
    capacity: int = 0
    ise: int = 0
    cross_type: int = 0

    @staticmethod
    def random(rng: random.Random) -> "TxCfg":
        return TxCfg(rng.randbytes(42), rng.getrandbits(8), rng.getrandbits(8), rng.getrandbits(8), rng.getrandbits(8))

    def writes(self) -> list[tuple[int, int]]:
        w = [(addr(f"TX.TX_TEMPLATE[{i}]"), wire_word(self.template[4 * i : 4 * i + 4])) for i in range(10)]
        w.append((addr("TX.TX_TEMPLATE_TAIL"), wire_word(self.template[40:42])))
        ouch = self.display | self.capacity << 8 | self.ise << 16 | self.cross_type << 24
        return [*w, (addr("TX.TX_OUCH"), ouch)]


@dataclass(frozen=True)
class MboxOrder:
    side: int = 0x42
    qty: int = 0
    price: int = 0
    locate: int = 0
    stock: bytes = b"        "
    tif: int = 0x30
    clordid: bytes = b" " * 14

    @staticmethod
    def random(rng: random.Random) -> "MboxOrder":
        return MboxOrder(rng.getrandbits(8), rng.getrandbits(32), rng.getrandbits(32), rng.getrandbits(16),
                         rng.randbytes(8), rng.getrandbits(8), rng.randbytes(14))  # fmt: skip

    def writes(self) -> list[tuple[int, int]]:
        w = [(addr("TX.MBOX_SIDE"), self.side), (addr("TX.MBOX_QTY"), self.qty), (addr("TX.MBOX_PRICE"), self.price),
             (addr("TX.MBOX_LOCATE"), self.locate), (addr("TX.MBOX_STOCK_0"), wire_word(self.stock[:4])),
             (addr("TX.MBOX_STOCK_1"), wire_word(self.stock[4:])), (addr("TX.MBOX_TIF"), self.tif)]  # fmt: skip
        w += [(addr(f"TX.MBOX_CLORDID[{i}]"), wire_word(self.clordid[4 * i : 4 * i + 4])) for i in range(3)]
        return [*w, (addr("TX.MBOX_CLORDID_TAIL"), wire_word(self.clordid[12:]))]

    def at_port(self) -> tuple[int, ...]:
        """m_host_* (side, qty, price, locate, stock, tif, clordid) with byte i of stock and ClOrdID in [8i+7:8i]."""
        return (self.side, self.qty, self.price, self.locate, le_int(self.stock), self.tif, le_int(self.clordid))


@dataclass
class Pulse:
    edge: int  # the net edge that samples it
    name: str  # COMMANDS key, or "TX_REFUSED" (a TX_COMMIT refused while the path is open: no cfg_tx_wr)
    data: tuple
    kill: int  # ctl_kill and ctl_tx_en in its cycle
    tx_en: int


class Ord:
    """The order path of hft_csr_wrap in the unit test (grey box).

    Net-side models with the documented timing of the real blocks: strategy_engine (st_error changes on the
    edge that samples cfg_rule_wr, to a value the test sets in `next_error`), risk_check (ev_userref_ignored
    and st_userref after the edge that samples cmd_userref_wr, ignored when `ignore_userref`; st_budget adds
    a BUDGET_ADD), order_tx_builder (a cfg_tx_wr sampled on edge k is decided on edge k+2: ev_cfg_refused for
    one cycle when `refuse_tx`, else st_tx_cfg_valid set) and the allow bitmap command port (as Sym, with its
    own RAM and `loc_p_ready`). The mailbox reader takes m_host_* with probability `host_p_ready`. Monitors:
    every command pulse with its edge and the controls of its cycle; every host order taken; every external
    ack on the host side."""

    def __init__(self, w: Wrap):
        self.w, self.r = w, w.root
        self.pulses: list[Pulse] = []
        self.taken: list[tuple[int, tuple[int, ...]]] = []  # (net edge that takes it, m_host_* fields)
        self.acks: list[tuple[int, str]] = []  # (host edge after which an external ack is high, register)
        self.loc_ram: dict[int, int] = {}
        self.loc_cmds: list[tuple[int, SymCmd]] = []
        self.loc_p_ready = 1.0
        self.host_p_ready = 0.0
        self.next_error = 0
        self.ignore_userref = False
        self.refuse_tx = False
        self.ctl: list[tuple[int, int, int]] = []  # (time in the cycle, ctl_kill, ctl_tx_en), every net cycle
        r = self.r
        r.st_error.value = 0xFF
        r.st_armed.value = 0
        r.st_userref.value = 1
        r.st_budget.value = 0
        r.st_tx_cfg_valid.value = 0
        r.ev_cfg_refused.value = 0
        r.ev_userref_ignored.value = 0
        r.loc_req_ready.value = 0
        r.loc_rsp_valid.value = 0
        r.loc_rsp_rdata.value = 0
        r.m_host_ready.value = 0
        cocotb.start_soon(self._net())
        cocotb.start_soon(self._host())

    def sample(self) -> list[tuple[str, tuple]]:
        r = self.r
        out = []
        if r.cfg_rule_wr.value:
            out.append(("RULE_COMMIT", (int(r.cfg_rule_idx.value), int(r.cfg_rule.value))))
        if r.cmd_arm.value:
            out.append(("RULE_ARM", (int(r.cmd_arm_mask.value),)))
        if r.cmd_disarm.value:
            out.append(("RULE_DISARM", (int(r.cmd_disarm_mask.value),)))
        if r.cfg_lim_wr.value:
            out.append(("RISK_COMMIT", (int(r.cfg_max_qty.value), int(r.cfg_max_notional.value),
                                        int(r.cfg_rate_refill.value), int(r.cfg_rate_period.value),
                                        int(r.cfg_rate_burst.value))))  # fmt: skip
        if r.cmd_budget_add.value:
            out.append(("BUDGET_ADD", (int(r.cmd_budget_amount.value),)))
        if r.cmd_userref_wr.value:
            out.append(("USERREF", (int(r.cmd_userref_value.value),)))
        if r.cfg_tx_wr.value:
            out.append(("TX_COMMIT", (int(r.cfg_template.value), int(r.cfg_display.value), int(r.cfg_capacity.value),
                                      int(r.cfg_ise.value), int(r.cfg_cross_type.value))))  # fmt: skip
        elif r.pulse_q.value and int(r.cmd_dst.value) >> (len(r.cmd_dst.value) - 4) == CMD_TX_COMMIT:
            out.append(("TX_REFUSED", ()))
        return out

    async def _net(self) -> None:
        r, rng = self.r, self.w.rng
        tx_decide: list[int] = []  # countdowns of cfg_tx_wr decisions
        while True:
            loc_ready = rng.random() < self.loc_p_ready and not r.net_rst.value
            r.loc_req_ready.value = int(loc_ready)
            host_ready = rng.random() < self.host_p_ready
            r.m_host_ready.value = int(host_ready)
            await ReadOnly()
            rst = bool(r.net_rst.value)
            pulses = [] if rst else self.sample()
            kill, tx_en = int(r.ctl_kill.value), int(r.ctl_tx_en.value)
            self.ctl.append((get_sim_time(), kill, tx_en))
            take = None
            if not rst and host_ready and r.m_host_valid.value:
                take = tuple(int(getattr(r, f"m_host_{n}").value)
                             for n in ("side", "qty", "price", "locate", "stock", "tif", "clordid"))  # fmt: skip
            loc = None
            if not rst and loc_ready and r.loc_req_valid.value:
                loc = SymCmd.of(int(r.loc_req_wr.value), int(r.loc_req_addr.value), int(r.loc_req_wdata.value),
                                int(r.loc_req_wstrb.value))  # fmt: skip
            await RisingEdge(r.net_clk)
            edge = get_sim_time()
            # Outputs of the models after this edge (one-cycle events back to 0 first).
            r.ev_cfg_refused.value = 0
            r.ev_userref_ignored.value = 0
            r.loc_rsp_valid.value = 0
            for p in pulses:
                self.pulses.append(Pulse(edge, p[0], p[1], kill, tx_en))
                if p[0] == "RULE_COMMIT":
                    r.st_error.value = self.next_error
                elif p[0] == "USERREF":
                    r.ev_userref_ignored.value = int(self.ignore_userref)
                    if not self.ignore_userref:
                        r.st_userref.value = p[1][0]
                elif p[0] == "BUDGET_ADD":
                    r.st_budget.value = min(int(r.st_budget.value) + p[1][0], 0xFFFF_FFFF)
                elif p[0] == "TX_COMMIT":
                    tx_decide.append(2)
            if tx_decide and tx_decide[0] == 0:  # sampled 2 edges ago: decided on this edge
                tx_decide.pop(0)
                if self.refuse_tx:
                    r.ev_cfg_refused.value = 1
                else:
                    r.st_tx_cfg_valid.value = 1
            tx_decide = [n - 1 for n in tx_decide]
            if take is not None:
                self.taken.append((edge, take))
            if loc is not None:
                self.loc_cmds.append((edge, loc))
                old = self.loc_ram.get(loc.addr, 0)
                r.loc_rsp_valid.value = 1
                r.loc_rsp_rdata.value = rng.getrandbits(32)
                if loc.wr:
                    bits = sum(0xFF << (8 * b) for b in range(4) if loc.wstrb >> b & 1)
                    self.loc_ram[loc.addr] = (old & ~bits) | (loc.wdata & bits)
                else:
                    r.loc_rsp_rdata.value = old
            if rst:
                tx_decide = []
                r.st_tx_cfg_valid.value = 0

    async def _host(self) -> None:
        r = self.r
        regs = regs_by_path()
        ext = [p for p, reg in regs.items() if any(f.external for f in reg.fields)]
        names = {p: p.split(".")[1] for p in ext} | {"LOCATE_ALLOW": "LOCATE_ALLOW"}
        handles = []
        for p in [*ext, "LOCATE_ALLOW"]:
            h = r.ohwif_in if p.split(".")[0] in ("RISK", "STRATEGY", "TX") else r.hwif_in  # hft_order_csr (decision 41)
            for part in p.split("."):
                h = getattr(h, part)
            for sig in ("wr_ack", "rd_ack"):
                if hasattr(h, sig):
                    handles.append((names[p], getattr(h, sig)))
        while True:
            await RisingEdge(r.host_clk)
            edge = get_sim_time()
            await ReadOnly()
            for name, h in handles:
                if h.value and not r.host_rst.value:
                    self.acks.append((edge, name))

    def named(self, name: str) -> list[Pulse]:
        return [p for p in self.pulses if p.name == name]

    async def idle(self, limit: int = 400) -> None:
        """No command anywhere in the channel and the mailbox empty and out of its reset handshake (wr_count 0: the
        fence lets a command through at once), grey box; then two more host edges."""
        r = self.r
        for _ in range(limit):
            await ReadOnly()
            if int(r.mbox_count.value) != 0:
                await RisingEdge(r.host_clk)
                continue
            if int(r.h_state_q.value) == 0 and int(r.n_state_q.value) == 0 and r.u_cmd_cdc.src_ready.value:
                if r.u_rsp_cdc.src_ready.value and not r.nop_ack_q.value:
                    break
            await RisingEdge(r.host_clk)
        else:
            raise AssertionError("the command channel never became idle")
        for _ in range(2):
            await RisingEdge(r.host_clk)
