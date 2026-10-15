"""eth_ip_udp_rx: Ethernet/IPv4/UDP receive filter (audit/09).

Every frame is checked against the golden model (model/net.py): exactly one stat event
per frame with the model's reason and byte count and, for frames whose headers pass,
the m_udp_* metadata. Metadata is sampled at the stat event: it holds the frame's
values from stage B until the next frame's stage B. The payload path arrives in F2 S3;
until then the output must stay silent (PAYLOAD_PATH).
"""

import random
from collections import deque
from collections.abc import Callable
from dataclasses import replace

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

from common.clocks import start_clock
from common.runner import run_cocotb
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
    udp_frame,
    with_field,
    with_ip_options,
    with_vlan,
)

PERIOD_PS = 6400  # net_clk, 156.25 MHz
N_FILTERS = 8
PAYLOAD_PATH = False  # F2 S3: m_axis_udp_* is compared with the model's payload
STAGE_A_BEAT = 4  # cfg_* is sampled together with beat 4 (09 §2.3)
LOCAL_MAC = "02:00:00:00:00:01"
CFG = RxConfig(local_mac=LOCAL_MAC, filters=(("10.0.0.2", 5000, True),))


class Rx:
    """Drives frames and cfg_*; checks every stat event (and the metadata) against the model."""

    def __init__(self, dut, cfg: RxConfig) -> None:
        self.dut = dut
        self.expected: deque[tuple[str, RxExpect]] = deque()
        self.sof_ts: deque[int] = deque()
        self.sent = 0
        self.events = 0
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
        self.dut.s_axis_rx_tvalid.value = 0
        for _ in range(cycles):
            await RisingEdge(self.dut.clk)

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
    ) -> None:
        """Present one frame, byte 0 = tdata[7:0], and expect the model's outcome under cfg
        (default: the config applied now). intent: the reason the test means to produce.
        gaps[k]: idle cycles before beat k. at_beat[k]: runs while beat k is presented, so
        the DUT sees its effect at the same edge as beat k. noise: random tuser on the
        beats before tlast (the MAC's tuser is only valid on the tlast beat)."""
        name = name or f"frame {self.sent}"
        want = expect(frame, tuser, cfg or self.cfg)
        if intent is not None:
            assert want.reason == intent, f"{name}: test frame gives {want.reason.name}, meant {intent.name}"
        self.expected.append((name, want))
        self.sent += 1
        dut = self.dut
        beats = [frame[i : i + 8] for i in range(0, len(frame), 8)]
        for k, beat in enumerate(beats):
            await self.idle((gaps or {}).get(k, 0))
            last = k == len(beats) - 1
            dut.s_axis_rx_tdata.value = int.from_bytes(beat.ljust(8, b"\0"), "little")
            dut.s_axis_rx_tkeep.value = (1 << len(beat)) - 1
            dut.s_axis_rx_tlast.value = int(last)
            dut.s_axis_rx_tuser.value = int(tuser) if last else int(noise and random.random() < 0.5)
            dut.s_axis_rx_tvalid.value = 1
            if at_beat and k in at_beat:
                at_beat[k]()
            await RisingEdge(dut.clk)
        dut.s_axis_rx_tvalid.value = 0

    async def finish(self, cycles: int = 8) -> None:
        """Let the last events come out; every frame must have produced its stat event."""
        await self.idle(cycles)
        head = self.expected[0][0] if self.expected else ""
        assert not self.expected, f"{len(self.expected)} frame(s) without a stat event (first: {head})"
        assert self.events == self.sent

    async def _ts(self) -> None:
        ts = random.getrandbits(63)
        while True:
            self.dut.ts_now.value = ts
            await RisingEdge(self.dut.clk)
            ts += 1

    async def _monitor(self) -> None:
        dut = self.dut
        in_frame = False
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            assert dut.s_axis_rx_tready.value == 1, "RX must never backpressure the MAC"
            if dut.stat_valid.value:
                self._check_stat()
            if not PAYLOAD_PATH:
                assert dut.m_axis_udp_tvalid.value == 0, "payload output before the payload path exists"
            if dut.rst.value:
                in_frame = False
            elif dut.s_axis_rx_tvalid.value:
                if not in_frame:
                    self.sof_ts.append(int(dut.ts_now.value))  # sampled by the DUT at the SOF edge
                in_frame = not dut.s_axis_rx_tlast.value

    def _check_stat(self) -> None:
        dut = self.dut
        assert self.expected, "stat event with no frame outstanding"
        name, want = self.expected.popleft()
        ts = self.sof_ts.popleft()
        self.events += 1
        reason, nbytes = int(dut.stat_reason.value), int(dut.stat_bytes.value)
        got = RxReason(reason).name if reason in RxReason._value2member_map_ else str(reason)
        assert (reason, nbytes) == (want.reason, want.nbytes), (
            f"{name}: stat ({got}, {nbytes} B), model ({want.reason.name}, {want.nbytes} B)"
        )
        if want.meta is not None:
            meta = RxMeta(
                int(dut.m_udp_src_ip.value),
                int(dut.m_udp_dst_ip.value),
                int(dut.m_udp_dst_port.value),
                int(dut.m_udp_len.value),
                int(dut.m_udp_filter_idx.value),
            )
            assert meta == want.meta, f"{name}: metadata {meta}, model {want.meta}"
            assert int(dut.m_udp_sof_ts.value) == ts, f"{name}: m_udp_sof_ts {int(dut.m_udp_sof_ts.value):#x}, ts_now at the SOF beat {ts:#x}"


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
    cocotb.start_soon(rx._ts())
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
    The mixed packets (A, P2) and (B, P1) are never accepted; the control packets follow
    exactly the config of the stage-A cycle (beat 4)."""
    R = RxReason
    a, b, p1, p2 = "10.0.0.2", "10.0.0.3", 5000, 6000
    old = RxConfig(local_mac=LOCAL_MAC, filters=((a, p1, True),))
    new = replace(old, filters=((b, p2, True),))
    rx = await start(dut, old)
    for k in range(9):
        seen = new if k <= STAGE_A_BEAT else old
        for ip, port in [(a, p2), (b, p1), (a, p1), (b, p2)]:
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


def test_eth_ip_udp_rx():
    run_cocotb(
        toplevel="eth_ip_udp_rx",
        filelists=["common.f", "core.f"],
        test_module="test_eth_ip_udp_rx",
        parameters={"N_FILTERS": N_FILTERS},
    )
