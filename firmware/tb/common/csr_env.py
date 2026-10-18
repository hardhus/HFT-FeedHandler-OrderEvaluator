"""Test environment of hft_csr_wrap, shared by its unit test (tb/unit/test_hft_csr_wrap.py, the
wrapper is the DUT) and the system test (tb/system/test_rx_csr.py, the wrapper is u_wrap next to
eth_ip_udp_rx): NET configurations and their commit, the cfg_* monitor, and the counter snapshot
with its model (audit/10 §2.3, §2.4).

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
# Counter registers in rx_stats order (RX_PKTS, RX_BYTES, DROP_<reason> by reason code).
COUNTERS = ["RX_PKTS", "RX_BYTES"] + [f"DROP_{r.name}" for r in list(RxReason)[1:]]
N_CNT = len(COUNTERS)
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


class Snap:
    """Counter side: stat_* event log (from the wrapper's stat_* inputs), an optional stat_*
    generator (unit test only), snapshot handshake monitors and the software snapshot protocol."""

    def __init__(self, w: Wrap, drive: bool = True):
        self.w, self.dut = w, w.root
        self.events: list[tuple[int, int, int]] = []  # (sampling net edge time, reason, bytes)
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
            stat = None
            if dut.stat_valid.value and not dut.net_rst.value:
                stat = (int(dut.stat_reason.value), int(dut.stat_bytes.value))
            await RisingEdge(dut.net_clk)
            edge = get_sim_time()
            if accept:
                self.accepts.append(edge)
            if stat:
                self.events.append((edge, *stat))

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
