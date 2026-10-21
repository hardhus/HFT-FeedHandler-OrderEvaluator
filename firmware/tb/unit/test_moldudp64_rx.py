"""moldudp64_rx: MoldUDP64 de-framer and sequence tracking (audit/11).

Every packet is checked against the golden model (model/mold.py), in order, with the feed
state carried from packet to packet as the RTL carries it:
- the messages on m_msg_*: sequence number, length, the model's bytes, zero bytes after the
  length, m_msg_sof_ts of their packet;
- exactly one packet event per input packet, after its messages: status, gap, message, dup
  and oversize counts, synced_now, the header's seq/count (packets of at least MOLD_HDR_LEN
  bytes);
- feed_expected_seq/feed_synced/feed_eos in every cycle, against a timeline of the model's
  states: a packet's update (with the resyncs waiting on it) is visible 2 cycles after its
  tlast, a resync on a first beat or between packets 1 cycle after the pulse, a reset 1 cycle
  after it.
Latency is checked with exact equality on every message and every packet event: 2 cycles from
the input beat that holds the message's last byte, 3 cycles from the input tlast.

Resync (a double-buffered control, 11 S4 notes): a pulse on a packet's first beat applies before
that packet; one in a later beat waits and applies after the packet, the last one if several
wait; one in the cycle after tlast applies after those. Mold.resync() pulses between packets,
once every event is out.
"""

import random
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path

import cocotb
import pytest
from cocotb.triggers import ReadOnly, RisingEdge

from common.clocks import start_clock
from common.coverage import Coverage
from common.runner import run_cocotb
from model.mold import (
    HDR_LEN,
    ITCH50_LEN,
    MAX_MSG,
    MIN_LEN,
    SEQ_MOD,
    FeedConfig,
    FeedState,
    MoldResult,
    MoldStatus,
    eos,
    expect,
    feed_categories,
    heartbeat,
    itch_like,
    pack,
    random_feed,
)
from model.mold import resync as model_resync

PERIOD_PS = 6400  # net_clk, 156.25 MHz
MSG_LATENCY = 2  # input beat with a message's last byte -> m_msg_valid (11 §2.5)
PKT_LATENCY = 3  # input tlast -> m_pkt_valid
SESSION = b"SESSION001"
CFG = FeedConfig(enable=True, session_check=True, session=SESSION, filter_mask=0x01)
UDP_PAYLOAD_MAX = 65535 - 28  # largest UDP payload eth_ip_udp_rx delivers (ip_len 65535)
S = MoldStatus


def block_ends(payload: bytes) -> dict[int, int]:
    """Block index -> payload offset of its last byte, for every complete block that the framer
    reaches (it stops at a block shorter than MIN_LEN). Written for latency, independently of
    the model."""
    if len(payload) < HDR_LEN:
        return {}
    count = int.from_bytes(payload[18:20], "big")
    ends, pos = {}, HDR_LEN
    for i in range(0 if count in (0, 0xFFFF) else count):
        if pos + 2 > len(payload):
            break
        n = int.from_bytes(payload[pos : pos + 2], "big")
        if n < MIN_LEN or pos + 2 + n > len(payload):
            break
        ends[i] = pos + 1 + n
        pos += 2 + n
    return ends


Resync = tuple[int, bool, int]  # (beat index, learn, seq); beat = number of beats: after tlast


@dataclass
class Pkt:
    """One input packet: the model's outcome and what the monitor saw (cycle numbers count
    rising edges, as in test_eth_ip_udp_rx)."""

    name: str
    payload: bytes
    ts: int
    want: MoldResult
    before: FeedState  # feed state the packet is checked against (after a first-beat resync)
    after: FeedState  # feed state after the packet (and the resyncs waiting on it)
    abort: bool = False
    fidx: int = 0
    cut: bool = False  # cut by a reset: no event, a prefix of the messages
    msgs: deque = field(default_factory=deque)  # messages still expected
    beats: list[int] = field(default_factory=list)  # input cycle of each beat
    msg_latency: list[int] = field(default_factory=list)
    pkt_latency: int = -1
    pre: bool = False  # a resync on the first beat


class Mold:
    """Drives packets the way eth_ip_udp_rx delivers them and the feed configuration the way
    hft_csr_wrap does; checks every message and packet event against the model."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.cycle = 0
        self.cfg = CFG
        self.state = FeedState()  # model feed state after the last registered packet
        self.awaiting: deque[Pkt] = deque()  # sent, first beat not seen yet
        self.in_flight: Pkt | None = None
        self.exp = FeedState()  # model feed state now visible on feed_*
        self.timeline: list[tuple[int, FeedState]] = []  # (cycle it becomes visible, state)
        self.between: deque[FeedState] = deque()  # states after Mold.resync() pulses
        self.out_q: deque[Pkt] = deque()  # packets whose messages or event are outstanding
        self.n_msgs = 0
        self.lanes: set[tuple[int, int]] = set()  # (start lane, end lane) of output messages
        self.s_tdata, self.s_tkeep = dut.s_axis_udp_tdata, dut.s_axis_udp_tkeep
        self.s_tvalid, self.s_tlast, self.s_tuser = dut.s_axis_udp_tvalid, dut.s_axis_udp_tlast, dut.s_axis_udp_tuser
        self.s_len, self.s_fidx, self.s_ts = dut.s_udp_len, dut.s_udp_filter_idx, dut.s_udp_sof_ts
        self.m_valid, self.m_data, self.m_len = dut.m_msg_valid, dut.m_msg_data, dut.m_msg_len
        self.m_seq, self.m_ts = dut.m_msg_seq, dut.m_msg_sof_ts
        self.p_valid, self.p_status, self.p_seq = dut.m_pkt_valid, dut.m_pkt_status, dut.m_pkt_seq
        self.p_count, self.p_gap, self.p_msgs = dut.m_pkt_count, dut.m_pkt_gap, dut.m_pkt_msgs
        self.p_dups, self.p_over, self.p_synced_now = dut.m_pkt_dup_msgs, dut.m_pkt_oversize, dut.m_pkt_synced_now
        self.drive_cfg(self.cfg)

    # ---- configuration
    def drive_cfg(self, cfg: FeedConfig) -> None:
        self.dut.cfg_feed_en.value = int(cfg.enable)
        self.dut.cfg_session_check.value = int(cfg.session_check)
        self.dut.cfg_session.value = int.from_bytes(cfg.session, "big")  # byte 0 = [79:72]
        self.dut.cfg_filter_mask.value = cfg.filter_mask

    def set_cfg(self, **changes) -> None:
        """New configuration from the next packet's first beat on (call between packets)."""
        self.cfg = replace(self.cfg, **changes)
        self.drive_cfg(self.cfg)

    def pulse(self, req: tuple[bool, int] | None) -> None:
        """cfg_resync in this cycle (learn, seq), or not (learn/seq random: ignored)."""
        self.dut.cfg_resync.value = int(req is not None)
        self.dut.cfg_resync_learn.value = int(req[0]) if req else random.getrandbits(1)
        self.dut.cfg_resync_seq.value = req[1] % SEQ_MOD if req else random.getrandbits(64)

    def next_seq(self) -> int:
        """The model's expected sequence number: a packet starting here is in order."""
        return self.state.expected

    def my_fidx(self) -> int:
        """The lowest filter entry of this feed."""
        mask = self.cfg.filter_mask
        return (mask & -mask).bit_length() - 1

    # ---- input
    def garbage(self) -> None:
        """Idle input: tvalid low, everything else random (the DUT must not look at it)."""
        self.s_tvalid.value = 0
        self.s_tdata.value = random.getrandbits(64)
        self.s_tkeep.value = random.getrandbits(8)
        self.s_tlast.value = random.getrandbits(1)
        self.s_tuser.value = random.getrandbits(1)
        self.s_len.value = random.getrandbits(16)
        self.s_fidx.value = random.getrandbits(3)
        self.s_ts.value = random.getrandbits(64)
        self.pulse(None)

    async def idle(self, cycles: int) -> None:
        for _ in range(cycles):
            self.garbage()
            await RisingEdge(self.dut.clk)

    async def drain(self) -> None:
        """Idle until every sent packet's event is out."""
        while self.awaiting or self.in_flight or self.out_q:
            await self.idle(1)

    async def resync(self, learn: bool, seq: int = 0) -> None:
        """A resync pulse between packets (applied at once)."""
        await self.drain()
        self.state = model_resync(self.state, learn, seq)
        self.between.append(self.state)
        self.garbage()
        self.pulse((learn, seq))
        await RisingEdge(self.dut.clk)
        self.garbage()

    def register(
        self, payload: bytes, name: str, intent: MoldStatus | None, abort: bool = False,
        fidx: int | None = None, resyncs: tuple[Resync, ...] = (), cut: bool = False,
    ) -> Pkt:  # fmt: skip
        fidx = self.my_fidx() if fidx is None else fidx
        nbeats = (len(payload) + 7) // 8
        before = self.state
        for _, learn, seq in (r for r in resyncs if r[0] == 0):  # first beat: before the packet
            before = model_resync(before, learn, seq)
        want, after = expect(payload, abort, fidx, self.cfg, before)
        assert intent is None or want.status == intent, f"{name}: test packet gives {want.status.name}, meant {intent.name}"
        waiting = [r for r in resyncs if 0 < r[0] < nbeats]
        for _, learn, seq in waiting[-1:] + [r for r in resyncs if r[0] == nbeats]:  # the last waiting one wins
            after = model_resync(after, learn, seq)
        self.state = after
        p = Pkt(name, payload, random.getrandbits(64), want, before, after, abort, fidx, cut, deque(want.msgs))
        p.pre = any(r[0] == 0 for r in resyncs)
        self.awaiting.append(p)
        self.out_q.append(p)
        return p

    async def send(
        self, payload: bytes, *, intent: MoldStatus | None = None, gaps: dict[int, int] | None = None, name: str = "",
        abort: bool = False, fidx: int | None = None, udp_len: int | None = None,
        resync: tuple[Resync, ...] = (), cfg_mid: FeedConfig | None = None,
    ) -> Pkt:  # fmt: skip
        """Present one packet, byte 0 = tdata[7:0]; gaps[k]: idle cycles before beat k. abort:
        tuser on tlast; udp_len: s_udp_len when it is not the bytes presented (a frame cut by
        F2); cfg_mid: configuration driven from beat 1 on (sampling check)."""
        p = self.register(payload, name or f"packet {self.cycle}", intent, abort, fidx, resync)
        await self._drive(p, gaps or {}, len(payload), udp_len=udp_len, resync=resync, cfg_mid=cfg_mid)
        return p

    async def reset(self) -> None:
        """A 1-cycle reset between packets, once every event is out."""
        await self.drain()
        self.dut.rst.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.rst.value = 0
        self.state = FeedState()

    async def send_cut(self, payload: bytes, beats: int, name: str) -> Pkt:
        """Present the first `beats` beats, then a 1-cycle reset (the source is reset with us)."""
        p = self.register(payload, name, None, cut=True)
        await self._drive(p, {}, 8 * beats, last=False)
        self.garbage()
        self.dut.rst.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.rst.value = 0
        self.state = FeedState()
        return p

    async def _drive(
        self, p: Pkt, gaps: dict[int, int], nbytes: int, last: bool = True, udp_len: int | None = None,
        resync: tuple[Resync, ...] = (), cfg_mid: FeedConfig | None = None,
    ) -> None:  # fmt: skip
        data = p.payload[:nbytes]
        beats = [data[i : i + 8] for i in range(0, len(data), 8)]
        pulses: dict[int, list[tuple[bool, int]]] = {}
        for k, learn, seq in resync:
            pulses.setdefault(k, []).append((learn, seq))
        assert all(len(v) == 1 for v in pulses.values()) and all(k <= len(beats) for k in pulses)
        for k, beat in enumerate(beats):
            if gaps.get(k):
                for _ in range(gaps[k]):  # a gap inside the packet: s_udp_* stay held
                    self.s_tvalid.value = 0
                    self.s_tdata.value = random.getrandbits(64)
                    self.pulse(None)
                    await RisingEdge(self.dut.clk)
            end = last and k == len(beats) - 1
            self.s_tdata.value = int.from_bytes(beat.ljust(8, bytes([random.getrandbits(8)])), "little")
            self.s_tkeep.value = (1 << len(beat)) - 1 if k == len(beats) - 1 else 0xFF
            self.s_tlast.value = int(end)
            self.s_tuser.value = int(p.abort) if end else random.getrandbits(1)
            self.s_len.value = len(p.payload) if udp_len is None else udp_len
            self.s_fidx.value = p.fidx
            self.s_ts.value = p.ts
            self.s_tvalid.value = 1
            self.pulse(pulses[k][0] if k in pulses else None)
            if cfg_mid is not None and k == 1:
                self.drive_cfg(cfg_mid)
            await RisingEdge(self.dut.clk)
        self.garbage()
        if cfg_mid is not None:
            self.drive_cfg(self.cfg)
        if len(beats) in pulses:  # the cycle after tlast
            self.pulse(pulses[len(beats)][0])
            await RisingEdge(self.dut.clk)
            self.garbage()

    async def finish(self, cycles: int = 8) -> None:
        await self.idle(cycles)
        assert not self.awaiting, f"{len(self.awaiting)} packet(s) never presented"
        assert not self.out_q, f"{self.out_q[0].name}: no packet event ({len(self.out_q[0].msgs)} message(s) missing)"

    # ---- output
    def feed(self) -> FeedState:
        d = self.dut
        return FeedState(int(d.feed_expected_seq.value), bool(d.feed_synced.value), bool(d.feed_eos.value))

    async def feed_settled(self) -> FeedState:
        """feed_* one idle cycle later: an update at the last rising edge is visible, and the
        driver stays in its phase (it writes right after a rising edge)."""
        await self.idle(1)
        return self.feed()

    async def monitor(self) -> None:
        clk, rst = self.dut.clk, self.dut.rst
        while True:
            await RisingEdge(clk)
            await ReadOnly()
            self.cycle += 1
            if self.m_valid.value:
                self._check_msg()
            if self.p_valid.value:
                self._check_pkt()
            self._check_feed(bool(rst.value))
            if rst.value:
                self.in_flight = None
                self.timeline = [(self.cycle + 1, FeedState())]
                if self.out_q and self.out_q[0].cut:  # its remaining messages are lost
                    self.out_q.popleft()
                assert not any(p.cut for p in self.out_q), "a cut packet behind an outstanding one"
            elif self.s_tvalid.value:
                if self.in_flight is None:
                    assert self.awaiting, "input packet that the test did not register"
                    self.in_flight = self.awaiting.popleft()
                    if self.in_flight.pre:
                        assert self.dut.cfg_resync.value, f"{self.in_flight.name}: first-beat resync not driven"
                        self.timeline.append((self.cycle + 1, self.in_flight.before))
                self.in_flight.beats.append(self.cycle)
                if self.s_tlast.value:
                    self.timeline.append((self.cycle + 2, self.in_flight.after))
                    self.in_flight = None
            elif self.dut.cfg_resync.value and not self.out_q:  # Mold.resync(), between packets
                self.timeline.append((self.cycle + 1, self.between.popleft()))

    def _check_feed(self, in_reset: bool) -> None:
        """feed_* against the model state visible in this cycle (not while reset is applied)."""
        while self.timeline and self.timeline[0][0] <= self.cycle:
            self.exp = self.timeline.pop(0)[1]
        if not in_reset:
            assert self.feed() == self.exp, f"cycle {self.cycle}: feed state {self.feed()}, model {self.exp}"

    def _check_msg(self) -> None:
        assert self.out_q, "message with no packet outstanding"
        p = self.out_q[0]
        assert p.msgs, f"{p.name}: message after the last expected one (seq {int(self.m_seq.value)})"
        seq, data = p.msgs.popleft()
        got_seq, n = int(self.m_seq.value), int(self.m_len.value)
        vec = int(self.m_data.value).to_bytes(MAX_MSG, "little")
        assert got_seq == seq, f"{p.name}: message seq {got_seq}, model {seq}"
        assert n == len(data), f"{p.name}: seq {seq} length {n}, model {len(data)}"
        if vec[:n] != data:
            bad = next(i for i in range(n) if vec[i] != data[i])
            raise AssertionError(f"{p.name}: seq {seq} ({n} B) differs from byte {bad}: {vec[:n].hex()} vs {data.hex()}")
        assert not any(vec[n:]), f"{p.name}: seq {seq}: bytes after the length are not 0: {vec[n:].hex()}"
        assert int(self.m_ts.value) == p.ts, f"{p.name}: seq {seq} m_msg_sof_ts is not its packet's"
        index = (seq - int.from_bytes(p.payload[10:18], "big")) % SEQ_MOD
        end = block_ends(p.payload)[index]
        latency = self.cycle - p.beats[end // 8]
        p.msg_latency.append(latency)
        assert latency == MSG_LATENCY, f"{p.name}: seq {seq} out {latency} cycles after its last byte"
        self.lanes.add(((end + 1 - n) % 8, end % 8))
        self.n_msgs += 1

    def _check_pkt(self) -> None:
        assert self.out_q, "packet event with no packet outstanding"
        p = self.out_q.popleft()
        assert not p.cut, f"{p.name}: event for a packet cut by reset"
        assert not p.msgs, f"{p.name}: packet event before {len(p.msgs)} message(s)"
        status = int(self.p_status.value)
        name = S(status).name if status in S._value2member_map_ else str(status)
        w = p.want
        assert status == w.status, f"{p.name}: status {name}, model {w.status.name}"
        got = (int(self.p_gap.value), int(self.p_msgs.value), int(self.p_dups.value), int(self.p_over.value))
        want = (w.gap, len(w.msgs), w.dup_msgs, w.oversize)
        assert got == want, f"{p.name}: (gap, msgs, dup, oversize) {got}, model {want}"
        assert bool(self.p_synced_now.value) == w.synced_now, f"{p.name}: synced_now {int(self.p_synced_now.value)}"
        if len(p.payload) >= HDR_LEN:
            hdr = (int.from_bytes(p.payload[10:18], "big"), int.from_bytes(p.payload[18:20], "big"))
            assert (int(self.p_seq.value), int(self.p_count.value)) == hdr, f"{p.name}: header seq/count"
        p.pkt_latency = self.cycle - p.beats[-1]
        assert p.pkt_latency == PKT_LATENCY, f"{p.name}: packet event {p.pkt_latency} cycles after tlast"


async def start(dut) -> Mold:
    """Idle inputs and reset first, then the clock."""
    m = Mold(dut)
    m.garbage()
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    await RisingEdge(dut.clk)
    cocotb.start_soon(m.monitor())
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    return m


def msgs_of(*lengths: int) -> list[bytes]:
    return [random.randbytes(n) for n in lengths]


def itch(n: int) -> list[bytes]:
    return [itch_like(random, random.choice(list(ITCH50_LEN))) for _ in range(n)]


def fill_to_lane(start: int) -> int:
    """Length of a first message that puts the next block's length field at lane `start`
    (the first block starts at payload byte 20 = lane 4)."""
    return MIN_LEN + (start - (HDR_LEN + 2) - MIN_LEN) % 8 + 8 * random.randint(0, 6)


def len_to_lane(start: int, end: int) -> int:
    """Length of a message whose block starts at lane `start` and whose last byte is in lane `end`."""
    return MIN_LEN + (end - start - 1 - MIN_LEN) % 8 + 8 * random.randint(0, 6)


def seq() -> int:
    return random.getrandbits(64)


# Hand-written packets (from the MoldUDP64 and ITCH 5.0 tables; seq, count and lengths by hand).
def hexbytes(text: str) -> bytes:
    return bytes.fromhex("".join(line.split("#")[0] for line in text.splitlines()).replace(" ", ""))


HDR_HEX = "53455353494f4e303031"  # "SESSION001"
MSG_S = hexbytes("53 0000 0000 000000000001 4f")  # System Event (12 B)
MSG_D = hexbytes("44 0001 0000 000000000002 0000000000000007")  # Order Delete (19 B)
PKT = hexbytes(f"{HDR_HEX} 0000000000000064 0002  000c {MSG_S.hex()}  0013 {MSG_D.hex()}")


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_hand_packets(dut):
    """Hand-written packets: two messages (seq 100, 101), heartbeat and end of session (count 0
    and 0xFFFF, 20 B), an oversize block in the middle (seq 101 skipped), a 5-B block, a byte
    after the last block, the last message cut by one byte. A learn resync on each packet's
    first beat starts it unsynchronized, so every packet outputs all its complete blocks."""
    m = await start(dut)
    learn = ((0, True, 0),)
    cases = [
        ("two messages", PKT, S.OK),
        ("heartbeat", hexbytes(f"{HDR_HEX} 0000000000000066 0000"), S.HEARTBEAT),
        ("end of session", hexbytes(f"{HDR_HEX} 0000000000000066 ffff"), S.EOS),
        ("oversize in the middle",
         hexbytes(f"{HDR_HEX} 0000000000000064 0003  000c {MSG_S.hex()}  0041 {'00' * 65}  0013 {MSG_D.hex()}"), S.OK),
        ("5-B block", hexbytes(f"{HDR_HEX} 0000000000000064 0002  000c {MSG_S.hex()}  0005 4142434445"), S.UNSUPPORTED),
        ("trailing byte", PKT + b"\x00", S.MALFORMED),
        ("last message cut", PKT[:-1], S.MALFORMED),
    ]  # fmt: skip
    for name, payload, intent in cases:
        await m.send(payload, intent=intent, name=name, resync=learn)
        await m.idle(2)
    for name, payload, intent in cases:  # again back to back (IPG 0)
        await m.send(payload, intent=intent, name=f"{name}, IPG 0", resync=learn)
    await m.finish()
    assert m.n_msgs == 2 * (2 + 2 + 1 + 2 + 1), f"{m.n_msgs} messages"


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_alignment_matrix(dut):
    """Every block start lane (7: the length field is split across two beats) x every end lane
    of its message; every message length 6..64 nine times in a row (so it starts in every lane
    its block size allows); every ITCH 5.0 type. With and without IPG and tvalid gaps."""
    m = await start(dut)
    for s in range(8):
        for e in range(8):
            msgs = msgs_of(fill_to_lane(s), len_to_lane(s, e)) + itch(random.randint(0, 2))
            gaps = {random.randint(1, 3): random.randint(1, 2)} if (s + e) % 3 == 0 else {}
            await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, gaps=gaps, name=f"start lane {s}, end lane {e}")
            await m.idle((s * 8 + e) % 3)
    for n in range(MIN_LEN, MAX_MSG + 1):
        await m.send(pack(SESSION, m.next_seq(), msgs_of(*[n] * 9)), intent=S.OK, name=f"9 x {n} B")
    for i in range(4):
        types = list(ITCH50_LEN)
        random.shuffle(types)
        await m.send(pack(SESSION, m.next_seq(), [itch_like(random, t) for t in types]), intent=S.OK, name=f"all ITCH types {i}")
    await m.finish()
    assert m.lanes >= {(s, e) for s in range(8) for e in range(8)}, "a (start lane, end lane) pair was not exercised"


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_oversize(dut):
    """Blocks longer than MAX_MSG are skipped and counted, and their seq is consumed: 64 B (the
    limit, output) and 65 B at every start lane with a message after it, 1000 and 1400 B, several
    in one packet, oversize as the last block, and the largest block a UDP payload can carry.
    A length field of 0xFFFF that does not fit is MALFORMED (largest framer offset)."""
    m = await start(dut)
    for s in range(8):
        for n in (MAX_MSG, MAX_MSG + 1):
            msgs = msgs_of(fill_to_lane(s), n) + itch(2)
            await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, name=f"{n} B at lane {s}")
    await m.send(pack(SESSION, m.next_seq(), itch(1) + msgs_of(1000) + itch(3)), intent=S.OK, name="1000 B")
    await m.send(pack(SESSION, m.next_seq(), msgs_of(1400) + itch(1)), intent=S.OK, name="1400 B")
    await m.send(pack(SESSION, m.next_seq(), msgs_of(65, 12, 100, 300, 64, 66)), intent=S.OK, name="several")
    await m.send(pack(SESSION, m.next_seq(), itch(2) + msgs_of(200)), intent=S.OK, name="oversize last")
    largest = UDP_PAYLOAD_MAX - HDR_LEN - 2
    await m.send(pack(SESSION, m.next_seq(), msgs_of(largest)), intent=S.OK, name=f"{largest} B, the largest")
    await m.send(pack(SESSION, m.next_seq(), msgs_of(12, largest - 14)), intent=S.OK, name="largest after a message")
    cut = pack(SESSION, m.next_seq(), itch(1) + msgs_of(0xFFFF))[:UDP_PAYLOAD_MAX]
    await m.send(cut, intent=S.MALFORMED, name="length 0xFFFF, cut at the largest payload")
    await m.send(pack(SESSION, m.next_seq(), itch(3)), intent=S.OK, name="after the largest")
    await m.finish()


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def tc_unsupported(dut):
    """A block of length 0..5 (legal MoldUDP64, not supported at line rate) makes the packet
    UNSUPPORTED; the messages before it come out, nothing after it. As the first block, after a
    message ending in every lane (so its length field starts in every lane, split at lane 7),
    followed by more blocks, and as the last bytes. Length 6 in the same places is accepted."""
    m = await start(dut)
    for n in range(MIN_LEN):  # an oversize block before it is not reported
        payload = pack(SESSION, m.next_seq(), msgs_of(200) + itch(1) + msgs_of(n))
        await m.send(payload, intent=S.UNSUPPORTED, name=f"{n} B after an oversize block")
    for n in range(MIN_LEN + 1):
        intent = S.OK if n == MIN_LEN else S.UNSUPPORTED
        await m.send(pack(SESSION, m.next_seq(), msgs_of(n)), intent=intent, name=f"{n} B first")
        for s in range(8):
            msgs = msgs_of(fill_to_lane(s), n) + itch(2)
            await m.send(pack(SESSION, m.next_seq(), msgs), intent=intent, name=f"{n} B at lane {s}, then more")
            await m.send(pack(SESSION, m.next_seq(), msgs[:2]), intent=intent, name=f"{n} B at lane {s}, last")
        if n:  # the length field is complete, the data is not
            cut = pack(SESSION, m.next_seq(), itch(1) + msgs_of(n))[:-1]
            await m.send(cut, intent=S.UNSUPPORTED if n < MIN_LEN else S.MALFORMED, name=f"{n} B, data cut")
    await m.finish()


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def tc_malformed(dut):
    """Framing inconsistent with the count: bytes after the last block (1, 7, 8, 9, 20, with the
    last message ending in every lane, and after a last block that starts and ends in the tlast
    beat); the payload ending before the count is complete (in a
    message, after one byte of a length field, split or not, and right where a length field
    should start; a count larger than the blocks); a heartbeat or end of session that is not
    20 B. The complete messages before the fault come out."""
    m = await start(dut)
    for e in range(8):
        good = pack(SESSION, m.next_seq(), msgs_of(fill_to_lane(3), len_to_lane(3, e)))
        assert block_ends(good)[1] % 8 == e
        for extra in (1, 7, 8, 9, 20):
            await m.send(good + random.randbytes(extra), intent=S.MALFORMED, name=f"end lane {e}, {extra} B after")
        await m.send(good, intent=S.OK, name=f"end lane {e}, exact")
        await m.send(good[:-1], intent=S.MALFORMED, name=f"end lane {e}, last byte missing")
    # The last block starts and ends in the tlast beat (length field in lanes 7 and 0, 6 B up to
    # lane 6), with one more byte in that beat.
    for _ in range(3):
        good = pack(SESSION, m.next_seq(), msgs_of(fill_to_lane(7), 6))
        assert block_ends(good)[1] % 8 == 6 and len(good) % 8 == 7
        await m.send(good + random.randbytes(1), intent=S.MALFORMED, name="6 B block in the tlast beat, 1 B after")
        await m.send(good, intent=S.OK, name="6 B block in the tlast beat, exact")
    for s in range(8):  # cut inside the length field at lane s, and right before it
        msgs = msgs_of(fill_to_lane(s), 30, 12)
        good = pack(SESSION, m.next_seq(), msgs)
        ofs = HDR_LEN + 2 + len(msgs[0])
        assert ofs % 8 == s
        await m.send(good[: ofs + 1], intent=S.MALFORMED, name=f"length field at lane {s}, one byte")
        await m.send(good[:ofs], intent=S.MALFORMED, name=f"length field at lane {s}, none")
        await m.send(good[: ofs + 2], intent=S.MALFORMED, name=f"length field at lane {s}, no data")
    # Oversize blocks before the fault are not reported (only accepted packets count them).
    over = pack(SESSION, m.next_seq(), msgs_of(100) + itch(1) + msgs_of(65))
    await m.send(over + b"\x00", intent=S.MALFORMED, name="two oversize, then a byte after the last block")
    await m.send(over[:-1], intent=S.MALFORMED, name="two oversize, the last one cut")
    p = pack(SESSION, m.next_seq(), itch(3))
    await m.send(p[:18] + (5).to_bytes(2, "big") + p[20:], intent=S.MALFORMED, name="count 5, 3 blocks")
    await m.send(p[:18] + (2).to_bytes(2, "big") + p[20:], intent=S.MALFORMED, name="count 2, 3 blocks")
    await m.send(pack(SESSION, m.next_seq(), itch(1))[:HDR_LEN], intent=S.MALFORMED, name="count 1, header only")
    for build in (heartbeat, eos):
        for extra in (1, 3, 4, 5, 8, 12, 30):
            await m.send(build(SESSION, m.next_seq()) + random.randbytes(extra), intent=S.MALFORMED, name=f"{build.__name__} + {extra} B")
    await m.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_runt(dut):
    """Payloads of 1..19 B are RUNT (prefixes of a packet and of a heartbeat); 20 B is a whole
    heartbeat or end of session (accepted) or a header whose count needs blocks (MALFORMED)."""
    m = await start(dut)
    full = pack(SESSION, m.next_seq(), itch(2))
    hb = heartbeat(SESSION, m.next_seq())
    for n in range(1, HDR_LEN):
        await m.send(full[:n], intent=S.RUNT, name=f"{n} B of a packet")
        await m.send(hb[:n], intent=S.RUNT, name=f"{n} B of a heartbeat")
    await m.send(hb, intent=S.HEARTBEAT, name="20 B heartbeat")
    await m.send(eos(SESSION, m.next_seq()), intent=S.EOS, name="20 B end of session")
    await m.send(full[:HDR_LEN], intent=S.MALFORMED, name="20 B, count 2")
    await m.send(full, intent=S.OK, name="after the runts")
    await m.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_latency(dut):
    """11 §2.5, exact (the monitor checks every message and event; these are the edge cases): a
    message that starts and ends in one beat; one ending in the tlast beat, at lane 0 and 7;
    tvalid gaps right before and after the beat that ends a message; back-to-back packets whose
    event shares cycles with the next packet's messages. Packet part: at IPG 0 each packet's
    feed update is in place for the next one's header beat (a heartbeat, the shortest accepted
    packet, then a gap; a copy right behind its original; a partial overlap whose first block,
    a duplicate, ends in beat 3, the earliest)."""
    m = await start(dut)
    cases = [
        ("starts and ends in one beat", msgs_of(fill_to_lane(0), 6, 7, 30), {}),
        ("ends in tlast beat, lane 0", msgs_of(len_to_lane(4, 0)), {}),
        ("ends in tlast beat, lane 7", msgs_of(len_to_lane(4, 7)), {}),
        ("gaps around message ends", msgs_of(6, 6, 6, 6, 6), {3: 2, 4: 1, 5: 3}),
    ]
    for name, msgs, gaps in cases:
        p = await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, gaps=gaps, name=name)
        await m.idle(6)
        assert p.msg_latency == [MSG_LATENCY] * len(p.want.msgs), f"{name}: {p.msg_latency}"
        assert p.pkt_latency == PKT_LATENCY, f"{name}: event {p.pkt_latency}"
    sent = [await m.send(pack(SESSION, m.next_seq(), msgs_of(6, 6)), intent=S.OK, name=f"b2b {i}") for i in range(6)]
    await m.send(heartbeat(SESSION, m.next_seq()), intent=S.HEARTBEAT, name="heartbeat, IPG 0")
    await m.send(heartbeat(SESSION, m.next_seq() + 3), intent=S.HEARTBEAT, name="heartbeat with gap 3, IPG 0")
    orig = pack(SESSION, m.next_seq(), msgs_of(6))
    await m.send(orig, intent=S.OK, name="1 message, IPG 0")
    await m.send(orig, intent=S.DUP, name="its copy, IPG 0")
    await m.send(pack(SESSION, m.next_seq() - 1, msgs_of(6, 6, 6)), intent=S.OK, name="first block a duplicate, IPG 0")
    await m.send(eos(SESSION, m.next_seq()), intent=S.EOS, name="end of session, IPG 0")
    await m.send(pack(SESSION, m.next_seq(), msgs_of(6)), intent=S.OK, name="after the end of session, IPG 0")
    await m.finish()
    assert all(p.msg_latency == [MSG_LATENCY] * 2 and p.pkt_latency == PKT_LATENCY for p in sent)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_reset_mid_packet(dut):
    """11 §2.5: a 1-cycle reset in the header (beats 1, 2), in a message, and in the beat after a
    message ended. The cut packet gives no event and at most a prefix of its messages; the
    packets after the reset come out exactly."""
    m = await start(dut)
    for cut in (1, 2, 3, 5, 9):
        await m.send(pack(SESSION, m.next_seq(), itch(3)), intent=S.OK, name=f"before the reset at beat {cut}")
        await m.idle(6)
        await m.send_cut(pack(SESSION, m.next_seq(), msgs_of(6, 6, 30, 40, 50)), cut, name=f"cut at beat {cut}")
        await m.send(pack(SESSION, m.next_seq(), itch(4)), intent=S.OK, name=f"first after the reset at beat {cut}")
        await m.send(heartbeat(SESSION, m.next_seq()), intent=S.HEARTBEAT, name=f"second after the reset at beat {cut}")
    await m.finish()


async def synced_start(dut) -> Mold:
    """Reset, then one accepted packet at a random seq: the feed is in sync."""
    m = await start(dut)
    await m.send(pack(SESSION, seq(), itch(2)), intent=S.OK, name="sync")
    return m


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_heartbeat_eos(dut):
    """11 §2.4: a heartbeat (count 0) and an end of session (count 0xFFFF) carry the next
    expected seq. In order: accepted, E unchanged; ahead: accepted with a gap, E = S; behind:
    DUP. The end of session sets feed_eos, a heartbeat keeps it, a normal packet clears it. Both
    synchronize an unsynchronized feed. Also back to back (IPG 0)."""
    m = await synced_start(dut)
    for ipg in (2, 0):
        await m.send(heartbeat(SESSION, m.next_seq()), intent=S.HEARTBEAT, name="heartbeat in order")
        await m.idle(ipg)
        await m.send(heartbeat(SESSION, m.next_seq() + 5), intent=S.HEARTBEAT, name="heartbeat 5 ahead")
        await m.idle(ipg)
        await m.send(heartbeat(SESSION, m.next_seq() - 1), intent=S.DUP, name="heartbeat 1 behind")
        await m.idle(ipg)
        await m.send(eos(SESSION, m.next_seq()), intent=S.EOS, name="end of session")
        await m.idle(ipg)
        await m.send(eos(SESSION, m.next_seq()), intent=S.EOS, name="end of session again")
        await m.idle(ipg)
        await m.send(heartbeat(SESSION, m.next_seq()), intent=S.HEARTBEAT, name="heartbeat after it")
        await m.idle(ipg)
        await m.send(eos(SESSION, m.next_seq() - 1), intent=S.DUP, name="end of session 1 behind")
        await m.idle(ipg)
        await m.send(eos(SESSION, m.next_seq() + 1000), intent=S.EOS, name="end of session 1000 ahead")
        await m.idle(ipg)
        await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.OK, name="normal packet after it")
        await m.idle(ipg)
    for build, intent in ((heartbeat, S.HEARTBEAT), (eos, S.EOS)):
        await m.resync(learn=True)
        await m.send(build(SESSION, seq()), intent=intent, name=f"{build.__name__} while not in sync")
        await m.send(pack(SESSION, m.next_seq(), itch(1)), intent=S.OK, name=f"in order after that {build.__name__}")
    await m.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_gap(dut):
    """A packet ahead of E by 1, 1000, 2^40 and 2^63 - 1 (the largest gap; 2^63 is behind) is
    accepted with that gap and E = S + C; every message comes out. With idle cycles and IPG 0."""
    m = await synced_start(dut)
    for ipg in (3, 0):
        for g in (1, 1000, 1 << 40, (1 << 63) - 1):
            await m.send(pack(SESSION, m.next_seq() + g, itch(3)), intent=S.OK, name=f"gap {g}")
            await m.idle(ipg)
            await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.OK, name=f"in order after gap {g}")
            await m.idle(ipg)
    await m.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_dup_full(dut):
    """A packet entirely below E (S + C <= E) is DUP: no message, dup_msgs = C, oversize blocks
    in it are not counted as oversize, E unchanged. The copy of the last packet (A/B line, right
    behind it at IPG 0 and later), S + C = E exactly (S + C = E + 1 is a partial overlap), 2^16
    and 2^40 behind, and S - E = -2^63 (the most negative)."""
    m = await synced_start(dut)
    for ipg in (0, 4):
        orig = pack(SESSION, m.next_seq(), itch(3))
        await m.send(orig, intent=S.OK, name="original")
        await m.idle(ipg)
        await m.send(orig, intent=S.DUP, name=f"copy, IPG {ipg}")
        await m.idle(ipg)
        await m.send(orig, intent=S.DUP, name=f"copy again, IPG {ipg}")
        await m.idle(ipg)
    for c in (1, 3):
        await m.send(pack(SESSION, m.next_seq() - c, itch(c)), intent=S.DUP, name=f"S + C = E, C = {c}")
        await m.send(pack(SESSION, m.next_seq() - c, itch(c + 1)), intent=S.OK, name=f"S + C = E + 1, C = {c + 1}")
    await m.send(pack(SESSION, m.next_seq() - 4, itch(1) + msgs_of(65, 300) + itch(1)), intent=S.DUP, name="with oversize")
    for back in (0xFFFF, 0x10000, 0x10001, 1 << 40, 1 << 63):
        await m.send(pack(SESSION, m.next_seq() - back, itch(3)), intent=S.DUP, name=f"{back} behind")
    await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.OK, name="in order after them")
    await m.finish()


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def tc_dup_partial(dut):
    """S < E < S + C: the first E - S messages are duplicates (not output, dup_msgs), the rest
    come out, status OK, E = S + C. Every split of a 5-message packet; oversize blocks before
    and after the split (only those after it count as oversize); a split at message 300 of 400
    (index above 8 bits); a first block that is a duplicate and ends in beat 3."""
    m = await synced_start(dut)
    for k in range(1, 5):
        await m.send(pack(SESSION, m.next_seq() - k, itch(5)), intent=S.OK, name=f"{k} of 5 behind")
    for k in range(1, 6):
        msgs = msgs_of(12, 65, 20, 100, 30, 66)
        await m.send(pack(SESSION, m.next_seq() - k, msgs), intent=S.OK, name=f"{k} of 6 behind, with oversize")
    await m.send(pack(SESSION, m.next_seq() - 300, msgs_of(*[6] * 400)), intent=S.OK, name="300 of 400 behind")
    for ipg in (0, 2):
        await m.send(pack(SESSION, m.next_seq() - 1, msgs_of(6, 6, 6)), intent=S.OK, name=f"1 of 3 behind, IPG {ipg}")
        await m.idle(ipg)
    await m.finish()


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def tc_abort_no_seq_update(dut):
    """tuser on tlast: ABORT; the messages are those the same bytes give without the abort (dup
    filter included), gap/dup/oversize 0, the feed state unchanged. In order, with a gap, a
    copy, a partial overlap, heartbeat, end of session, oversize, while not in sync (no sync),
    and frames that F2 cut short (fewer bytes than s_udp_len, also inside the header). ABORT
    wins over RUNT and SESSION, DISABLED and OTHER_FEED win over it."""
    m = await synced_start(dut)
    for ipg in (2, 0):
        p = pack(SESSION, m.next_seq(), itch(3))
        await m.send(p, intent=S.ABORT, abort=True, name="in order")
        await m.idle(ipg)
        await m.send(p, intent=S.OK, name="the same packet again: still in order")
        await m.idle(ipg)
        await m.send(p, intent=S.ABORT, abort=True, name="copy")
        await m.send(pack(SESSION, m.next_seq() + 7, itch(2)), intent=S.ABORT, abort=True, name="gap")
        await m.send(pack(SESSION, m.next_seq() - 2, itch(4)), intent=S.ABORT, abort=True, name="partial overlap")
        await m.send(pack(SESSION, m.next_seq(), itch(1) + msgs_of(100) + itch(1)), intent=S.ABORT, abort=True, name="oversize")
        await m.send(heartbeat(SESSION, m.next_seq() + 3), intent=S.ABORT, abort=True, name="heartbeat")
        await m.send(eos(SESSION, m.next_seq()), intent=S.ABORT, abort=True, name="end of session")
        await m.idle(ipg)
    full = pack(SESSION, m.next_seq(), itch(4))
    for n in (5, 12, 19, 20, 33, len(full) - 1):
        await m.send(full[:n], intent=S.ABORT, abort=True, udp_len=len(full), name=f"cut by F2 after {n} B")
    await m.send(heartbeat(SESSION, m.next_seq())[:15], intent=S.ABORT, abort=True, name="runt")
    await m.send(pack(b"OTHER00001", m.next_seq(), itch(2)), intent=S.ABORT, abort=True, name="wrong session")
    await m.resync(learn=True)
    await m.send(pack(SESSION, seq(), itch(2)), intent=S.ABORT, abort=True, name="not in sync")
    await m.send(pack(SESSION, seq(), itch(2)), intent=S.OK, name="the next one synchronizes")
    await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.OTHER_FEED, abort=True, fidx=1, name="other feed")
    m.set_cfg(enable=False)
    await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.DISABLED, abort=True, name="disabled")
    m.set_cfg(enable=True)
    await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.OK, name="in order at the end")
    await m.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_session(dut):
    """With the session check on, a session differing in any one of its 10 bytes (beat 0 and
    beat 1) is SESSION: no message, the feed state unchanged; RUNT wins over it. With the check
    off any session is accepted. cfg_session and the check are taken from the packet's first
    beat: a change from beat 1 on applies to the next packet."""
    m = await synced_start(dut)
    for b in range(10):
        wrong = bytearray(SESSION)
        wrong[b] ^= 1 << random.randrange(8)
        await m.send(pack(bytes(wrong), m.next_seq(), itch(3)), intent=S.SESSION, name=f"session byte {b} differs")
        await m.send(pack(SESSION, m.next_seq(), itch(1)), intent=S.OK, name=f"in order after byte {b}")
    await m.send(pack(b"XESSION001", m.next_seq(), itch(1))[:19], intent=S.RUNT, name="19 B, wrong session")
    await m.send(heartbeat(b"SESSION00X", m.next_seq()), intent=S.SESSION, name="heartbeat, wrong session")
    other = b"SESSION002"
    await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.OK, cfg_mid=replace(m.cfg, session=other), name="cfg_session changes in beat 1")
    await m.send(pack(other, m.next_seq(), itch(2)), intent=S.SESSION, cfg_mid=replace(m.cfg, session=other), name="the change is not taken")
    await m.send(pack(other, m.next_seq(), itch(2)), intent=S.SESSION, cfg_mid=replace(m.cfg, session_check=False), name="check off from beat 1")
    m.set_cfg(session=other)  # back to back: the next first beat takes it
    await m.send(pack(other, m.next_seq(), itch(2)), intent=S.OK, name="new session, IPG 0")
    await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.SESSION, name="old session, IPG 0")
    m.set_cfg(session_check=False)
    for s in (SESSION, other, random.randbytes(10)):
        await m.send(pack(s, m.next_seq(), itch(2)), intent=S.OK, name=f"check off, session {s!r}")
    await m.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_other_feed(dut):
    """cfg_filter_mask selects the filter entries of this feed (A and B line). A packet from an
    entry outside the mask is OTHER_FEED: no message, the feed state unchanged; RUNT and ABORT
    lose to it. The same packet on both lines: the second copy is DUP. The mask is taken from the
    packet's first beat."""
    m = await start(dut)
    m.set_cfg(filter_mask=0x21)  # entries 0 (A) and 5 (B)
    await m.send(pack(SESSION, seq(), itch(2)), intent=S.OK, fidx=5, name="sync on B")
    for i in range(8):
        mine = (0x21 >> i) & 1
        p = pack(SESSION, m.next_seq(), itch(2))
        await m.send(p, intent=S.OK if mine else S.OTHER_FEED, fidx=i, name=f"entry {i}")
        if mine:
            await m.send(p, intent=S.DUP, fidx=5 - i, name=f"copy from entry {5 - i}")
    await m.send(heartbeat(SESSION, m.next_seq())[:10], intent=S.OTHER_FEED, fidx=3, name="runt from entry 3")
    await m.send(pack(SESSION, m.next_seq(), itch(1)), intent=S.OK, fidx=0, cfg_mid=replace(m.cfg, filter_mask=0x02), name="mask changes in beat 1")
    await m.send(pack(SESSION, m.next_seq(), itch(1)), intent=S.OTHER_FEED, fidx=1, cfg_mid=replace(m.cfg, filter_mask=0x02), name="the change is not taken")
    m.set_cfg(filter_mask=0x02)
    await m.send(pack(SESSION, m.next_seq(), itch(1)), intent=S.OK, fidx=1, name="new mask, IPG 0")
    await m.send(pack(SESSION, m.next_seq(), itch(1)), intent=S.OTHER_FEED, fidx=0, name="old entry, IPG 0")
    await m.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_disabled(dut):
    """cfg_feed_en = 0: every packet is DISABLED (also runt, abort, wrong session, other entry):
    no message, the feed state unchanged. Enable is taken from the packet's first beat."""
    m = await synced_start(dut)
    m.set_cfg(enable=False)
    await m.send(pack(SESSION, m.next_seq(), itch(3)), intent=S.DISABLED, name="in order")
    await m.send(pack(SESSION, m.next_seq() + 9, itch(3)), intent=S.DISABLED, name="gap")
    await m.send(heartbeat(SESSION, m.next_seq())[:7], intent=S.DISABLED, name="runt")
    await m.send(pack(SESSION, m.next_seq(), itch(3)), intent=S.DISABLED, abort=True, name="abort")
    await m.send(pack(b"XXXXXXXXXX", m.next_seq(), itch(1)), intent=S.DISABLED, name="wrong session")
    await m.send(pack(SESSION, m.next_seq(), itch(1)), intent=S.DISABLED, fidx=4, name="other entry")
    await m.send(pack(SESSION, m.next_seq(), itch(1)), intent=S.DISABLED, cfg_mid=replace(m.cfg, enable=True), name="enabled from beat 1")
    m.set_cfg(enable=True)
    await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.OK, cfg_mid=replace(m.cfg, enable=False), name="disabled from beat 1")
    await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.OK, name="in order")
    m.set_cfg(enable=False)
    await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.DISABLED, name="disabled, IPG 0")
    m.set_cfg(enable=True)
    await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.OK, name="enabled, IPG 0")
    await m.finish()


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def tc_resync(dut):
    """cfg_resync: set (E = seq, in sync) or learn (out of sync: the next accepted packet
    synchronizes), applied like a double-buffered control register (11 S4 notes). Between
    packets and on a packet's first beat it applies at once, before that packet; in a later beat
    it waits for the packet's own update, and of several waiting requests the last one wins; in
    the cycle after tlast it applies after the waiting one. At IPG 0 the earlier packet's waiting
    request and the next packet's first-beat request meet in one cycle and act in that order. A
    resync clears feed_eos."""
    m = await synced_start(dut)
    x = seq()
    await m.resync(learn=False, seq=x)
    assert await m.feed_settled() == FeedState(x, True, False)
    await m.send(pack(SESSION, x + 5, itch(2)), intent=S.OK, name="5 ahead of the set seq")
    await m.resync(learn=False, seq=m.next_seq() + 10)
    await m.send(pack(SESSION, m.next_seq() - 10, itch(3)), intent=S.DUP, name="behind the set seq")
    await m.resync(learn=True)
    await m.send(pack(SESSION, seq(), itch(2))[:-1], intent=S.MALFORMED, name="learn: malformed does not sync")
    await m.send(pack(SESSION, seq(), itch(2)), intent=S.OK, name="learn: any seq syncs")
    msgs = msgs_of(10, 10, 10)  # 56 B: 7 beats
    for ipg in (0, 2):  # first beat: before the packet
        y = seq()
        await m.send(pack(SESSION, y, msgs), intent=S.OK, resync=((0, False, y),), name=f"set y on its first beat, at y, IPG {ipg}")
        await m.idle(ipg)
        await m.send(pack(SESSION, seq(), msgs), intent=S.OK, resync=((0, True, 0),), name=f"learn on its first beat: syncs, IPG {ipg}")
        await m.idle(ipg)
    for beat in (1, 2, 3, 6, 7):  # later beats and the cycle after tlast: after the packet
        y = seq()
        await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, resync=((beat, False, y),), name=f"set in beat {beat}")
        await m.send(pack(SESSION, y, itch(1)), intent=S.OK, name=f"at the set seq (beat {beat})")
        await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, resync=((beat, True, 0),), name=f"learn in beat {beat}")
        await m.send(pack(SESSION, seq(), itch(1)), intent=S.OK, name=f"syncs (learn in beat {beat})")
    y, z = seq(), seq()
    await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, resync=((1, False, y), (4, True, 0)), name="set then learn: learn wins")
    await m.send(heartbeat(SESSION, 0)[:9], intent=S.RUNT, name="runt: E kept by the learn")
    await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, resync=((1, True, 0), (4, False, y)), name="learn then set: set wins")
    await m.send(pack(SESSION, y, itch(1)), intent=S.OK, name="in sync at the set seq")
    await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, resync=((2, False, y), (7, False, z)), name="set y waits, set z after tlast")
    await m.send(pack(SESSION, z, itch(1)), intent=S.OK, name="at z")
    await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, resync=((2, False, y), (7, True, 0)), name="set y waits, learn after tlast")
    await m.send(heartbeat(SESSION, 0)[:9], intent=S.RUNT, name="runt: E = y, out of sync")
    for ipg in (0, 1):  # the earlier packet's waiting request, then the first-beat one
        y, z = seq(), seq()
        await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, resync=((2, False, y),), name=f"set y in beat 2, IPG {ipg}")
        await m.idle(ipg)
        await m.send(pack(SESSION, z, msgs), intent=S.OK, resync=((0, False, z),), name=f"set z on the first beat, at z, IPG {ipg}")
        await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, resync=((2, False, y),), name=f"set y in beat 2 again, IPG {ipg}")
        await m.idle(ipg)
        await m.send(heartbeat(SESSION, 0)[:12], intent=S.RUNT, resync=((0, True, 0),), name=f"learn on a runt's first beat: E = y, IPG {ipg}")
        await m.send(pack(SESSION, seq(), itch(1)), intent=S.OK, name=f"syncs, IPG {ipg}")
    await m.send(heartbeat(SESSION, m.next_seq())[:12], intent=S.RUNT, resync=((1, False, x),), name="set during a runt")
    m.set_cfg(enable=False)
    await m.send(pack(SESSION, x, itch(1)), intent=S.DISABLED, resync=((2, True, 0),), name="learn during a disabled packet")
    m.set_cfg(enable=True)
    await m.send(eos(SESSION, seq()), intent=S.EOS, name="end of session syncs, eos = 1")
    await m.resync(learn=False, seq=m.next_seq())
    assert not (await m.feed_settled()).eos
    await m.send(eos(SESSION, m.next_seq()), intent=S.EOS, resync=((3, True, 0),), name="eos cleared by a resync after tlast")
    await m.send(pack(SESSION, seq(), itch(1)), intent=S.OK, name="last")
    await m.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_unsynced_first_packet(dut):
    """After reset the feed is out of sync (E = 0, synced = 0, eos = 0). Packets that are not
    accepted (malformed, unsupported, abort, session, runt, other feed, disabled) leave it so,
    with every complete block out (no dup filter); the first accepted packet synchronizes:
    synced_now, no gap, E = S + C (S for a heartbeat), at any seq. A reset desynchronizes."""
    m = await start(dut)
    for first in ("normal", "heartbeat"):
        if first != "normal":
            await m.reset()
        assert await m.feed_settled() == FeedState(), f"after reset: {m.feed()}"
        s = seq()
        await m.send(pack(SESSION, s, itch(3))[:-2], intent=S.MALFORMED, name="malformed")
        await m.send(pack(SESSION, s, itch(1) + msgs_of(4)), intent=S.UNSUPPORTED, name="unsupported")
        await m.send(pack(SESSION, s, itch(2)), intent=S.ABORT, abort=True, name="abort")
        await m.send(pack(b"SESSION999", s, itch(2)), intent=S.SESSION, name="session")
        await m.send(pack(SESSION, s, itch(2))[:11], intent=S.RUNT, name="runt")
        await m.send(pack(SESSION, s, itch(2)), intent=S.OTHER_FEED, fidx=7, name="other feed")
        m.set_cfg(enable=False)
        await m.send(pack(SESSION, s, itch(2)), intent=S.DISABLED, name="disabled")
        m.set_cfg(enable=True)
        if first == "normal":
            await m.send(pack(SESSION, s - 1000, itch(3)), intent=S.OK, name="first accepted: normal")
        else:
            await m.send(heartbeat(SESSION, s - 1000), intent=S.HEARTBEAT, name="first accepted: heartbeat")
        await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.OK, name="in order after it")
        await m.send(pack(SESSION, m.next_seq(), itch(2)), intent=S.OK, name="before the reset")
        await m.send_cut(pack(SESSION, m.next_seq(), itch(3)), 3, name="cut by reset")
        await m.send(pack(SESSION, seq(), itch(2)), intent=S.OK, name="first after the reset syncs")
        await m.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_seq_wrap(dut):
    """Sequence numbers wrap at 2^64 (serial arithmetic): a packet from 2^64 - 3 with 6 messages
    (E = 3 after it), a partial overlap across the wrap, a gap across it, a heartbeat at
    2^64 - 1, a copy across it."""
    m = await start(dut)
    top = SEQ_MOD
    await m.resync(learn=False, seq=top - 3)
    await m.send(pack(SESSION, top - 3, itch(6)), intent=S.OK, name="2^64 - 3 .. 2")
    assert m.next_seq() == 3
    await m.send(pack(SESSION, top - 2, itch(7)), intent=S.OK, name="partial overlap across the wrap")
    await m.resync(learn=False, seq=top - 1)
    await m.send(pack(SESSION, 5, itch(2)), intent=S.OK, name="gap 6 across the wrap")
    await m.resync(learn=False, seq=top - 1)
    await m.send(heartbeat(SESSION, top - 1), intent=S.HEARTBEAT, name="heartbeat at 2^64 - 1")
    await m.send(heartbeat(SESSION, top - 2), intent=S.DUP, name="heartbeat at 2^64 - 2")
    p = pack(SESSION, top - 1, itch(3))
    await m.send(p, intent=S.OK, name="2^64 - 1 .. 1")
    await m.send(p, intent=S.DUP, name="its copy")
    await m.finish()


N_RANDOM = 10_000  # 05 F3 "done": do not lower
N_LINE_RATE = 2000
CFG_B = FeedConfig(enable=True, session_check=False, session=b"FEED-B-001", filter_mask=0xFF)


def coverage_bins(cfgs: tuple[FeedConfig, ...]) -> list[str]:
    """Required bins of tc_random_golden (11 §2.9)."""
    cats = sorted({c for cfg in cfgs for c in feed_categories(cfg)})
    return [
        *(f"status_{s.name}" for s in MoldStatus),
        "gap", "no_gap", "dup_partial", "oversize", "synced_now",
        *(f"start_lane_{k}" for k in range(8)), *(f"end_lane_{k}" for k in range(8)), "length_split",
        "ipg0", "tvalid_gap",
        "resync_between", "resync_first_beat", "resync_in_packet", "resync_after_tlast",
        *(f"cat_{c}" for c in cats),
    ]  # fmt: skip


@cocotb.test(timeout_time=500, timeout_unit="ms")
async def tc_random_golden(dut):
    """10^4 random_feed() packets match the model exactly (every message, event and feed state
    cycle). Two configs: A/B lines on entries 0 and 5 with the session check, then every entry
    and no check (a learn resync between them). Random IPG (half 0), tvalid gaps, and resync
    pulses that set E to the value the model already holds (on a first beat, in a packet, after
    tlast, between packets): they leave the sequence as the generator meant it and exercise the
    waiting logic and the eos clear. Ends with the coverage gate (coverage.json)."""
    rng = random.Random(random.getrandbits(32))
    cfg_a = replace(CFG, filter_mask=0x21)
    m = await start(dut)
    cov = Coverage("moldudp64_rx.tc_random_golden", coverage_bins((cfg_a, CFG_B)))
    n_msgs = 0
    for half, cfg in enumerate((cfg_a, CFG_B)):
        if half:
            await m.resync(learn=True)
        m.set_cfg(**{k: getattr(cfg, k) for k in ("session_check", "session", "filter_mask")})
        for i, t in enumerate(random_feed(rng, N_RANDOM // 2, cfg)):
            ipg = 0 if rng.random() < 0.5 else rng.randint(1, 4)
            if i and ipg and rng.random() < 0.05:  # not before the packet that synchronizes
                await m.resync(learn=False, seq=m.next_seq())
                cov.hit("resync_between")
            await m.idle(ipg)
            nbeats = (len(t.payload) + 7) // 8
            gaps = {k: rng.randint(1, 3) for k in rng.sample(range(1, nbeats), min(2, nbeats - 1))} if (
                nbeats > 1 and rng.random() < 0.3) else {}  # fmt: skip
            m.set_cfg(enable=t.enable)
            # A resync that sets E to the model's value at that point (before or after the packet).
            resync: tuple[Resync, ...] = ()
            r = rng.random() if i else 1.0
            if r < 0.04:
                resync = ((0, False, m.next_seq()),)
                cov.hit("resync_first_beat")
            elif r < 0.08 and nbeats > 1:
                _, after = expect(t.payload, t.abort, t.filter_idx, m.cfg, m.state)
                beat = rng.randint(1, nbeats)
                resync = ((beat, False, after.expected),)
                cov.hit("resync_after_tlast" if beat == nbeats else "resync_in_packet")
            p = await m.send(t.payload, intent=t.intent, gaps=gaps, abort=t.abort, fidx=t.filter_idx,
                             resync=resync, name=f"{t.category} #{half}.{i}")  # fmt: skip
            m.set_cfg(enable=True)
            w = p.want
            cov.hit(f"status_{w.status.name}")
            cov.hit(f"cat_{t.category}")
            if w.status in (S.OK, S.HEARTBEAT, S.EOS):
                cov.hit("gap" if w.gap else "no_gap")
                n_msgs += len(w.msgs)
            if w.status == S.OK and w.dup_msgs:
                cov.hit("dup_partial")
            if w.oversize:
                cov.hit("oversize")
            if w.synced_now:
                cov.hit("synced_now")
            if ipg == 0:
                cov.hit("ipg0")
            if gaps:
                cov.hit("tvalid_gap")
    await m.finish()
    for start_lane, end_lane in m.lanes:  # every output message's lanes, seen by the monitor
        cov.hit(f"start_lane_{start_lane}")
        cov.hit(f"end_lane_{end_lane}")
        if start_lane == 1:  # length field in lane 7 and lane 0 of the next beat
            cov.hit("length_split")
    assert n_msgs > N_RANDOM, f"only {n_msgs} accepted messages"
    cov.check(Path.cwd())  # the run directory


@cocotb.test(timeout_time=200, timeout_unit="ms")
async def tc_line_rate_b2b(dut):
    """11 §2.2, zero loss at line rate: 2000 in-order packets at IPG 0 (tvalid held high from the
    first to the last). Half are full of 12-B S/W messages, a quarter of 6-B messages (8-B
    blocks: a block boundary in every beat, the densest supported case), the rest mixed ITCH;
    sizes up to 1472 B. Every message exact; the monitor counts them all."""
    m = await start(dut)
    sent = 0
    for i in range(N_LINE_RATE):
        r = random.random()
        size = 1472 if random.random() < 0.5 else random.randint(28, 400)
        if r < 0.5:
            msgs = [itch_like(random, random.choice("SW")) for _ in range(max(1, (size - HDR_LEN) // 14))]
        elif r < 0.75:
            msgs = msgs_of(*[6] * ((size - HDR_LEN) // 8))
        else:
            msgs = []
            while HDR_LEN + sum(2 + len(x) for x in msgs) + 52 <= size:
                msgs += itch(1)
            msgs = msgs or itch(1)
        await m.send(pack(SESSION, m.next_seq(), msgs), intent=S.OK, name=f"packet {i} ({len(msgs)} messages)")
        sent += len(msgs)
    await m.finish()
    assert m.n_msgs == sent, f"{m.n_msgs} messages out, {sent} sent"


# The long tests run as their own pytest entries (parallel workers, one shared build); GROUP
# only gives each entry its own run directory.
LONG_TESTS = ("tc_random_golden", "tc_line_rate_b2b")


def directed_tests() -> list[str]:
    """Every tc_* of this module that is not a long test. Built when called, after the whole module
    is loaded, so a test defined anywhere in the file is in exactly one group."""
    return [name for name in globals() if name.startswith("tc_") and name not in LONG_TESTS]


@pytest.mark.parametrize("group", ["directed", *LONG_TESTS])
def test_moldudp64_rx(group):
    run_cocotb(
        toplevel="moldudp64_rx",
        filelists=["common.f", "core.f"],
        test_module="test_moldudp64_rx",
        testcase=directed_tests() if group == "directed" else group,
        extra_env={"GROUP": group},
    )
