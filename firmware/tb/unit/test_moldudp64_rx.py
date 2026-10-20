"""moldudp64_rx: MoldUDP64 de-framer (audit/11).

Every packet is checked against the golden model (model/mold.py), in order:
- the messages on m_msg_*: sequence number, length, the model's bytes, zero bytes after the
  length, m_msg_sof_ts of their packet;
- exactly one packet event per input packet, after its messages: status, message and oversize
  counts, and the header's seq/count (packets of at least MOLD_HDR_LEN bytes).
Latency is checked with exact equality on every message and every packet event: 2 cycles from
the input beat that holds the message's last byte, 3 cycles from the input tlast.

The sequence tracking is not in the RTL yet: each packet is compared with the model starting
from the reset state (not synchronized), so every complete block comes out.
"""

import random
from collections import deque
from dataclasses import dataclass, field

import cocotb
import pytest
from cocotb.triggers import ReadOnly, RisingEdge

from common.clocks import start_clock
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
    heartbeat,
    itch_like,
    pack,
)

PERIOD_PS = 6400  # net_clk, 156.25 MHz
MSG_LATENCY = 2  # input beat with a message's last byte -> m_msg_valid (11 §2.5)
PKT_LATENCY = 3  # input tlast -> m_pkt_valid
SESSION = b"SESSION001"
CFG = FeedConfig(enable=True, session_check=False, session=SESSION, filter_mask=0x01)
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


@dataclass
class Pkt:
    """One input packet: the model's outcome and what the monitor saw (cycle numbers count
    rising edges, as in test_eth_ip_udp_rx)."""

    name: str
    payload: bytes
    ts: int
    want: MoldResult
    cut: bool = False  # cut by a reset: no event, a prefix of the messages
    msgs: deque = field(default_factory=deque)  # messages still expected
    beats: list[int] = field(default_factory=list)  # input cycle of each beat
    msg_latency: list[int] = field(default_factory=list)
    pkt_latency: int = -1


class Mold:
    """Drives packets the way eth_ip_udp_rx delivers them; checks every message and packet event
    against the model."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.cycle = 0
        self.awaiting: deque[Pkt] = deque()  # sent, first beat not seen yet
        self.in_flight: Pkt | None = None
        self.out_q: deque[Pkt] = deque()  # packets whose messages or event are outstanding
        self.n_msgs = 0
        self.lanes: set[tuple[int, int]] = set()  # (start lane, end lane) of output messages
        self.s_tdata, self.s_tkeep = dut.s_axis_udp_tdata, dut.s_axis_udp_tkeep
        self.s_tvalid, self.s_tlast = dut.s_axis_udp_tvalid, dut.s_axis_udp_tlast
        self.s_len, self.s_ts = dut.s_udp_len, dut.s_udp_sof_ts
        self.m_valid, self.m_data, self.m_len = dut.m_msg_valid, dut.m_msg_data, dut.m_msg_len
        self.m_seq, self.m_ts = dut.m_msg_seq, dut.m_msg_sof_ts
        self.p_valid, self.p_status, self.p_seq = dut.m_pkt_valid, dut.m_pkt_status, dut.m_pkt_seq
        self.p_count, self.p_msgs, self.p_over = dut.m_pkt_count, dut.m_pkt_msgs, dut.m_pkt_oversize

    def garbage(self) -> None:
        """Idle input: tvalid low, everything else random (the DUT must not look at it)."""
        self.s_tvalid.value = 0
        self.s_tdata.value = random.getrandbits(64)
        self.s_tkeep.value = random.getrandbits(8)
        self.s_tlast.value = random.getrandbits(1)
        self.s_len.value = random.getrandbits(16)
        self.s_ts.value = random.getrandbits(64)

    async def idle(self, cycles: int) -> None:
        for _ in range(cycles):
            self.garbage()
            await RisingEdge(self.dut.clk)

    def register(self, payload: bytes, name: str, intent: MoldStatus | None, cut: bool = False) -> Pkt:
        want, _ = expect(payload, False, 0, CFG, FeedState())
        assert intent is None or want.status == intent, f"{name}: test packet gives {want.status.name}, meant {intent.name}"
        p = Pkt(name, payload, random.getrandbits(64), want, cut, deque(want.msgs))
        self.awaiting.append(p)
        self.out_q.append(p)
        return p

    async def send(
        self, payload: bytes, *, intent: MoldStatus | None = None, gaps: dict[int, int] | None = None, name: str = ""
    ) -> Pkt:
        """Present one packet, byte 0 = tdata[7:0]; gaps[k]: idle cycles before beat k."""
        p = self.register(payload, name or f"packet {self.cycle}", intent)
        await self._drive(p, gaps or {}, len(payload))
        return p

    async def send_cut(self, payload: bytes, beats: int, name: str) -> Pkt:
        """Present the first `beats` beats, then a 1-cycle reset (the source is reset with us)."""
        p = self.register(payload, name, None, cut=True)
        await self._drive(p, {}, 8 * beats, last=False)
        self.garbage()
        self.dut.rst.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.rst.value = 0
        return p

    async def _drive(self, p: Pkt, gaps: dict[int, int], nbytes: int, last: bool = True) -> None:
        data = p.payload[:nbytes]
        beats = [data[i : i + 8] for i in range(0, len(data), 8)]
        for k, beat in enumerate(beats):
            if gaps.get(k):
                for _ in range(gaps[k]):  # a gap inside the packet: s_udp_* stay held
                    self.s_tvalid.value = 0
                    self.s_tdata.value = random.getrandbits(64)
                    await RisingEdge(self.dut.clk)
            self.s_tdata.value = int.from_bytes(beat.ljust(8, bytes([random.getrandbits(8)])), "little")
            self.s_tkeep.value = (1 << len(beat)) - 1 if k == len(beats) - 1 else 0xFF
            self.s_tlast.value = int(last and k == len(beats) - 1)
            self.s_len.value = len(p.payload)
            self.s_ts.value = p.ts
            self.s_tvalid.value = 1
            await RisingEdge(self.dut.clk)
        self.garbage()

    async def finish(self, cycles: int = 8) -> None:
        await self.idle(cycles)
        assert not self.awaiting, f"{len(self.awaiting)} packet(s) never presented"
        assert not self.out_q, f"{self.out_q[0].name}: no packet event ({len(self.out_q[0].msgs)} message(s) missing)"

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
            if rst.value:
                self.in_flight = None
                if self.out_q and self.out_q[0].cut:  # its remaining messages are lost
                    self.out_q.popleft()
                assert not any(p.cut for p in self.out_q), "a cut packet behind an outstanding one"
            elif self.s_tvalid.value:
                if self.in_flight is None:
                    assert self.awaiting, "input packet that the test did not register"
                    self.in_flight = self.awaiting.popleft()
                self.in_flight.beats.append(self.cycle)
                if self.s_tlast.value:
                    self.in_flight = None

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
        assert status == p.want.status, f"{p.name}: status {name}, model {p.want.status.name}"
        got = (int(self.p_msgs.value), int(self.p_over.value))
        assert got == (len(p.want.msgs), p.want.oversize), f"{p.name}: (msgs, oversize) {got}, model {(len(p.want.msgs), p.want.oversize)}"
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
    after the last block, the last message cut by one byte."""
    m = await start(dut)
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
        await m.send(payload, intent=intent, name=name)
        await m.idle(2)
    for name, payload, intent in cases:  # again back to back (IPG 0)
        await m.send(payload, intent=intent, name=f"{name}, IPG 0")
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
            await m.send(pack(SESSION, seq(), msgs), intent=S.OK, gaps=gaps, name=f"start lane {s}, end lane {e}")
            await m.idle((s * 8 + e) % 3)
    for n in range(MIN_LEN, MAX_MSG + 1):
        await m.send(pack(SESSION, seq(), msgs_of(*[n] * 9)), intent=S.OK, name=f"9 x {n} B")
    for i in range(4):
        types = list(ITCH50_LEN)
        random.shuffle(types)
        await m.send(pack(SESSION, seq(), [itch_like(random, t) for t in types]), intent=S.OK, name=f"all ITCH types {i}")
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
            await m.send(pack(SESSION, seq(), msgs), intent=S.OK, name=f"{n} B at lane {s}")
    await m.send(pack(SESSION, seq(), itch(1) + msgs_of(1000) + itch(3)), intent=S.OK, name="1000 B")
    await m.send(pack(SESSION, seq(), msgs_of(1400) + itch(1)), intent=S.OK, name="1400 B")
    await m.send(pack(SESSION, seq(), msgs_of(65, 12, 100, 300, 64, 66)), intent=S.OK, name="several")
    await m.send(pack(SESSION, seq(), itch(2) + msgs_of(200)), intent=S.OK, name="oversize last")
    largest = UDP_PAYLOAD_MAX - HDR_LEN - 2
    await m.send(pack(SESSION, seq(), msgs_of(largest)), intent=S.OK, name=f"{largest} B, the largest")
    await m.send(pack(SESSION, seq(), msgs_of(12, largest - 14)), intent=S.OK, name="largest after a message")
    cut = pack(SESSION, seq(), itch(1) + msgs_of(0xFFFF))[:UDP_PAYLOAD_MAX]
    await m.send(cut, intent=S.MALFORMED, name="length 0xFFFF, cut at the largest payload")
    await m.send(pack(SESSION, seq(), itch(3)), intent=S.OK, name="after the largest")
    await m.finish()


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def tc_unsupported(dut):
    """A block of length 0..5 (legal MoldUDP64, not supported at line rate) makes the packet
    UNSUPPORTED; the messages before it come out, nothing after it. As the first block, after a
    message ending in every lane (so its length field starts in every lane, split at lane 7),
    followed by more blocks, and as the last bytes. Length 6 in the same places is accepted."""
    m = await start(dut)
    for n in range(MIN_LEN):  # an oversize block before it is not reported
        payload = pack(SESSION, seq(), msgs_of(200) + itch(1) + msgs_of(n))
        await m.send(payload, intent=S.UNSUPPORTED, name=f"{n} B after an oversize block")
    for n in range(MIN_LEN + 1):
        intent = S.OK if n == MIN_LEN else S.UNSUPPORTED
        await m.send(pack(SESSION, seq(), msgs_of(n)), intent=intent, name=f"{n} B first")
        for s in range(8):
            msgs = msgs_of(fill_to_lane(s), n) + itch(2)
            await m.send(pack(SESSION, seq(), msgs), intent=intent, name=f"{n} B at lane {s}, then more")
            await m.send(pack(SESSION, seq(), msgs[:2]), intent=intent, name=f"{n} B at lane {s}, last")
        if n:  # the length field is complete, the data is not
            cut = pack(SESSION, seq(), itch(1) + msgs_of(n))[:-1]
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
        good = pack(SESSION, seq(), msgs_of(fill_to_lane(3), len_to_lane(3, e)))
        assert block_ends(good)[1] % 8 == e
        for extra in (1, 7, 8, 9, 20):
            await m.send(good + random.randbytes(extra), intent=S.MALFORMED, name=f"end lane {e}, {extra} B after")
        await m.send(good, intent=S.OK, name=f"end lane {e}, exact")
        await m.send(good[:-1], intent=S.MALFORMED, name=f"end lane {e}, last byte missing")
    # The last block starts and ends in the tlast beat (length field in lanes 7 and 0, 6 B up to
    # lane 6), with one more byte in that beat.
    for _ in range(3):
        good = pack(SESSION, seq(), msgs_of(fill_to_lane(7), 6))
        assert block_ends(good)[1] % 8 == 6 and len(good) % 8 == 7
        await m.send(good + random.randbytes(1), intent=S.MALFORMED, name="6 B block in the tlast beat, 1 B after")
        await m.send(good, intent=S.OK, name="6 B block in the tlast beat, exact")
    for s in range(8):  # cut inside the length field at lane s, and right before it
        msgs = msgs_of(fill_to_lane(s), 30, 12)
        good = pack(SESSION, seq(), msgs)
        ofs = HDR_LEN + 2 + len(msgs[0])
        assert ofs % 8 == s
        await m.send(good[: ofs + 1], intent=S.MALFORMED, name=f"length field at lane {s}, one byte")
        await m.send(good[:ofs], intent=S.MALFORMED, name=f"length field at lane {s}, none")
        await m.send(good[: ofs + 2], intent=S.MALFORMED, name=f"length field at lane {s}, no data")
    # Oversize blocks before the fault are not reported (only accepted packets count them).
    over = pack(SESSION, seq(), msgs_of(100) + itch(1) + msgs_of(65))
    await m.send(over + b"\x00", intent=S.MALFORMED, name="two oversize, then a byte after the last block")
    await m.send(over[:-1], intent=S.MALFORMED, name="two oversize, the last one cut")
    p = pack(SESSION, seq(), itch(3))
    await m.send(p[:18] + (5).to_bytes(2, "big") + p[20:], intent=S.MALFORMED, name="count 5, 3 blocks")
    await m.send(p[:18] + (2).to_bytes(2, "big") + p[20:], intent=S.MALFORMED, name="count 2, 3 blocks")
    await m.send(pack(SESSION, seq(), itch(1))[:HDR_LEN], intent=S.MALFORMED, name="count 1, header only")
    for build in (heartbeat, eos):
        for extra in (1, 3, 4, 5, 8, 12, 30):
            await m.send(build(SESSION, seq()) + random.randbytes(extra), intent=S.MALFORMED, name=f"{build.__name__} + {extra} B")
    await m.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_runt(dut):
    """Payloads of 1..19 B are RUNT (prefixes of a packet and of a heartbeat); 20 B is a whole
    heartbeat or end of session (accepted) or a header whose count needs blocks (MALFORMED)."""
    m = await start(dut)
    full = pack(SESSION, seq(), itch(2))
    hb = heartbeat(SESSION, seq())
    for n in range(1, HDR_LEN):
        await m.send(full[:n], intent=S.RUNT, name=f"{n} B of a packet")
        await m.send(hb[:n], intent=S.RUNT, name=f"{n} B of a heartbeat")
    await m.send(hb, intent=S.HEARTBEAT, name="20 B heartbeat")
    await m.send(eos(SESSION, seq()), intent=S.EOS, name="20 B end of session")
    await m.send(full[:HDR_LEN], intent=S.MALFORMED, name="20 B, count 2")
    await m.send(full, intent=S.OK, name="after the runts")
    await m.finish()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_latency(dut):
    """11 §2.5, exact (the monitor checks every message and event; these are the edge cases): a
    message that starts and ends in one beat; one ending in the tlast beat, at lane 0 and 7;
    tvalid gaps right before and after the beat that ends a message; back-to-back packets whose
    event shares cycles with the next packet's messages."""
    m = await start(dut)
    cases = [
        ("starts and ends in one beat", pack(SESSION, seq(), msgs_of(fill_to_lane(0), 6, 7, 30)), {}),
        ("ends in tlast beat, lane 0", pack(SESSION, seq(), msgs_of(len_to_lane(4, 0))), {}),
        ("ends in tlast beat, lane 7", pack(SESSION, seq(), msgs_of(len_to_lane(4, 7))), {}),
        ("gaps around message ends", pack(SESSION, seq(), msgs_of(6, 6, 6, 6, 6)), {3: 2, 4: 1, 5: 3}),
    ]
    for name, payload, gaps in cases:
        p = await m.send(payload, intent=S.OK, gaps=gaps, name=name)
        await m.idle(6)
        assert p.msg_latency == [MSG_LATENCY] * len(p.want.msgs), f"{name}: {p.msg_latency}"
        assert p.pkt_latency == PKT_LATENCY, f"{name}: event {p.pkt_latency}"
    sent = [await m.send(pack(SESSION, seq(), msgs_of(6, 6)), intent=S.OK, name=f"b2b {i}") for i in range(6)]
    await m.finish()
    assert all(p.msg_latency == [MSG_LATENCY] * 2 and p.pkt_latency == PKT_LATENCY for p in sent)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_reset_mid_packet(dut):
    """11 §2.5: a 1-cycle reset in the header (beats 1, 2), in a message, and in the beat after a
    message ended. The cut packet gives no event and at most a prefix of its messages; the
    packets after the reset come out exactly."""
    m = await start(dut)
    for cut in (1, 2, 3, 5, 9):
        await m.send(pack(SESSION, seq(), itch(3)), intent=S.OK, name=f"before the reset at beat {cut}")
        await m.idle(6)
        await m.send_cut(pack(SESSION, seq(), msgs_of(6, 6, 30, 40, 50)), cut, name=f"cut at beat {cut}")
        await m.send(pack(SESSION, seq(), itch(4)), intent=S.OK, name=f"first after the reset at beat {cut}")
        await m.send(heartbeat(SESSION, seq()), intent=S.HEARTBEAT, name=f"second after the reset at beat {cut}")
    await m.finish()


def test_moldudp64_rx():
    run_cocotb(toplevel="moldudp64_rx", filelists=["common.f", "core.f"], test_module="test_moldudp64_rx")
