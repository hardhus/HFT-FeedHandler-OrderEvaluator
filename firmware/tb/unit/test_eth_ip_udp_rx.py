"""eth_ip_udp_rx: Ethernet/IPv4/UDP receive filter (audit/09).

Every frame is checked against the golden model (model/net.py):
- exactly one stat event per frame, with the model's reason and byte count;
- the payload on m_axis_udp: the model's bytes, tuser (abort) only on the last beat,
  tkeep 0xFF before the last beat and contiguous from lane 0 on it;
- the m_udp_* metadata and m_udp_sof_ts on the first and last beat of every payload packet
  and at the stat event (the DUT holds them from stage B until the next frame's stage B;
  the SVA a_meta_stable checks every beat in between).
The monitor numbers the cycles, so latency tests check 09 §2.3 exactly.
"""

import ipaddress
import math
import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import cocotb
import pytest
from cocotb.triggers import ReadOnly, RisingEdge
from cocotbext.axi import AxiStreamBus, AxiStreamFrame, AxiStreamSource
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether
from scapy.packet import Raw

from common.clocks import start_clock
from common.coverage import Coverage
from common.runner import run_cocotb
from model.net import (
    RxConfig,
    RxExpect,
    RxMeta,
    RxReason,
    expect,
    ip_int,
    last_beat_payload_bytes,
    mcast_mac,
    pad_to,
    random_config,
    random_traffic,
    udp_frame,
    with_field,
    with_ip_options,
    with_vlan,
)

PERIOD_PS = 6400  # net_clk, 156.25 MHz
N_FILTERS = 8
STAGE_A_BEAT = 4  # cfg_* is sampled together with beat 4 (09 §2.3)
SOF_TO_PAYLOAD = 7  # cycles from the SOF beat to the first payload beat (09 §2.3)
N_RANDOM = 10_000  # 05 F2 "done": do not lower (09 §5)
N_LINE_RATE = 2000
LOCAL_MAC = "02:00:00:00:00:01"
CFG = RxConfig(local_mac=LOCAL_MAC, filters=(("10.0.0.2", 5000, True),))
CONTIGUOUS_KEEP = {(1 << n) - 1 for n in range(1, 9)}


@dataclass
class Frame:
    """One frame: the model's outcome and what the monitor saw. Cycle numbers count rising
    edges; an input beat belongs to the cycle in which it is presented, an output to the
    cycle in which it is visible."""

    name: str
    want: RxExpect  # for a cut frame: the outcome had it not been cut
    cut: bool = False  # cut by a reset: no stat event, only a prefix of the payload
    ts: int | None = None  # ts_now at the SOF beat
    sof: int = -1
    eof: int = -1
    out_first: int = -1
    out_last: int = -1
    stat: int = -1


class Rx:
    """Drives frames and cfg_*; checks every stat event, payload packet and metadata
    against the model."""

    def __init__(self, dut, cfg: RxConfig) -> None:
        self.dut = dut
        self.cycle = 0
        self.awaiting_sof: deque[Frame] = deque()  # sent, SOF not seen yet
        self.in_flight: Frame | None = None  # input frame between SOF and tlast
        self.stat_q: deque[Frame] = deque()  # stat event outstanding
        self.pay_q: deque[Frame] = deque()  # payload packet outstanding
        self.pkt = bytearray()  # payload packet being received
        self.pkt_beats = 0
        self.stat_events = 0  # totals over the test
        self.out_beats = 0
        # Signal handles, looked up once: the long tests run a few 10^5 cycles.
        self.clk, self.rst, self.ts_now = dut.clk, dut.rst, dut.ts_now
        self.s_tdata, self.s_tkeep, self.s_tvalid = dut.s_axis_rx_tdata, dut.s_axis_rx_tkeep, dut.s_axis_rx_tvalid
        self.s_tready, self.s_tlast, self.s_tuser = dut.s_axis_rx_tready, dut.s_axis_rx_tlast, dut.s_axis_rx_tuser
        self.m_tdata, self.m_tkeep, self.m_tvalid = dut.m_axis_udp_tdata, dut.m_axis_udp_tkeep, dut.m_axis_udp_tvalid
        self.m_tlast, self.m_tuser = dut.m_axis_udp_tlast, dut.m_axis_udp_tuser
        self.meta = (dut.m_udp_src_ip, dut.m_udp_dst_ip, dut.m_udp_dst_port, dut.m_udp_len, dut.m_udp_filter_idx)
        self.sof_ts, self.stat_valid = dut.m_udp_sof_ts, dut.stat_valid
        self.stat_reason, self.stat_bytes = dut.stat_reason, dut.stat_bytes
        self.apply(cfg)

    def apply(self, cfg: RxConfig) -> None:
        """Drive cfg_* now (the DUT sees it at the next clock edge)."""
        assert len(cfg.filters) <= N_FILTERS
        self.cfg = cfg
        entries = [*cfg.filters, *[("0.0.0.0", 0, False)] * (N_FILTERS - len(cfg.filters))]
        dut = self.dut
        dut.cfg_rx_en.value = int(cfg.rx_en)
        dut.cfg_local_mac.value = int(cfg.local_mac.replace(":", ""), 16)
        dut.cfg_accept_bcast.value = int(cfg.accept_bcast)
        dut.cfg_filter_ip.value = sum(ip_int(ip) << (32 * i) for i, (ip, _, _) in enumerate(entries))
        dut.cfg_filter_port.value = sum(port << (16 * i) for i, (_, port, _) in enumerate(entries))
        dut.cfg_filter_en.value = sum(int(en) << i for i, (_, _, en) in enumerate(entries))

    async def idle(self, cycles: int) -> None:
        self.s_tvalid.value = 0
        for _ in range(cycles):
            await RisingEdge(self.clk)

    def expect_frame(
        self, frame: bytes, *, tuser: bool = False, cfg: RxConfig | None = None,
        intent: RxReason | None = None, name: str = "",
    ) -> Frame:  # fmt: skip
        """Register the model's outcome of a frame about to be sent (under cfg, default:
        the config applied now). intent: the reason the test means to produce."""
        f = Frame(name or f"frame {len(self.awaiting_sof) + self.cycle}", expect(frame, tuser, cfg or self.cfg))
        assert intent is None or f.want.reason == intent, (
            f"{f.name}: test frame gives {f.want.reason.name}, meant {intent.name}"
        )
        self.awaiting_sof.append(f)
        self.stat_q.append(f)
        if f.want.payload is not None:
            self.pay_q.append(f)
        return f

    async def send(
        self,
        frame: bytes,
        *,
        tuser: bool = False,
        cfg: RxConfig | None = None,
        intent: RxReason | None = None,
        gaps: dict[int, int] | None = None,
        at_beat: dict[int, Callable[[], None]] | None = None,
        noise: bool = False,
        name: str = "",
    ) -> Frame:
        """Present one frame, byte 0 = tdata[7:0], and expect the model's outcome.
        gaps[k]: idle cycles before beat k. at_beat[k]: runs while beat k is presented, so
        the DUT sees its effect at the same edge as beat k. noise: random tuser on the
        beats before tlast (the MAC's tuser is only valid on the tlast beat)."""
        f = self.expect_frame(frame, tuser=tuser, cfg=cfg, intent=intent, name=name)
        await self._drive(frame, tuser, gaps or {}, at_beat or {}, noise)
        return f

    async def send_cut(self, frame: bytes, beats: int, name: str = "") -> Frame:
        """Present the first `beats` beats, then a 1-cycle reset; the rest of the frame is
        dropped (the MAC is reset with us). No stat event; the payload beats that leave
        before the reset must be a prefix of the model's payload, without tlast."""
        f = Frame(name or "cut frame", expect(frame, False, self.cfg), cut=True)
        self.awaiting_sof.append(f)
        if f.want.payload is not None:
            self.pay_q.append(f)
        await self._drive(frame[: 8 * beats], False, {}, {}, False, last=False)
        self.dut.s_axis_rx_tvalid.value = 0
        self.dut.rst.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.rst.value = 0
        return f

    async def _drive(self, frame: bytes, tuser: bool, gaps: dict, at_beat: dict, noise: bool, last: bool = True):
        beats = [frame[i : i + 8] for i in range(0, len(frame), 8)]
        for k, beat in enumerate(beats):
            if gaps.get(k):
                await self.idle(gaps[k])
            tlast = last and k == len(beats) - 1
            self.s_tdata.value = int.from_bytes(beat.ljust(8, b"\0"), "little")
            self.s_tkeep.value = (1 << len(beat)) - 1
            self.s_tlast.value = int(tlast)
            self.s_tuser.value = int(tuser) if tlast else int(noise and random.random() < 0.5)
            self.s_tvalid.value = 1
            if k in at_beat:
                at_beat[k]()
            await RisingEdge(self.clk)
        self.s_tvalid.value = 0

    async def finish(self, cycles: int = 8) -> None:
        """Let the last events come out; every frame must have produced its outcome."""
        await self.idle(cycles)
        for name, q in (("stat event", self.stat_q), ("payload packet", self.pay_q), ("SOF", self.awaiting_sof)):
            assert not q, f"{len(q)} frame(s) without a {name} (first: {q[0].name})"
        assert not self.pkt_beats, f"payload packet without tlast ({self.pkt_beats} beats)"

    async def _monitor(self) -> None:
        """Each cycle: drive the free-running ts_now, then (ReadOnly) check the outputs and
        track the input frames."""
        ts = random.getrandbits(63)
        while True:
            await RisingEdge(self.clk)
            ts += 1
            self.ts_now.value = ts  # sampled by the DUT at the next edge, seen below
            await ReadOnly()
            self.cycle += 1
            if self.stat_valid.value:
                self._check_stat()
            if self.m_tvalid.value:
                self._out_beat()
            if self.rst.value:
                self.in_flight = None
                if self.pay_q and self.pay_q[0].cut:  # its partial payload packet ends here
                    self.pay_q.popleft()
                    self.pkt.clear()
                    self.pkt_beats = 0
                assert not self.pkt_beats, "reset in the middle of a payload packet that was not cut"
            elif self.s_tvalid.value:
                assert self.s_tready.value == 1, "RX must never backpressure the MAC"
                if self.in_flight is None:
                    assert self.awaiting_sof, "input frame that the test did not register"
                    self.in_flight = self.awaiting_sof.popleft()
                    self.in_flight.ts = ts  # sampled by the DUT at the SOF edge
                    self.in_flight.sof = self.cycle
                if self.s_tlast.value:
                    self.in_flight.eof = self.cycle
                    self.in_flight = None

    def _check_meta(self, f: Frame, where: str) -> None:
        meta = RxMeta(*(int(h.value) for h in self.meta))
        assert meta == f.want.meta, f"{f.name}: metadata at {where} {meta}, model {f.want.meta}"
        ts = int(self.sof_ts.value)
        assert ts == f.ts, f"{f.name}: m_udp_sof_ts {ts:#x} at {where}, ts_now at the SOF beat {f.ts:#x}"

    def _check_stat(self) -> None:
        assert self.stat_q, "stat event with no frame outstanding"
        f = self.stat_q.popleft()
        f.stat = self.cycle
        self.stat_events += 1
        reason, nbytes = int(self.stat_reason.value), int(self.stat_bytes.value)
        got = RxReason(reason).name if reason in RxReason._value2member_map_ else str(reason)
        assert (reason, nbytes) == (f.want.reason, f.want.nbytes), (
            f"{f.name}: stat ({got}, {nbytes} B), model ({f.want.reason.name}, {f.want.nbytes} B)"
        )
        if f.want.meta is not None:
            self._check_meta(f, "the stat event")

    def _out_beat(self) -> None:
        assert self.pay_q, f"payload beat {int(self.m_tdata.value):#x} with no packet expected"
        f = self.pay_q[0]
        keep, last, user = int(self.m_tkeep.value), int(self.m_tlast.value), int(self.m_tuser.value)
        if self.pkt_beats == 0:
            f.out_first = self.cycle
        if self.pkt_beats == 0 or last:  # in between, a_meta_stable (SVA) holds it every cycle
            self._check_meta(f, f"payload beat {self.pkt_beats}")
        if not last:
            assert (keep, user) == (0xFF, 0), f"{f.name}: beat {self.pkt_beats} keep {keep:#04x} tuser {user} before tlast"
        else:
            assert keep in CONTIGUOUS_KEEP, f"{f.name}: last beat keep {keep:#04x} is not contiguous from lane 0"
        n = keep.bit_count()
        self.pkt += int(self.m_tdata.value).to_bytes(8, "little")[:n]
        self.pkt_beats += 1
        self.out_beats += 1
        if f.cut:
            assert not last, f"{f.name}: the cut frame's payload packet got a tlast"
            assert f.want.payload.startswith(self.pkt), f"{f.name}: payload before the reset is not the model's prefix"
        elif last:
            self.pay_q.popleft()
            f.out_last = self.cycle
            got, want = bytes(self.pkt), f.want.payload
            bad = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
            assert got == want, f"{f.name}: payload {len(got)} B, model {len(want)} B; first difference at byte {bad}"
            assert bool(user) == f.want.aborted, f"{f.name}: tuser (abort) {user}, model {f.want.aborted}"
            assert self.pkt_beats == math.ceil(len(want) / 8), f"{f.name}: {self.pkt_beats} beats for {len(want)} B"
            self.pkt.clear()
            self.pkt_beats = 0


async def start(dut, cfg: RxConfig = CFG) -> Rx:
    """Idle inputs, config and reset first, then the clock (no edge with undriven inputs)."""
    dut.s_axis_rx_tvalid.value = 0
    dut.s_axis_rx_tdata.value = 0
    dut.s_axis_rx_tkeep.value = 0
    dut.s_axis_rx_tlast.value = 0
    dut.s_axis_rx_tuser.value = 0
    dut.ts_now.value = 0
    rx = Rx(dut, cfg)
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    await RisingEdge(dut.clk)  # the synchronous reset has cleared the control flops
    # Monitor from here on: started after the last reset edge, it would miss a SOF beat
    # presented right after start() returns.
    cocotb.start_soon(rx._monitor())
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    return rx


def payload(n: int) -> bytes:
    return random.randbytes(n)


def flip_bit(frame: bytes, pos: int, bit: int) -> bytes:
    return frame[:pos] + bytes([frame[pos] ^ (1 << bit)]) + frame[pos + 1 :]


def frame_for(cfg: RxConfig, idx: int, n: int | None = None) -> bytes:
    """A frame for filter entry idx: its multicast MAC for a group address, else ours."""
    ip, port, _ = cfg.filters[idx]
    mac = mcast_mac(ip) if ip_int(ip) >> 28 == 0xE else cfg.local_mac
    return pad_to(udp_frame(payload(n or random.randint(18, 100)), dst_mac=mac, dst_ip=ip, dport=port))


@cocotb.test(timeout_time=1, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_smoke(dut):
    """Idle input after reset: the MAC is never stalled and nothing comes out."""
    rx = await start(dut)
    await rx.finish(32)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_ip_hdr_checks(dut):
    """EtherType (ARP, IPv6, VLAN), version, IHL, a single-bit checksum error in each header
    word, fragments and protocols: each gives its reason; DF and the reserved bit are ignored."""
    rx = await start(dut)
    R = RxReason
    f = udp_frame(payload(40))
    cases = [
        ("valid", f, R.OK),
        ("arp", with_field(f, "ethertype", 0x0806), R.ETHERTYPE),
        ("ipv6", with_field(f, "ethertype", 0x86DD), R.ETHERTYPE),
        ("vlan", with_vlan(f), R.ETHERTYPE),
        ("version 6", with_field(f, "ver_ihl", 0x65), R.IP_HDR),
        ("ihl 6 + options", with_ip_options(f), R.IP_HDR),
        ("ihl 4", with_field(f, "ver_ihl", 0x44), R.IP_HDR),
        *[(f"checksum: bit {w % 8} of word {w}", flip_bit(f, 14 + 2 * w + w % 2, w % 8), R.IP_HDR) for w in range(10)],
        ("MF", with_field(f, "flags_frag", 0x6000), R.FRAG),
        ("offset 1", with_field(f, "flags_frag", 0x0001), R.FRAG),
        ("offset 0x1000", with_field(f, "flags_frag", 0x5000), R.FRAG),
        ("no DF", with_field(f, "flags_frag", 0x0000), R.OK),
        ("reserved flag", with_field(f, "flags_frag", 0xC000), R.OK),
        ("tcp", with_field(f, "proto", 6), R.PROTO),
        ("icmp", with_field(f, "proto", 1), R.PROTO),
        ("igmp", with_field(f, "proto", 2), R.PROTO),
    ]  # fmt: skip
    for name, frame, intent in cases:
        await rx.send(frame, intent=intent, name=name)
    await rx.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_filter_table(dut):
    """M4: every entry on its own, the lowest index among duplicates, disabled entries, IP or
    port mismatch, multicast MAC/IP consistency, broadcast on/off and a foreign unicast MAC."""
    R = RxReason
    full = random_config(random.Random(random.getrandbits(32)), N_FILTERS, accept_bcast=False)
    rx = await start(dut, full)

    for i in range(N_FILTERS):
        only_i = replace(full, filters=tuple((ip, p, j == i) for j, (ip, p, _) in enumerate(full.filters)))
        rx.apply(only_i)
        await rx.send(frame_for(full, i), intent=R.OK, name=f"only entry {i} enabled")
        await rx.send(frame_for(full, (i + 1) % N_FILTERS), intent=R.FILTER, name=f"entry {(i + 1) % N_FILTERS} off")

    dup = list(full.filters)
    ip3, port3, _ = dup[3]
    dup[1] = (ip3, port3, False)
    dup[5] = (ip3, port3, True)
    rx.apply(replace(full, filters=tuple(dup)))
    await rx.send(frame_for(full, 3), intent=R.OK, name="duplicates 1 (off), 3, 5 -> 3")

    rx.apply(full)
    ip0, port0, _ = full.filters[0]
    taken = {(ip_int(ip), p) for ip, p, _ in full.filters}
    other_port = next(p for p in range(1, 65536) if (ip_int(ip0), p) not in taken)
    f0 = frame_for(full, 0)
    await rx.send(with_field(f0, "dport", other_port), intent=R.FILTER, name="IP right, port wrong")
    await rx.send(with_field(f0, "dst_ip", "10.255.255.254"), intent=R.FILTER, name="port right, IP wrong")
    await rx.send(with_field(f0, "dst_mac", "02:00:00:00:00:77"), intent=R.MAC, name="foreign unicast MAC")

    f1 = frame_for(full, 1)  # odd entries are multicast groups
    mac1 = int.from_bytes(f1[:6], "big")
    await rx.send(f1, intent=R.OK, name="multicast group")
    await rx.send(with_field(f1, "dst_mac", (mac1 ^ 0x1).to_bytes(6, "big")), intent=R.MAC, name="other group's MAC")
    await rx.send(with_field(f1, "dst_mac", (mac1 | 1 << 23).to_bytes(6, "big")), intent=R.MAC, name="MAC bit 23 set")
    await rx.send(with_field(f0, "dst_mac", mcast_mac(ip0)), intent=R.MAC, name="multicast MAC, unicast IP")

    bcast = with_field(f0, "dst_mac", "ff:ff:ff:ff:ff:ff")
    await rx.send(bcast, intent=R.MAC, name="broadcast, not accepted")
    rx.apply(replace(full, accept_bcast=True))
    await rx.send(bcast, intent=R.OK, name="broadcast, accepted")
    await rx.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_k1_sticky_ip_regression(dut):
    """Legacy K1 (01 §2): a valid packet, then dst IPs whose lower or upper half alone matches
    the filter. Both must be FILTER, back to back and with gaps."""
    R = RxReason
    rx = await start(dut, RxConfig(local_mac=LOCAL_MAC, filters=(("192.168.1.10", 5000, True),)))
    good = udp_frame(payload(30), dst_ip="192.168.1.10")
    low_half = udp_frame(payload(30), dst_ip="10.0.1.10")  # x.x.1.10 matches, 192.168 does not
    high_half = udp_frame(payload(30), dst_ip="192.168.7.7")  # 192.168 matches, 1.10 does not
    for gap in (0, 3):
        for name, frame, intent in [
            ("good", good, R.OK),
            ("low half only", low_half, R.FILTER),
            ("high half only", high_half, R.FILTER),
            ("good", good, R.OK),
            ("high half only", high_half, R.FILTER),
            ("low half only", low_half, R.FILTER),
        ]:
            await rx.send(frame, intent=intent, name=f"{name} (gap {gap})")
            await rx.idle(gap)
    await rx.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_cfg_change_no_tearing(dut):
    """09 §2.6: the filter changes from (A, P1) to (B, P2) at each cycle 0..8 after the SOF.
    A and B differ in both 16-bit halves, so a config read in pieces (port vs IP, or the two IP
    halves in different cycles) would accept a mixed packet: (A, P2), (B, P1), or a dst IP
    made of one half of A and one of B, with either port. Mixed packets are never accepted;
    the control packets (A, P1) and (B, P2) follow exactly the config of the stage-A cycle
    (beat 4)."""
    R = RxReason
    a, b, p1, p2 = "10.0.0.2", "192.168.1.3", 5000, 6000
    a_hi_b_lo = str(ipaddress.IPv4Address(ip_int(a) & 0xFFFF0000 | ip_int(b) & 0xFFFF))
    b_hi_a_lo = str(ipaddress.IPv4Address(ip_int(b) & 0xFFFF0000 | ip_int(a) & 0xFFFF))
    mixed = [(a, p2), (b, p1), *((ip, p) for ip in (a_hi_b_lo, b_hi_a_lo) for p in (p1, p2))]
    old = RxConfig(local_mac=LOCAL_MAC, filters=((a, p1, True),))
    new = replace(old, filters=((b, p2, True),))
    rx = await start(dut, old)
    for k in range(9):
        seen = new if k <= STAGE_A_BEAT else old
        for ip, port in [*mixed, (a, p1), (b, p2)]:
            rx.apply(old)
            intent = R.OK if seen.filters[0][:2] == (ip, port) else R.FILTER
            await rx.send(
                udp_frame(payload(80), dst_ip=ip, dport=port),  # 16 beats
                cfg=seen,
                intent=intent,
                at_beat={k: lambda: rx.apply(new)},
                name=f"({ip}, {port}), change at beat {k}",
            )
    await rx.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_rx_disable(dut):
    """cfg_rx_en is latched at the SOF: a change during a frame applies from the next frame;
    a change presented with the SOF beat applies to that frame."""
    R = RxReason
    off = replace(CFG, rx_en=False)
    rx = await start(dut)
    await rx.send(udp_frame(payload(60)), cfg=CFG, at_beat={3: lambda: rx.apply(off)}, intent=R.OK, name="1->0 mid")
    await rx.send(udp_frame(payload(60)), intent=R.DISABLED, name="after 1->0")
    await rx.send(udp_frame(payload(60)), cfg=off, at_beat={3: lambda: rx.apply(CFG)}, intent=R.DISABLED, name="0->1 mid")
    await rx.send(udp_frame(payload(60)), intent=R.OK, name="after 0->1")
    await rx.send(udp_frame(payload(60)), cfg=off, at_beat={0: lambda: rx.apply(off)}, intent=R.DISABLED, name="1->0 at SOF")
    await rx.send(udp_frame(payload(60)), cfg=CFG, at_beat={0: lambda: rx.apply(CFG)}, intent=R.OK, name="0->1 at SOF")
    rx.apply(off)
    await rx.send(bytes(5), intent=R.DISABLED, name="single-beat frame, disabled")
    rx.apply(CFG)
    await rx.send(bytes(5), intent=R.LEN, name="single-beat frame, enabled")
    await rx.finish()  # fmt: skip


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_length_checks(dut):
    """Runt, padding, trailing bytes, truncation, UDP/IP length mismatch, and frames that end in
    the header: before stage A (<= 40 B), with stage B (41..48 B) and after it (gap)."""
    R = RxReason
    rx = await start(dut)
    p4 = udp_frame(payload(4))  # 46 B datagram + Ethernet header
    p18 = udp_frame(payload(18))  # exactly 60 B
    p30 = udp_frame(payload(30))  # 72 B
    cases = [
        ("60 B padded", pad_to(p4), R.OK),
        ("59 B runt", pad_to(p4, 59), R.LEN),
        ("64 B, padding past 60", pad_to(p4, 64), R.LEN),
        ("60 B, no padding", p18, R.OK),
        ("61 B, one byte after the datagram", p18 + b"\x00", R.LEN),
        ("truncated at 65 B", p30[:65], R.LEN),
        ("truncated at 42 B", p30[:42], R.LEN),
        ("truncated at 47 B", p30[:47], R.LEN),
        ("truncated at 48 B", p30[:48], R.LEN),
        ("41 B: headers incomplete", p30[:41], R.LEN),
        ("40 B: ends with beat 4", p30[:40], R.LEN),
        ("33 B", p30[:33], R.LEN),
        ("1 B", p30[:1], R.LEN),
        ("udp_len = ip_len - 19", with_field(p30, "udp_len", 39), R.LEN),
        ("udp_len = ip_len - 21", with_field(p30, "udp_len", 37), R.LEN),
        ("udp_len = 8 (empty)", pad_to(udp_frame(b"")), R.LEN),
        ("ip_len 19, udp_len 0xffff", with_field(with_field(pad_to(p4), "ip_len", 19), "udp_len", 0xFFFF), R.LEN),
        ("41 B with a bad MAC", with_field(p30, "dst_mac", "02:00:00:00:00:77")[:41], R.LEN),
    ]  # fmt: skip
    for name, frame, intent in cases:
        await rx.send(frame, intent=intent, name=name)
    # tlast in the stage-B cycle (42..48 B, no gap): the verdict must be this frame's, so the
    # previous frame's verdict is made different in both directions.
    filtered = with_field(p30, "dport", 5001)
    for n in range(42, 49):
        await rx.send(p30, intent=R.OK, name=f"accepted before {n} B")
        await rx.send(filtered[:n], intent=R.FILTER, name=f"{n} B, filtered, after an accepted frame")
        await rx.send(filtered, intent=R.FILTER, name=f"filtered before {n} B")
        await rx.send(p30[:n], intent=R.LEN, name=f"{n} B, header passes, after a filtered frame")
    # tlast after stage B, with gaps around the end of the header
    await rx.send(p30[:42], gaps={5: 3}, intent=R.LEN, name="42 B, gap before beat 5")
    await rx.send(p18, gaps={4: 2, 5: 4, 7: 1}, intent=R.OK, name="60 B with gaps")
    await rx.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_tuser_abort(dut):
    """tuser on the tlast beat: FCS for a frame that passes and for one rejected in the header.
    tuser on the other beats is noise (the MAC flags errors only on tlast)."""
    R = RxReason
    rx = await start(dut)
    good = udp_frame(payload(50))
    await rx.send(good, tuser=True, intent=R.FCS, name="good frame, FCS error")
    await rx.send(with_field(good, "dport", 5001), tuser=True, intent=R.FCS, name="filtered frame, FCS error")
    await rx.send(good[:30], tuser=True, intent=R.FCS, name="short frame, FCS error")
    for i in range(20):
        await rx.send(udp_frame(payload(random.randint(18, 200))), noise=True, intent=R.OK, name=f"tuser noise {i}")
    await rx.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_k2_byte_lane_order_scapy(dut):
    """Legacy K2: frames built by scapy (its own lengths and checksums) and driven by
    cocotbext-axi's AxiStreamSource, not by this testbench's driver. Every payload byte,
    IP byte and port byte is distinct, so any lane swap or shift shows up."""
    src_ip, dst_ip, sport, dport = "10.20.30.40", "172.16.33.66", 0xABCD, 0x1234
    rx = await start(dut, RxConfig(local_mac=LOCAL_MAC, filters=((dst_ip, dport, True),)))
    source = AxiStreamSource(AxiStreamBus.from_prefix(dut, "s_axis_rx"), dut.clk, dut.rst)
    frames = []
    for n in (1, 2, 5, 6, 7, 8, 9, 15, 16, 17, 64, 200, 255):
        pkt = Ether(dst=LOCAL_MAC, src="02:00:00:00:00:99") / IP(src=src_ip, dst=dst_ip) / UDP(sport=sport, dport=dport)
        frame = pad_to(bytes(pkt / Raw(bytes(range(n)))))
        frames.append(rx.expect_frame(frame, intent=RxReason.OK, name=f"scapy, {n} payload bytes"))
        await source.send(AxiStreamFrame(frame))
    await source.wait()
    await rx.finish()
    for f in frames:  # the model read these from scapy's parse; pin them to the literals too
        assert (f.want.meta.src_ip, f.want.meta.dst_ip, f.want.meta.dst_port) == (0x0A141E28, 0xAC102142, 0x1234)


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def tc_tail_all_last_lengths(dut):
    """Every case of the last payload beat: payload lengths 1..25 and 120..127, padded (short
    frames) and unpadded, plus the runts (abort). Each alone and followed with IPG 0 by an
    accepted frame, so a tail beat shares its cycle with the next SOF."""
    rx = await start(dut)
    for n in [*range(1, 26), *range(120, 128)]:
        f = udp_frame(payload(n))
        variants = [("padded", pad_to(f)), ("runt", f)] if len(f) < 60 else [("unpadded", f)]
        for kind, frame in variants:
            await rx.send(frame, name=f"{n} B {kind}, alone")
            await rx.idle(4)
            await rx.send(frame, name=f"{n} B {kind}, then IPG 0")
            await rx.send(udp_frame(payload(random.randint(18, 40))), intent=RxReason.OK, name=f"after {n} B {kind}")
    await rx.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_latency(dut):
    """09 §2.3, exact: SOF beat -> first payload beat 7 cycles; input tlast -> payload tlast
    1 cycle (<= 2 payload bytes in the last beat, or the last beat held for tlast) or 2 (a
    tail beat); input tlast -> stat 1 cycle. A packet whose only beat is held (09 §2.4:
    padding) starts with its tlast, 1 cycle after the input tlast."""
    rx = await start(dut)
    by_tail = {last_beat_payload_bytes(n): n for n in range(18, 26)}  # unpadded frames
    # (name, frame, intent, first beat after SOF or None if held, payload tlast after input tlast)
    cases = [(f"{t} payload byte(s) in the last beat", udp_frame(payload(by_tail[t])), RxReason.OK, SOF_TO_PAYLOAD,
              1 if t <= 2 else 2) for t in (1, 2, 3, 8)]
    cases += [
        ("padded, one beat (held)", pad_to(udp_frame(payload(4))), RxReason.OK, None, 1),
        ("padded, two beats", pad_to(udp_frame(payload(10))), RxReason.OK, SOF_TO_PAYLOAD, 1),
        ("bytes after the datagram (last beat held)", udp_frame(payload(10)) + bytes(20), RxReason.LEN, SOF_TO_PAYLOAD, 1),
    ]
    for name, frame, intent, first_latency, tlast_latency in cases:
        f = await rx.send(frame, intent=intent, name=name)
        await rx.idle(6)
        if first_latency is None:
            assert f.out_first == f.out_last, f"{name}: expected a single, held beat"
        else:
            assert f.out_first - f.sof == first_latency, f"{name}: first payload beat {f.out_first - f.sof} cycles after SOF"
        assert f.out_last - f.eof == tlast_latency, f"{name}: payload tlast {f.out_last - f.eof} cycles after tlast"
        assert f.stat - f.eof == 1, f"{name}: stat {f.stat - f.eof} cycles after tlast"
    await rx.finish()  # fmt: skip


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_reset_mid_frame(dut):
    """09 §2.8: a 1-cycle reset in the header, at the first payload beat and in the middle of
    the payload. The cut frame gets no stat event and no further payload; frames after the
    reset come out exactly."""
    rx = await start(dut)
    for cut in (3, 7, 12):
        await rx.send(udp_frame(payload(60)), intent=RxReason.OK, name=f"before the reset at beat {cut}")
        await rx.idle(4)
        f = await rx.send_cut(udp_frame(payload(200)), cut, name=f"cut at beat {cut}")
        # The payload starts with beat 6: a cut in the header leaves no output, later cuts do.
        assert (f.out_first != -1) == (cut > 6), f"cut at beat {cut}: payload out before the reset {f.out_first}"
        await rx.send(udp_frame(payload(50)), intent=RxReason.OK, name=f"first after the reset at beat {cut}")
        await rx.send(udp_frame(payload(30)), intent=RxReason.OK, name=f"second after the reset at beat {cut}")
    await rx.finish()


def coverage_bins() -> list[str]:
    """Required bins of tc_random_golden (09 §2.12)."""
    return [
        *(f"reason_{r.name}" for r in RxReason),
        *(f"tail_{n}_{kind}" for n in range(1, 9) for kind in ("nopad", "padded", "abort")),
        "mac_ucast",
        "mac_mcast",
        "mac_bcast",
        *(f"filter_idx_{i}" for i in range(N_FILTERS)),
        *(f"b2b_{prev}_{cur}" for prev in ("acc", "rej") for cur in ("acc", "rej")),
        "gap_header",
        "gap_payload",
        "tuser_noise_ok",
    ]


@cocotb.test(timeout_time=200, timeout_unit="ms")
async def tc_random_golden(dut):
    """05 F2 "done": 10^4 random_traffic() frames match the model exactly. Two random configs:
    broadcast accepted, then not accepted with one entry disabled. Random tvalid gaps, IPG (half
    of them 0) and tuser noise. Ends with the coverage gate of 09 §2.12 (coverage.json)."""
    rng = random.Random(random.getrandbits(32))
    cfg_a = random_config(rng, N_FILTERS, accept_bcast=True)
    cfg_b = random_config(rng, N_FILTERS, accept_bcast=False)
    cfg_b = replace(cfg_b, filters=tuple((ip, p, i != 6) for i, (ip, p, _) in enumerate(cfg_b.filters)))
    rx = await start(dut, cfg_a)
    cov = Coverage("eth_ip_udp_rx.tc_random_golden", coverage_bins())
    prev_ok: bool | None = None
    for cfg in (cfg_a, cfg_b):
        for i, t in enumerate(random_traffic(rng, N_RANDOM // 2, cfg)):
            ipg = 0 if rng.random() < 0.5 else rng.randint(1, 4)
            await rx.idle(ipg)
            nbeats = math.ceil(len(t.frame) / 8)
            gaps = {k: rng.randint(1, 3) for k in rng.sample(range(1, nbeats), min(2, nbeats - 1))} if (
                nbeats > 1 and rng.random() < 0.3) else {}  # fmt: skip
            noise = rng.random() < 0.2
            rx.apply(replace(cfg, rx_en=t.rx_en))
            f = await rx.send(t.frame, tuser=t.tuser, intent=t.intent, gaps=gaps, noise=noise, name=f"{t.category} #{i}")
            # The monitor has checked (or will check) the DUT against f.want; cover what was exercised.
            ok = f.want.reason == RxReason.OK
            cov.hit(f"reason_{f.want.reason.name}")
            if f.want.payload is not None:
                padded = len(t.frame) == 60 and len(f.want.payload) < 18
                kind = "abort" if f.want.aborted else "padded" if padded else "nopad"
                cov.hit(f"tail_{last_beat_payload_bytes(len(f.want.payload))}_{kind}")
            if ok:
                dst = t.frame[:6]
                cov.hit("mac_bcast" if dst == b"\xff" * 6 else "mac_mcast" if dst[0] & 1 else "mac_ucast")
                cov.hit(f"filter_idx_{f.want.meta.filter_idx}")
                if any(k <= 5 for k in gaps):
                    cov.hit("gap_header")
                if any(k >= 6 for k in gaps):
                    cov.hit("gap_payload")
                if noise and nbeats > 1:
                    cov.hit("tuser_noise_ok")
            if ipg == 0 and prev_ok is not None:
                cov.hit(f"b2b_{'acc' if prev_ok else 'rej'}_{'acc' if ok else 'rej'}")
            prev_ok = ok
    await rx.finish()
    cov.check(Path.cwd())  # the run directory


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def tc_line_rate_b2b(dut):
    """05 F2 "done", zero loss at line rate: 2000 frames of 60 B and 1514 B mixed, tvalid held
    high throughout (no gap, IPG 0). Every payload exact, output beats = sum(ceil(payload / 8)),
    one stat event per frame."""
    rx = await start(dut)
    beats = 0
    for i in range(N_LINE_RATE):
        n = 1472 if random.random() < 0.5 else random.randint(1, 18)  # 1514 B or 60 B frames
        await rx.send(pad_to(udp_frame(payload(n))), intent=RxReason.OK, name=f"frame {i} ({n} B payload)")
        beats += math.ceil(n / 8)
    await rx.finish()
    assert rx.stat_events == N_LINE_RATE, f"{rx.stat_events} stat events for {N_LINE_RATE} frames"
    assert rx.out_beats == beats, f"{rx.out_beats} output beats, expected {beats}"


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def tc_k4_long_packet_no_spurious_event(dut):
    """Legacy K4 (a 6-bit word counter wrapped every 512 B): (1) a 9000-B frame, filtered,
    carrying a complete accepted frame (headers and an 'A') at frame offsets 512, 1024 and 1536:
    no output at all, one FILTER event. It comes first, so a wrapping counter is caught by the
    legacy failure itself (checked without the SVA). (2) A 9000-B jumbo, accepted: payload
    exact. (3) A 17000-B frame, past the 2047-beat counter limit: the counter saturates
    (a_beat_cnt_saturates), payload and stat_bytes exact."""
    R = RxReason
    rx = await start(dut)
    inner = udp_frame(b"A" + payload(40))
    assert expect(inner, False, CFG).reason == R.OK, "the embedded frame must pass on its own"
    body = bytearray(payload(9000 - 42))
    for ofs in (512, 1024, 1536):
        body[ofs - 42 : ofs - 42 + len(inner)] = inner
    outer = with_field(udp_frame(bytes(body)), "dport", 5001)
    assert all(outer[ofs : ofs + len(inner)] == inner for ofs in (512, 1024, 1536))
    await rx.send(outer, intent=R.FILTER, name="filtered jumbo with embedded frames")
    await rx.send(udp_frame(payload(9000 - 42)), intent=R.OK, name="9000 B jumbo")
    await rx.send(udp_frame(payload(17000 - 42)), intent=R.OK, name="17000 B frame")
    await rx.finish()


# The long tests run as their own pytest entries (parallel workers, one shared build); GROUP
# only gives each entry its own run directory.
LONG_TESTS = ("tc_random_golden", "tc_line_rate_b2b", "tc_k4_long_packet_no_spurious_event")
DIRECTED_TESTS = [name for name in dict(globals()) if name.startswith("tc_") and name not in LONG_TESTS]


@pytest.mark.parametrize("group", ["directed", *LONG_TESTS])
def test_eth_ip_udp_rx(group):
    run_cocotb(
        toplevel="eth_ip_udp_rx",
        filelists=["common.f", "core.f"],
        test_module="test_eth_ip_udp_rx",
        parameters={"N_FILTERS": N_FILTERS},
        testcase=DIRECTED_TESTS if group == "directed" else group,
        extra_env={"GROUP": group},
    )
