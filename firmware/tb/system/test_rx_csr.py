"""rx_csr_top: eth_ip_udp_rx and moldudp64_rx configured and counted through the host CSR block
(audit/10 S6, audit/11 S8).

Software's view: write the NET and FEED registers, commit, wait for commit_busy = 0, send frames,
take a snapshot and read the counters. The reference for every frame is the F2 golden model
(model/net.py) under the configuration the receiver really used: cfg_rx_en at the SOF beat and the
other cfg_* at the stage A beat (09 §2.3), read from the wrapper's outputs. So commits may land
while frames are in flight. Stat events, payload bytes and metadata are checked frame by frame.

Every payload packet then goes on to moldudp64_rx, checked as a chain: the F2 model's payload,
abort flag and filter index go into the MoldUDP64 model (model/mold.py) under the FEED cfg_* of the
packet's first beat and the feed state carried from packet to packet, with the resync pulses
applied as the RTL applies them (11 S4 notes: on a first beat or between packets at once, later in
a packet after it, the last one if several wait). Every message (seq, length, bytes, zero bytes
after the length, the packet's SOF timestamp), every packet event (fields and its latency, 3
cycles after tlast) and feed_expected_seq/synced/eos in every cycle are compared with the model.

Every snapshot equals the counters at its acceptance edge (common/csr_env.Snap, fed by the
receiver's stat_* and moldudp64_rx's packet events and feed state). Between two snapshots in sync
with no resync in between, the accounting identity of the MoldUDP64 counters holds:
EXPECTED_SEQ moves by MOLD_MSGS + MOLD_OVERSIZE_MSGS + MOLD_GAP_MSGS (11 S1 notes, rule 3).

Clocks: the RATIO profile is (net period, host period), as in test_hft_csr_wrap.py. One async reset
source (rst_in) goes through one reset_sync per domain.
"""

import random
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path

import cocotb
import pytest
from cocotb.triggers import ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

from common.clocks import PROFILES, profile, start_clock
from common.coverage import Coverage
from common.csr_env import (
    COUNTERS,
    FEED_DELAY,
    MASK64,
    SNAP_NAMES,
    STATS_CTRL,
    FeedCfg,
    NetCfg,
    Snap,
    Wrap,
    read_cfg,
    read_feed_cfg,
)
from common.runner import run_cocotb
from model import mold
from model.csr import OKAY
from model.mold import ACCEPTED, HDR_LEN, MAX_MSG, SEQ_MOD, FeedConfig, FeedState, MoldResult, MoldStatus
from model.net import (
    RxConfig,
    RxExpect,
    RxMeta,
    RxReason,
    expect,
    ip_int,
    mcast_mac,
    pad_to,
    random_config,
    random_traffic,
    udp_frame,
)

RUNTIME_PARAMS = ("ratio", "group")
STAGE_A_BEAT = 4  # eth_ip_udp_rx samples cfg_* (except rx_en) with input beat 4 (09 §2.3)
N_RANDOM = 2000  # frames of tc_rx_csr_random (audit/10 S6); do not lower
N_FEED = 2000  # frames of tc_feed_random (audit/11 §2.9); do not lower
PKT_LATENCY = 3  # moldudp64_rx: input tlast -> m_pkt_valid (11 §2.5)

# Hand-written: DROP_<reason>.LO at 0x0420 + 8 * (reason - 1), and the two totals.
RX_PKTS_LO, RX_BYTES_LO = 0x0410, 0x0418
DROP_LO = {"DISABLED": 0x0420, "FCS": 0x0428, "MAC": 0x0430, "ETHERTYPE": 0x0438, "IP_HDR": 0x0440,
           "FRAG": 0x0448, "PROTO": 0x0450, "FILTER": 0x0458, "LEN": 0x0460}  # fmt: skip
ID, VERSION = 0x0000, 0x0004

# The feed tests' network: entry 0 = line A (unicast), entry 1 = line B (multicast), both in the feed
# mask; entries 2..7 = other feeds.
LOCAL_MAC = "02:00:00:00:00:01"
FEED_LINES = [("10.0.0.2", 5000), ("233.54.12.1", 5001), *((f"10.0.1.{k}", 6000 + k) for k in range(2, 8))]
NET_FEED = RxConfig(True, LOCAL_MAC, False, tuple((ip, port, True) for ip, port in FEED_LINES))
SESSION = b"SESSION001"
FEED_ON = FeedCfg(enable=1, session_check=1, filter_mask=0x03, session=int.from_bytes(SESSION, "big"))
S = MoldStatus


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


def to_mold(cfg: FeedCfg) -> FeedConfig:
    """The model configuration of the FEED cfg_* moldudp64_rx sampled."""
    return FeedConfig(bool(cfg.enable), bool(cfg.session_check), cfg.session.to_bytes(10, "big"), cfg.filter_mask)


def feed_frame(payload: bytes, idx: int, dport: int | None = None) -> bytes:
    """A frame carrying a MoldUDP64 payload on filter entry idx of NET_FEED (padded to 60 B)."""
    ip, port = FEED_LINES[idx]
    mac = mcast_mac(ip) if ip_int(ip) >> 28 == 0xE else LOCAL_MAC
    return pad_to(udp_frame(payload, dst_mac=mac, dst_ip=ip, dport=port if dport is None else dport))


@dataclass
class Sent:
    name: str
    frame: bytes
    tuser: bool
    rx_en: int | None = None  # cfg_rx_en at the SOF beat
    cfg: NetCfg | None = None  # cfg_* at the stage A beat
    want: RxExpect | None = None


@dataclass
class MoldPkt:
    """One packet into moldudp64_rx: the model's outcome and what the monitor saw."""

    sent: Sent
    ts: int  # m_udp_sof_ts of the packet
    cfg: FeedConfig  # FEED cfg_* at the first beat
    before: FeedState
    want: MoldResult
    msgs: deque = field(default_factory=deque)  # messages still expected
    tlast: int = -1  # cycle of the input tlast
    resync_waited: bool = False


class System:
    """Frame driver and the frame-by-frame checker on net_clk (F2, then moldudp64_rx)."""

    def __init__(self, dut, w: Wrap):
        self.dut, self.w, self.root, self.mold = dut, w, dut.u_wrap, dut.u_mold
        self.queue: deque[Sent] = deque()  # sent, outcome not seen yet
        self.stat_q: deque[Sent] = deque()
        self.pay_q: deque[Sent] = deque()
        self.done: list[Sent] = []
        self.in_flight: Sent | None = None
        self.start_cfg: NetCfg | None = None  # cfg_* at the SOF beat of the frame in flight
        self.beat = 0
        self.cfg_changed_in_frame = 0
        self.pkt = bytearray()
        # moldudp64_rx chain
        self.cycle = 0
        self.mold_q: deque[Sent] = deque()  # payload packets not yet at moldudp64_rx
        self.cur: MoldPkt | None = None  # packet at moldudp64_rx, first beat seen, tlast not
        self.cur_cfg: FeedCfg | None = None  # its FEED cfg_* at the first beat
        self.state = FeedState()  # model feed state after everything applied so far
        self.waiting: tuple[bool, int] | None = None  # resync waiting for the packet in flight
        self.timeline: list[tuple[int, FeedState]] = []  # (cycle it becomes visible, state)
        self.exp = FeedState()  # model state now visible on feed_*
        self.out_q: deque[MoldPkt] = deque()  # packets whose messages or event are outstanding
        self.mold_done: list[MoldPkt] = []
        self.accepted_msgs: list[tuple[int, bytes]] = []  # messages of accepted packets, in order
        self.resyncs: list[tuple[int, bool, str]] = []  # (net edge, learn, where: first/waited/between)
        self.resync_applied: list[int] = []  # net edge of the cycle in which each resync is applied
        self.feed_cfg_changed_in_pkt = 0
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
            if not (self.stat_q or self.pay_q or self.in_flight or self.mold_q or self.cur or self.out_q):
                return
            await RisingEdge(self.dut.net_clk)
        raise AssertionError(
            f"{len(self.stat_q)} frame(s) without a stat event, {len(self.pay_q)} without payload, "
            f"{len(self.mold_q) + len(self.out_q) + int(self.cur is not None)} without a packet event")

    async def _monitor(self) -> None:
        d, r = self.dut, self.root
        while True:
            await RisingEdge(d.net_clk)
            await ReadOnly()
            self.cycle += 1
            assert not r.net_rst.value, "net reset after start (the chain model does not follow resets)"
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
                        self.mold_q.append(s)
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
            if d.m_msg_valid.value:
                self._mold_msg()
            if d.m_pkt_valid.value:
                self._mold_pkt()
            self._mold_feed()
            self._mold_input()  # after the output checks: its timeline entries are for later cycles
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

    # ---- moldudp64_rx: the chain model
    def _resync(self, where: str) -> tuple[bool, int]:
        learn, seq = bool(self.root.cfg_resync_learn.value), int(self.root.cfg_resync_seq.value)
        self.resyncs.append((get_sim_time(), learn, where))
        return learn, seq

    def _mold_input(self) -> None:
        """The beat on moldudp64_rx's input in this cycle (sampled at the next edge) and the resync
        pulse with it: a first beat takes the FEED cfg_* and the pulse before the packet; a pulse in
        a later beat of the packet (gaps and tlast included) waits for the packet's update."""
        d, r = self.dut, self.root
        resync = bool(r.cfg_resync.value)
        if d.m_axis_udp_tvalid.value and self.cur is None:
            assert self.mold_q, "packet into moldudp64_rx that F2 did not deliver"
            s = self.mold_q.popleft()
            if resync:
                self.state = mold.resync(self.state, *self._resync("first"))
                self.resync_applied.append(get_sim_time())
                self.timeline.append((self.cycle + 1, self.state))
            self.cur_cfg = read_feed_cfg(r)
            cfg = to_mold(self.cur_cfg)
            want, self._after = mold.expect(s.want.payload, s.want.aborted, s.want.meta.filter_idx, cfg, self.state)
            self.cur = MoldPkt(s, int(d.m_udp_sof_ts.value), cfg, self.state, want, deque(want.msgs))
            self.out_q.append(self.cur)
        elif self.cur is not None:
            if resync:
                self.waiting = self._resync("waited")
                self.cur.resync_waited = True
        elif resync:
            self.state = mold.resync(self.state, *self._resync("between"))
            self.resync_applied.append(get_sim_time())
            self.timeline.append((self.cycle + 1, self.state))
        if self.cur is not None and d.m_axis_udp_tvalid.value and d.m_axis_udp_tlast.value:
            after = self._after
            if self.waiting:  # the last waiting request, after the packet's own update
                after = mold.resync(after, *self.waiting)
                self.resync_applied.append(get_sim_time())
                self.waiting = None
            if read_feed_cfg(r) != self.cur_cfg:
                self.feed_cfg_changed_in_pkt += 1
            self.state = after
            self.timeline.append((self.cycle + 2, after))
            self.cur.tlast = self.cycle
            self.cur = None

    def _mold_feed(self) -> None:
        """feed_* against the model state visible in this cycle."""
        while self.timeline and self.timeline[0][0] <= self.cycle:
            self.exp = self.timeline.pop(0)[1]
        m = self.mold
        got = FeedState(int(m.feed_expected_seq.value), bool(m.feed_synced.value), bool(m.feed_eos.value))
        assert got == self.exp, f"cycle {self.cycle}: feed state {got}, model {self.exp}"

    def _mold_msg(self) -> None:
        d = self.dut
        assert self.out_q, "message with no packet outstanding"
        p = self.out_q[0]
        name = p.sent.name
        assert p.msgs, f"{name}: message after the last expected one (seq {int(d.m_msg_seq.value)})"
        seq, data = p.msgs.popleft()
        got_seq, n = int(d.m_msg_seq.value), int(d.m_msg_len.value)
        vec = int(d.m_msg_data.value).to_bytes(MAX_MSG, "little")
        assert got_seq == seq, f"{name}: message seq {got_seq}, model {seq}"
        assert n == len(data) and vec[:n] == data, f"{name}: seq {seq}: {vec[:n].hex()}, model {data.hex()}"
        assert not any(vec[n:]), f"{name}: seq {seq}: bytes after the length are not 0"
        assert int(d.m_msg_sof_ts.value) == p.ts, f"{name}: seq {seq}: m_msg_sof_ts is not its packet's"

    def _mold_pkt(self) -> None:
        d = self.dut
        assert self.out_q, "packet event with no packet outstanding"
        p = self.out_q.popleft()
        name, w = p.sent.name, p.want
        assert not p.msgs, f"{name}: packet event before {len(p.msgs)} message(s)"
        status = int(d.m_pkt_status.value)
        assert status == w.status, f"{name}: status {status}, model {w.status.name}"
        got = (int(d.m_pkt_gap.value), int(d.m_pkt_msgs.value), int(d.m_pkt_dup_msgs.value),
               int(d.m_pkt_oversize.value), bool(d.m_pkt_synced_now.value))  # fmt: skip
        want = (w.gap, len(w.msgs), w.dup_msgs, w.oversize, w.synced_now)
        assert got == want, f"{name}: (gap, msgs, dup, oversize, synced_now) {got}, model {want}"
        payload = p.sent.want.payload
        if len(payload) >= HDR_LEN:
            hdr = (int.from_bytes(payload[10:18], "big"), int.from_bytes(payload[18:20], "big"))
            assert (int(d.m_pkt_seq.value), int(d.m_pkt_count.value)) == hdr, f"{name}: header seq/count"
        assert self.cycle - p.tlast == PKT_LATENCY, f"{name}: packet event {self.cycle - p.tlast} cycles after tlast"
        if w.status in ACCEPTED:
            self.accepted_msgs += w.msgs
        self.mold_done.append(p)


async def start(dut) -> tuple[Wrap, System]:
    """Idle inputs, the reset source asserted, both clocks; release the reset and wait until both
    synchronized resets are low. ts_now counts net_clk cycles."""
    net_ps, host_ps, jit = profile()
    for name in ("awvalid", "wvalid", "bready", "arvalid", "rready"):
        getattr(dut, f"s_axil_{name}").value = 0
    for name, v in (("tvalid", 0), ("tdata", 0), ("tkeep", 0), ("tlast", 0), ("tuser", 0)):
        getattr(dut, f"s_axis_rx_{name}").value = v
    dut.ts_now.value = 0
    dut.rst_in.value = 1
    start_clock(dut.net_clk, net_ps, jit)
    start_clock(dut.host_clk, host_ps, jit)

    async def timestamp():
        t = random.getrandbits(64)
        while True:
            await RisingEdge(dut.net_clk)
            t = (t + 1) & MASK64
            dut.ts_now.value = t

    cocotb.start_soon(timestamp())
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


async def configure_feed(w: Wrap, cfg: FeedCfg, resync: bool = False) -> None:
    """Software: write the FEED registers, FEED_COMMIT (commit or resync), wait until applied."""
    await w.feed.write_cfg(cfg)
    await w.feed.commit(resync)
    await w.feed.wait_idle()


async def snapshot_checked(w: Wrap) -> list[int]:
    """Software protocol (read STATS_SEQ, request, wait, read), checked against the model."""
    seq, got = await w.snap.snapshot()
    await w.snap.check(seq, got)
    return got


def named(values: list[int]) -> dict[str, int]:
    """Snapshot values by register name (STATS.<n> as <n>, FEED.<n> as <n>)."""
    return {n.split(".", 1)[1]: v for n, v in zip(SNAP_NAMES, values)}


def accounted(v: dict[str, int]) -> int:
    """MOLD_MSGS + MOLD_OVERSIZE_MSGS + MOLD_GAP_MSGS (mod 2^64)."""
    return (v["MOLD_MSGS"] + v["MOLD_OVERSIZE_MSGS"] + v["MOLD_GAP_MSGS"]) & MASK64


# ---------------------------------------------------------------------------------------------


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_bring_up(dut):
    """02 §3.4 scenario 1, F5 part: identify the device, frames before any commit are dropped
    (DISABLED), configure one filter and commit, then accepted frames, wrong-port frames and FCS
    errors; the counters read through a snapshot are the hand-counted values. The feed is never
    committed, so every payload packet reaches moldudp64_rx as MOLD_DISABLED."""
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
    values = await snapshot_checked(w)
    got = dict(zip(COUNTERS, values))
    assert got["RX_PKTS"] == 20 and got["RX_BYTES"] == sum(len(f) for f in frames)
    assert (got["DROP_DISABLED"], got["DROP_FILTER"], got["DROP_FCS"]) == (5, 3, 2)
    assert sum(got[f"DROP_{n}"] for n in DROP_LO) == 10
    assert [s.want.reason for s in sysm.done].count(RxReason.OK) == 10
    v = named(values)
    assert v["MOLD_DISABLED"] == 12, "10 accepted and 2 FCS frames reach moldudp64_rx"
    assert sum(v[f"MOLD_{s.name}"] for s in S) == 12 and accounted(v) == 0 and v["MOLD_DUP_MSGS"] == 0
    assert (v["EXPECTED_SEQ"], v["FEED_STATE"]) == (0, 0)


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


@cocotb.test(timeout_time=10, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_feed_bring_up(dut):
    """02 §3.4 scenario 1, F3 part. NET with line A (unicast) and line B (multicast), one packet
    before the FEED commit (DISABLED), then FEED (session check, mask A|B) and a hand-written
    MoldUDP64 stream: sync, the A/B copy, in order on B, heartbeat, a gap of 5, a partial overlap,
    another session, an oversize message, an FCS error, another feed's entry, end of session. The
    messages of the accepted packets and every FEED snapshot value are hand-counted. Then a resync
    to 5000 (set) and a learn resync, each followed by a packet."""
    w, sysm = await start(dut)
    await configure(w, NET_FEED)
    itch = {t: mold.itch_like(random, t) for t in "SADXEUCFPW"}

    async def send(payload: bytes, idx: int = 0, tuser: bool = False) -> None:
        await sysm.send(feed_frame(payload, idx), tuser, gap=2)

    await send(mold.pack(SESSION, 1, [itch["S"]]))  # feed not committed yet: DISABLED
    await sysm.finish()
    await configure_feed(w, FEED_ON)
    big = random.randbytes(100)
    await send(mold.pack(SESSION, 1000, [itch["S"], itch["A"], itch["D"]]))  # sync: E = 1003
    await send(mold.pack(SESSION, 1000, [itch["S"], itch["A"], itch["D"]]), idx=1)  # B copy: DUP, 3 dups
    await send(mold.pack(SESSION, 1003, [itch["X"], itch["E"]]), idx=1)  # E = 1005
    await send(mold.heartbeat(SESSION, 1005))
    await send(mold.pack(SESSION, 1010, [itch["U"]]))  # gap 5, E = 1011
    await send(mold.pack(SESSION, 1009, [itch["C"], itch["F"], itch["P"]]))  # 2 dups, 1011 out, E = 1012
    await send(mold.pack(b"SESSION002", 1012, [itch["S"]]))  # SESSION
    await send(mold.pack(SESSION, 1012, [big, itch["A"]]))  # 1012 oversize, 1013 out, E = 1014
    await send(mold.pack(SESSION, 1014, [itch["S"]]), tuser=True)  # ABORT (1014 out, discarded)
    await send(mold.pack(SESSION, 1014, [itch["S"]]), idx=2)  # OTHER_FEED
    await send(mold.eos(SESSION, 1014))
    await sysm.finish()
    statuses = [S(p.want.status) for p in sysm.mold_done]
    assert statuses == [S.DISABLED, S.OK, S.DUP, S.OK, S.HEARTBEAT, S.OK, S.OK, S.SESSION, S.OK, S.ABORT,
                        S.OTHER_FEED, S.EOS], statuses  # fmt: skip
    hand = [(1000, itch["S"]), (1001, itch["A"]), (1002, itch["D"]), (1003, itch["X"]), (1004, itch["E"]),
            (1010, itch["U"]), (1011, itch["P"]), (1013, itch["A"])]  # fmt: skip
    assert sysm.accepted_msgs == hand
    v = named(await snapshot_checked(w))
    want = {"OK": 5, "HEARTBEAT": 1, "EOS": 1, "DUP": 1, "DISABLED": 1, "OTHER_FEED": 1, "ABORT": 1, "SESSION": 1,
            "MSGS": 8, "DUP_MSGS": 5, "OVERSIZE_MSGS": 1, "GAP_EVENTS": 1, "GAP_MSGS": 5}  # fmt: skip
    for n in [s.name for s in S] + ["MSGS", "DUP_MSGS", "OVERSIZE_MSGS", "GAP_EVENTS", "GAP_MSGS"]:
        assert v[f"MOLD_{n}"] == want.get(n, 0), f"MOLD_{n} = {v[f'MOLD_{n}']}, hand-counted {want.get(n, 0)}"
    assert (v["EXPECTED_SEQ"], v["FEED_STATE"]) == (1014, 0b11)  # synced, eos
    assert (v["EXPECTED_SEQ"] - 1000) == accounted(v) == 14  # E_end - E_sync

    await configure_feed(w, replace(FEED_ON, resync_seq=5000), resync=True)  # set
    assert sysm.resyncs[-1][1:] == (False, "between")
    v = named(await snapshot_checked(w))
    assert (v["EXPECTED_SEQ"], v["FEED_STATE"]) == (5000, 0b01), "set: E = 5000, eos cleared"
    await send(mold.pack(SESSION, 5000, [itch["S"], itch["W"]]))
    await sysm.finish()
    v = named(await snapshot_checked(w))
    assert (v["EXPECTED_SEQ"], v["FEED_STATE"], v["MOLD_OK"], v["MOLD_MSGS"], v["MOLD_GAP_MSGS"]) == (5002, 1, 6, 10, 5)

    await configure_feed(w, replace(FEED_ON, resync_learn=1), resync=True)  # learn
    v = named(await snapshot_checked(w))
    assert (v["EXPECTED_SEQ"], v["FEED_STATE"]) == (5002, 0), "learn: not synced, E kept"
    await send(mold.pack(SESSION, 9000, [itch["S"]]))  # synchronizes: no gap
    await sysm.finish()
    assert sysm.mold_done[-1].want.synced_now
    v = named(await snapshot_checked(w))
    assert (v["EXPECTED_SEQ"], v["FEED_STATE"], v["MOLD_OK"], v["MOLD_MSGS"], v["MOLD_GAP_EVENTS"]) == (9001, 1, 7, 11, 1)
    assert sysm.accepted_msgs[len(hand) :] == [(5000, itch["S"]), (5001, itch["W"]), (9000, itch["S"])]
    w.feed.check_done()


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


FEED_BINS = [
    *(f"status_{s.name}" for s in MoldStatus), "gap", "partial_dup", "oversize", "synced_now",
    "ab_dup", "ab_b_first", "f2_fcs", "f2_cut", "f2_filter_drop",
    "resync_learn", "resync_set", "resync_waited", "resync_between", "feed_cfg_change_in_packet",
    "identity_checked", "snapshot_while_traffic",
]  # fmt: skip
N_IDENTITY = 5  # snapshot pairs whose accounting identity tc_feed_random must check at least


@cocotb.test(timeout_time=3000, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_feed_random(dut):
    """N_FEED frames of random_feed packets (every MoldUDP64 outcome category) on lines A and B,
    with A/B copies (the copy right after, a few packets later or before the original), F2 faults
    (FCS error, a frame cut short, a wrong port), while software commits FEED configurations (mask
    without B, no session check, another session, feed off), resyncs (learn, or set near the
    expected seq), recommits NET and takes snapshots. Every message, packet event and feed state
    against the chain model; every snapshot exact; the accounting identity between consecutive
    snapshots in sync with no resync between them; coverage gate (11 §2.9)."""
    w, sysm = await start(dut)
    rng = random.Random(random.getrandbits(32))
    cov = Coverage("rx_csr.tc_feed_random", FEED_BINS)
    r = dut.u_wrap
    variants = [FEED_ON] * 6 + [
        replace(FEED_ON, filter_mask=0x01),  # line B is another feed's
        replace(FEED_ON, session_check=0),
        replace(FEED_ON, session=int.from_bytes(b"SESSION002", "big")),
        replace(FEED_ON, enable=0),
    ]
    identity = {"prev": None, "checked": 0}
    # Variants come from a shuffled bag, refilled when empty: each of them (feed off included) is
    # committed once in every 10 FEED configurations, so status_DISABLED does not depend on the seed.
    bag: list[FeedCfg] = []

    def next_variant() -> FeedCfg:
        if not bag:
            bag.extend(variants)
            rng.shuffle(bag)
        return bag.pop()

    def check_identity(seq: int, values: list[int]) -> None:
        """Against the previous snapshot: in sync at both and no resync applied from FEED_DELAY + 2
        net edges before the first acceptance edge on (a waiting one counts at its packet's tlast),
        so E moved only by accepted packets."""
        edge = w.snap.accepts[seq - 1]
        v, prev = named(values), identity["prev"]
        identity["prev"] = (edge, v)
        if prev is None:
            return
        p_edge, p = prev
        lo = w.snap.edge_index[p_edge] - FEED_DELAY - 2
        if not (v["FEED_STATE"] & 1 and p["FEED_STATE"] & 1):
            return
        if any(lo <= w.snap.edge_index[t] and t <= edge for t in sysm.resync_applied):
            return
        moved = (v["EXPECTED_SEQ"] - p["EXPECTED_SEQ"]) & MASK64
        counted = (accounted(v) - accounted(p)) & MASK64
        assert moved == counted, f"snapshot {seq}: EXPECTED_SEQ moved {moved}, counters account for {counted}"
        identity["checked"] += 1

    async def snapshot() -> None:
        for _ in range(rng.randint(1, 3)):
            assert await w.drv.write(STATS_CTRL, 1) == OKAY
        await w.snap.quiet()
        seq = await w.snap.seq()
        values = await w.snap.read_counters()
        await w.snap.check(seq, values)
        check_identity(seq, values)
        if sysm.cur is not None or sysm.out_q:
            cov.hit("snapshot_while_traffic")

    done = {"traffic": False}

    async def software():
        while not done["traffic"]:
            for _ in range(rng.randint(100, 800)):
                await RisingEdge(r.host_clk)
            x = rng.random()
            if x < 0.4:
                await snapshot()
            elif x < 0.65:
                await configure_feed(w, next_variant())
            elif x < 0.9:
                learn = rng.random() < 0.3
                e = (sysm.state.expected + rng.randint(-30, 30)) % SEQ_MOD
                cfg = replace(FEED_ON, resync_learn=int(learn), resync_seq=e)
                cov.hit("resync_learn" if learn else "resync_set")
                await configure_feed(w, cfg, resync=True)
            else:
                await configure(w, NET_FEED)

    await configure(w, NET_FEED)
    await configure_feed(w, FEED_ON)
    task = cocotb.start_soon(software())
    pkts = mold.random_feed(rng, N_FEED, FeedConfig(True, True, SESSION, 0x03))
    later: list[tuple[int, mold.FeedPacket]] = []  # (send before this many more frames, copy)
    sent = 0

    async def send(p: mold.FeedPacket, idx: int) -> None:
        nonlocal sent
        frame, tuser, dport = feed_frame(p.payload, idx), p.abort, None
        fault = rng.random()
        if fault < 0.03:
            tuser = True
            cov.hit("f2_fcs")
        elif fault < 0.06 and len(frame) > 43:
            frame = frame[: rng.randint(43, len(frame) - 1)]  # truncated: F2 aborts it
            cov.hit("f2_cut")
        elif fault < 0.08:
            frame = feed_frame(p.payload, idx, dport=7777)  # no filter entry: F2 drops it
            cov.hit("f2_filter_drop")
        await sysm.send(frame, tuser, gap=rng.choice([0, 0, 1, 3]))
        sent += 1

    for p in pkts:
        if sent >= N_FEED:
            break
        copy = p.filter_idx in (0, 1) and rng.random() < 0.4
        other = 1 - p.filter_idx if p.filter_idx in (0, 1) else p.filter_idx
        if copy and rng.random() < 0.2:
            await send(p, other)  # the copy first
            cov.hit("ab_b_first")
            copy = False
        await send(p, p.filter_idx)
        if copy:
            if rng.random() < 0.6:
                await send(p, other)
            else:
                later.append((rng.randint(1, 5), (p, other)))
        for item in [x for x in later if x[0] <= 1]:
            later.remove(item)
            await send(*item[1])
        later = [(k - 1, x) for k, x in later]
    for _, (p, idx) in later:
        await send(p, idx)
    await sysm.finish()
    done["traffic"] = True
    await task
    await w.feed.wait_idle()
    w.feed.check_done()
    await snapshot()
    assert sent >= N_FEED

    for p in sysm.mold_done:
        w_ = p.want
        cov.hit(f"status_{S(w_.status).name}")
        if w_.gap:
            cov.hit("gap")
        if w_.dup_msgs and w_.status == S.OK:
            cov.hit("partial_dup")
        if w_.oversize:
            cov.hit("oversize")
        if w_.synced_now:
            cov.hit("synced_now")
        if w_.status == S.DUP and p.sent.want.meta.filter_idx in (0, 1):
            cov.hit("ab_dup")
    for _, _, where in sysm.resyncs:
        if where in ("waited", "between"):
            cov.hit(f"resync_{where}")
    if sysm.feed_cfg_changed_in_pkt:
        cov.hit("feed_cfg_change_in_packet")
    if identity["checked"] >= N_IDENTITY:
        cov.hit("identity_checked")
    dut._log.info(f"{len(sysm.done)} frames, {len(sysm.mold_done)} MoldUDP64 packets, "
                  f"{len(sysm.accepted_msgs)} accepted messages, {len(sysm.resyncs)} resyncs, "
                  f"{len(w.snap.accepts)} snapshots, {identity['checked']} identity checks")  # fmt: skip
    cov.check(Path.cwd())  # the run directory


# The long tests run as their own pytest entries.
LONG_TESTS = ("tc_rx_csr_random", "tc_feed_random")


def directed_tests() -> list[str]:
    return [name for name in globals() if name.startswith("tc_") and name not in LONG_TESTS]


# Clock profiles: (net period, host period); jitter = the CDC jitter model in the build.
@pytest.mark.parametrize(
    "group, jit, ratio",
    [("directed", 0, "net2host"), ("directed", 0, "host2net"),
     ("tc_rx_csr_random", 0, "net2host"), ("tc_rx_csr_random", 1, "jitter"),
     ("tc_feed_random", 0, "net2host"), ("tc_feed_random", 1, "jitter")],
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
