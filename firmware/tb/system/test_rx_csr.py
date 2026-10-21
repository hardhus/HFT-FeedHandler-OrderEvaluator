"""rx_csr_top: eth_ip_udp_rx configured and counted through the host CSR block (audit/10 S6).

Software's view: write the NET registers, NET_COMMIT, wait for commit_busy = 0, send frames, take a
snapshot and read the counters. The reference for every frame is the F2 golden model (model/net.py)
under the configuration the receiver really used: cfg_rx_en at the SOF beat and the other cfg_* at
the stage A beat (09 §2.3), read from the wrapper's outputs. So commits may land while frames are in
flight. Stat events, payload bytes and metadata are checked frame by frame; every snapshot equals the
counters at its acceptance edge (common/csr_env.Snap, fed by the receiver's stat_*).

Clocks: the RATIO profile is (net period, host period), as in test_hft_csr_wrap.py. One async reset
source (rst_in) goes through one reset_sync per domain.
"""

import random
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cocotb
import pytest
from cocotb.triggers import ReadOnly, RisingEdge

from common.clocks import PROFILES, profile, start_clock
from common.coverage import Coverage
from common.csr_env import COUNTERS, STATS_CTRL, NetCfg, Snap, Wrap, read_cfg
from common.runner import run_cocotb
from model.csr import OKAY
from model.net import RxConfig, RxExpect, RxMeta, RxReason, expect, ip_int, random_config, random_traffic, udp_frame

RUNTIME_PARAMS = ("ratio", "group")
STAGE_A_BEAT = 4  # eth_ip_udp_rx samples cfg_* (except rx_en) with input beat 4 (09 §2.3)
N_RANDOM = 2000  # frames of tc_rx_csr_random (audit/10 S6); do not lower

# Hand-written: DROP_<reason>.LO at 0x0420 + 8 * (reason - 1), and the two totals.
RX_PKTS_LO, RX_BYTES_LO = 0x0410, 0x0418
DROP_LO = {"DISABLED": 0x0420, "FCS": 0x0428, "MAC": 0x0430, "ETHERTYPE": 0x0438, "IP_HDR": 0x0440,
           "FRAG": 0x0448, "PROTO": 0x0450, "FILTER": 0x0458, "LEN": 0x0460}  # fmt: skip
ID, VERSION = 0x0000, 0x0004


def to_net(cfg: RxConfig) -> NetCfg:
    """The NET registers that hold an RxConfig (8 entries; missing ones disabled)."""
    entries = [*cfg.filters, *[("0.0.0.0", 0, False)] * (8 - len(cfg.filters))]
    return NetCfg(
        rx_en=int(cfg.rx_en),
        accept_bcast=int(cfg.accept_bcast),
        mac=int(cfg.local_mac.replace(":", ""), 16),
        ip=tuple(ip_int(ip) for ip, _, _ in entries),
        port=tuple(port for _, port, _ in entries),
        en=tuple(int(en) for _, _, en in entries),
    )


def to_rx(cfg: NetCfg, rx_en: int) -> RxConfig:
    """The model configuration of what the receiver sampled (rx_en from the SOF beat)."""
    return RxConfig(
        rx_en=bool(rx_en),
        local_mac=cfg.mac.to_bytes(6, "big").hex(":"),
        accept_bcast=bool(cfg.accept_bcast),
        filters=tuple(
            (".".join(str(b) for b in ip.to_bytes(4, "big")), port, bool(en))
            for ip, port, en in zip(cfg.ip, cfg.port, cfg.en)
        ),
    )


@dataclass
class Sent:
    name: str
    frame: bytes
    tuser: bool
    rx_en: int | None = None  # cfg_rx_en at the SOF beat
    cfg: NetCfg | None = None  # cfg_* at the stage A beat
    want: RxExpect | None = None


class System:
    """Frame driver and the frame-by-frame checker on net_clk."""

    def __init__(self, dut, w: Wrap):
        self.dut, self.w, self.root = dut, w, dut.u_wrap
        self.queue: deque[Sent] = deque()  # sent, outcome not seen yet
        self.stat_q: deque[Sent] = deque()
        self.pay_q: deque[Sent] = deque()
        self.done: list[Sent] = []
        self.in_flight: Sent | None = None
        self.start_cfg: NetCfg | None = None  # cfg_* at the SOF beat of the frame in flight
        self.beat = 0
        self.cfg_changed_in_frame = 0
        self.pkt = bytearray()
        cocotb.start_soon(self._monitor())

    async def send(self, frame: bytes, tuser: bool = False, gap: int = 0, name: str = "") -> Sent:
        s = Sent(name or f"frame {len(self.done) + len(self.queue)}", frame, tuser)
        self.queue.append(s)
        self.stat_q.append(s)
        d = self.dut
        for _ in range(gap):
            d.s_axis_rx_tvalid.value = 0
            await RisingEdge(d.net_clk)
        beats = [frame[i : i + 8] for i in range(0, len(frame), 8)]
        for k, beat in enumerate(beats):
            last = k == len(beats) - 1
            d.s_axis_rx_tdata.value = int.from_bytes(beat.ljust(8, b"\0"), "little")
            d.s_axis_rx_tkeep.value = (1 << len(beat)) - 1
            d.s_axis_rx_tlast.value = int(last)
            d.s_axis_rx_tuser.value = int(tuser and last)
            d.s_axis_rx_tvalid.value = 1
            await RisingEdge(d.net_clk)
        d.s_axis_rx_tvalid.value = 0
        return s

    async def finish(self, limit: int = 50) -> None:
        for _ in range(limit):
            if not (self.stat_q or self.pay_q or self.in_flight):
                return
            await RisingEdge(self.dut.net_clk)
        raise AssertionError(f"{len(self.stat_q)} frame(s) without a stat event, {len(self.pay_q)} without payload")

    async def _monitor(self) -> None:
        d, r = self.dut, self.root
        while True:
            await RisingEdge(d.net_clk)
            await ReadOnly()
            if r.net_rst.value:
                continue
            if d.s_axis_rx_tvalid.value:  # this beat and the cfg_* now are sampled at the next edge
                if self.in_flight is None:
                    self.in_flight, self.beat = self.queue.popleft(), 0
                    self.in_flight.rx_en = int(r.cfg_rx_en.value)
                    self.start_cfg = read_cfg(r)
                s = self.in_flight
                if self.beat == STAGE_A_BEAT:
                    s.cfg = read_cfg(r)
                    s.want = expect(s.frame, s.tuser, to_rx(s.cfg, s.rx_en))
                    if s.want.payload is not None:
                        self.pay_q.append(s)
                if d.s_axis_rx_tlast.value and s.want is None:
                    # Ends before stage A: shorter than the Ethernet + IPv4 headers, so only rx_en
                    # (at SOF) and tuser decide (DISABLED, FCS or LEN); the other cfg_* are unused.
                    assert len(s.frame) < 42, f"{s.name}: {len(s.frame)} B frame without a stage A beat"
                    s.cfg = self.start_cfg
                    s.want = expect(s.frame, s.tuser, to_rx(s.cfg, s.rx_en))
                    assert s.want.payload is None
                if d.s_axis_rx_tlast.value:
                    if read_cfg(r) != self.start_cfg:
                        self.cfg_changed_in_frame += 1
                    self.in_flight = None
                self.beat += 1
            if r.stat_valid.value:
                self._stat()
            if d.m_axis_udp_tvalid.value:
                self._payload_beat()

    def _stat(self) -> None:
        assert self.stat_q, "stat event with no frame outstanding"
        s = self.stat_q.popleft()
        assert s.want is not None, f"{s.name}: stat event before its stage A beat"
        got = (int(self.root.stat_reason.value), int(self.root.stat_bytes.value))
        assert got == (s.want.reason, s.want.nbytes), f"{s.name}: stat {got}, model ({s.want.reason.name}, {s.want.nbytes})"
        self.done.append(s)

    def _payload_beat(self) -> None:
        d = self.dut
        assert self.pay_q, "payload beat with no packet expected"
        s = self.pay_q[0]
        keep = int(d.m_axis_udp_tkeep.value)
        self.pkt += int(d.m_axis_udp_tdata.value).to_bytes(8, "little")[: keep.bit_count()]
        if d.m_axis_udp_tlast.value:
            self.pay_q.popleft()
            assert bytes(self.pkt) == s.want.payload, f"{s.name}: payload differs from the model"
            assert bool(d.m_axis_udp_tuser.value) == s.want.aborted, f"{s.name}: abort flag"
            meta = RxMeta(*(int(h.value) for h in (d.m_udp_src_ip, d.m_udp_dst_ip, d.m_udp_dst_port,
                                                   d.m_udp_len, d.m_udp_filter_idx)))  # fmt: skip
            assert meta == s.want.meta, f"{s.name}: metadata {meta}, model {s.want.meta}"
            self.pkt.clear()


async def start(dut) -> tuple[Wrap, System]:
    """Idle inputs, the reset source asserted, both clocks; release the reset and wait until both
    synchronized resets are low."""
    net_ps, host_ps, jit = profile()
    for name in ("awvalid", "wvalid", "bready", "arvalid", "rready"):
        getattr(dut, f"s_axil_{name}").value = 0
    for name, v in (("tvalid", 0), ("tdata", 0), ("tkeep", 0), ("tlast", 0), ("tuser", 0)):
        getattr(dut, f"s_axis_rx_{name}").value = v
    dut.ts_now.value = 0
    dut.rst_in.value = 1
    start_clock(dut.net_clk, net_ps, jit)
    start_clock(dut.host_clk, host_ps, jit)
    for _ in range(6):
        await RisingEdge(dut.host_clk)
        await RisingEdge(dut.net_clk)
    dut.rst_in.value = 0
    for _ in range(100):
        await RisingEdge(dut.host_clk)
        if not (dut.u_wrap.host_rst.value or dut.u_wrap.net_rst.value):
            break
    else:
        raise AssertionError("the synchronized resets never fell")
    await RisingEdge(dut.host_clk)
    w = Wrap(dut, dut.u_wrap)
    w.snap = Snap(w, drive=False)
    return w, System(dut, w)


async def configure(w: Wrap, cfg: RxConfig) -> None:
    """Software: write the NET registers, commit, wait until applied."""
    await w.write_cfg(to_net(cfg))
    await w.commit()
    await w.wait_idle()


async def snapshot_checked(w: Wrap) -> list[int]:
    """Software protocol (read STATS_SEQ, request, wait, read), checked against the model."""
    seq, got = await w.snap.snapshot()
    await w.snap.check(seq, got)
    return got


# ---------------------------------------------------------------------------------------------


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_bring_up(dut):
    """02 §3.4 scenario 1, F5 part: identify the device, frames before any commit are dropped
    (DISABLED), configure one filter and commit, then accepted frames, wrong-port frames and FCS
    errors; the counters read through a snapshot are the hand-counted values."""
    w, sysm = await start(dut)
    assert await w.drv.read(ID) == (0x48465431, OKAY)
    assert await w.drv.read(VERSION) == (0x0005_0001, OKAY)
    frames = []
    for i in range(5):  # before any commit: receiver off
        frames.append((await sysm.send(udp_frame(bytes(range(20 + i))), gap=2)).frame)
    await configure(w, RxConfig(True, "02:00:00:00:00:01", False, (("10.0.0.2", 5000, True),)))
    for i in range(10):
        frames.append((await sysm.send(udp_frame(random.randbytes(18 + 7 * i)), gap=1)).frame)
    for _ in range(3):
        frames.append((await sysm.send(udp_frame(random.randbytes(30), dport=5001), gap=1)).frame)
    for _ in range(2):
        frames.append((await sysm.send(udp_frame(random.randbytes(30)), tuser=True, gap=1)).frame)
    await sysm.finish()
    got = dict(zip(COUNTERS, await snapshot_checked(w)))
    assert got["RX_PKTS"] == 20 and got["RX_BYTES"] == sum(len(f) for f in frames)
    assert (got["DROP_DISABLED"], got["DROP_FILTER"], got["DROP_FCS"]) == (5, 3, 2)
    assert sum(got[f"DROP_{n}"] for n in DROP_LO) == 10
    assert [s.want.reason for s in sysm.done].count(RxReason.OK) == 10


@cocotb.test(timeout_time=10, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_reason_counter_map(dut):
    """One frame for every reason code: exactly the counter at the hand-written address
    0x0420 + 8 * (reason - 1) moves by one (OK moves none), RX_PKTS by one, RX_BYTES by the frame
    length."""
    w, sysm = await start(dut)
    rng = random.Random(random.getrandbits(32))
    cfg = random_config(rng)
    by_reason = {}
    for t in random_traffic(rng, 400, cfg):
        by_reason.setdefault(t.intent, t)
    assert set(by_reason) == set(RxReason)

    async def read_all() -> dict[int, int]:
        addrs = [RX_PKTS_LO, RX_BYTES_LO, *DROP_LO.values()]
        await snapshot_checked(w)
        return {a: (await w.drv.read(a))[0] for a in addrs}

    for reason, t in by_reason.items():
        await configure(w, RxConfig(t.rx_en, cfg.local_mac, cfg.accept_bcast, cfg.filters))
        before = await read_all()
        s = await sysm.send(t.frame, t.tuser, gap=2, name=reason.name)
        await sysm.finish()
        assert s.want.reason == reason
        after = await read_all()
        moved = {a for a in after if after[a] != before[a]}
        want = {RX_PKTS_LO, RX_BYTES_LO} | ({DROP_LO[reason.name]} if reason != RxReason.OK else set())
        assert moved == want, f"{reason.name}: moved {sorted(hex(a) for a in moved)}"
        assert after[RX_PKTS_LO] - before[RX_PKTS_LO] == 1
        assert after[RX_BYTES_LO] - before[RX_BYTES_LO] == min(len(t.frame), 0xFFFF)


RANDOM_BINS = [
    "commit_while_busy", "snapshot_while_busy", "snapshot_on_stat_edge", "cfg_change_in_frame",
    *(f"reason_{r.name}" for r in RxReason),
]  # fmt: skip


@cocotb.test(timeout_time=2000, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_rx_csr_random(dut):
    """N_RANDOM frames from the F2 traffic generator (about half rejected) while software commits
    new random configurations (sometimes twice in a row) and takes snapshots (sometimes in bursts).
    Every frame is checked against the model under the configuration it was sampled with; every
    snapshot read is exact; coverage gate of audit/10 §2.12 (CDC bins)."""
    w, sysm = await start(dut)
    rng = random.Random(random.getrandbits(32))
    cov = Coverage("rx_csr.tc_rx_csr_random", RANDOM_BINS)
    r = dut.u_wrap
    configs = [random_config(rng) for _ in range(6)]
    current = {"traffic": deque()}

    async def host_monitor():
        """Commit/snapshot requests that arrive while their path is busy (the coalescing bins)."""
        commit, snap = r.hwif_out.NET.NET_COMMIT.commit.value, r.hwif_out.STATS.STATS_CTRL.snapshot.value
        while True:
            await RisingEdge(r.host_clk)
            await ReadOnly()
            if commit.value and (r.commit_pending_q.value or not r.cfg_src_ready.value):
                cov.hit("commit_while_busy")
            if snap.value and (r.snap_pending_q.value or not r.snap_req_ready.value):
                cov.hit("snapshot_while_busy")

    async def software():
        n_commit = 0
        while len(sysm.done) < N_RANDOM:
            for _ in range(rng.randint(100, 800)):
                await RisingEdge(r.host_clk)
            if rng.random() < 0.5:
                cfg = rng.choice(configs)
                cfg = RxConfig(rng.random() < 0.85, cfg.local_mac, cfg.accept_bcast, cfg.filters)
                await w.write_cfg(to_net(cfg))
                await w.commit()
                if rng.random() < 0.3:
                    await w.commit()  # again at once: the first is still in flight
                current["traffic"] = deque(random_traffic(rng, 200, cfg))
                n_commit += 1
            else:
                for _ in range(rng.randint(1, 3)):
                    assert await w.drv.write(STATS_CTRL, 1) == OKAY
                await w.snap.quiet()
                seq = await w.snap.seq()
                await w.snap.check(seq, await w.snap.read_counters())
        assert n_commit > 5

    await configure(w, configs[0])
    current["traffic"] = deque(random_traffic(rng, 200, configs[0]))
    cocotb.start_soon(host_monitor())
    task = cocotb.start_soon(software())
    sent = 0
    while sent < N_RANDOM:
        if not current["traffic"]:
            current["traffic"] = deque(random_traffic(rng, 200, rng.choice(configs)))
        t = current["traffic"].popleft()
        await sysm.send(t.frame, t.tuser, gap=rng.choice([0, 0, 1, 3]))
        sent += 1
    await sysm.finish()
    await task
    await w.snap.quiet()
    final = await snapshot_checked(w)
    assert final[0] == N_RANDOM
    for s in sysm.done:
        cov.hit(f"reason_{s.want.reason.name}")
    if sysm.cfg_changed_in_frame:
        cov.hit("cfg_change_in_frame")
    events = {t for t, _, _ in w.snap.events}
    if any(a in events for a in w.snap.accepts):
        cov.hit("snapshot_on_stat_edge")
    dut._log.info(f"{len(sysm.done)} frames, {len(w.committed)} commits, {len(w.snap.accepts)} snapshots, "
                  f"{sysm.cfg_changed_in_frame} frames with a cfg change in flight")  # fmt: skip
    cov.check(Path.cwd())  # the run directory


# The long test runs as its own pytest entries.
LONG_TESTS = ("tc_rx_csr_random",)


def directed_tests() -> list[str]:
    return [name for name in globals() if name.startswith("tc_") and name not in LONG_TESTS]


# Clock profiles: (net period, host period); jitter = the CDC jitter model in the build.
@pytest.mark.parametrize(
    "group, jit, ratio",
    [("directed", 0, "net2host"), ("directed", 0, "host2net"),
     ("tc_rx_csr_random", 0, "net2host"), ("tc_rx_csr_random", 1, "jitter")],
)  # fmt: skip
def test_rx_csr(group, jit, ratio):
    assert ratio in PROFILES
    run_cocotb(
        toplevel="rx_csr_top",
        filelists=["common.f", "core.f", "csr.f", "system.f"],
        test_module="test_rx_csr",
        defines={"HFT_SIM_CDC_JITTER": 1} if jit else {},
        testcase=directed_tests() if group == "directed" else group,
        extra_env={"GROUP": group, "JITTER": jit, "RATIO": ratio},
    )
