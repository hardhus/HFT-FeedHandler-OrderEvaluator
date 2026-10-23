"""itch_decoder: ITCH 5.0 classifier and field decoder (audit/12).

Every input message is checked against the golden model (model/itch.py), in order:
- exactly one result per message (class, outcome) and an event exactly when the outcome is
  OUT: class, type, length, seq, sof_ts, the common header, every normalized field (0 where it
  does not apply) and the raw vector;
- every packet event passes through unchanged;
- latency with exact equality on every result, event and packet event: 2 cycles.
s_msg_class_en comes with each message (moldudp64_rx samples it on the packet's first beat);
it is random in idle cycles. A reset drops what is in the pipeline: inputs of the reset cycle
and the cycle before.
"""

import random
from collections import deque
from dataclasses import dataclass, field

import cocotb
import pytest
from cocotb.triggers import ReadOnly, RisingEdge

from common.clocks import start_clock
from common.runner import run_cocotb
from model.itch import (
    PRICE4,
    PRICE_MAX,
    SPEC,
    Bitmap,
    Event,
    ItchClass,
    ItchConfig,
    Outcome,
    Result,
    encode,
    expect,
)
from model.mold import MAX_MSG, MIN_LEN, MoldStatus
from selftest.test_itch_model import HAND, HAND_NORM, hexbytes

PERIOD_PS = 6400  # net_clk, 156.25 MHz
LATENCY = 2  # s_msg_valid -> m_ev_valid / m_res_valid, s_pkt_valid -> m_pkt_valid (12 §2.4)
ALL_EN = (1 << len(ItchClass)) - 1
C = ItchClass
EV_PORTS = ("order_ref", "new_ref", "shares", "price", "match", "side", "stock", "attribution", "printable",
            "cross_type")  # fmt: skip
PKT_PORTS = ("status", "seq", "count", "gap", "msgs", "dup_msgs", "oversize", "synced_now")
PKT_WIDTHS = {"status": 4, "seq": 64, "count": 16, "gap": 64, "msgs": 16, "dup_msgs": 16, "oversize": 16,
              "synced_now": 1}  # fmt: skip


@dataclass
class Msg:
    data: bytes
    seq: int = field(default_factory=lambda: random.getrandbits(64))
    sof_ts: int = field(default_factory=lambda: random.getrandbits(64))
    name: str = ""


@dataclass
class Want:
    msg: Msg
    cycle: int  # input cycle
    ev: Event | None
    res: Result


def random_pkt() -> dict[str, int]:
    p = {k: random.getrandbits(w) for k, w in PKT_WIDTHS.items()}
    p["status"] = random.choice(list(MoldStatus)).value
    return p


class Dec:
    """Drives messages and packet events the way moldudp64_rx delivers them; checks every
    output against the model in the cycle it is due."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.cycle = 0
        self.sent: deque[Msg] = deque()  # driven, not yet seen on the input by the monitor
        self.sent_pkts: deque[dict] = deque()
        self.out_q: deque[Want] = deque()  # seen on the input, output due
        self.pkt_q: deque[tuple[int, dict]] = deque()
        self.got: list[tuple[Msg, dict]] = []  # every event, with its message
        self.results: list[tuple[Msg, Result]] = []
        self.order: list[tuple[int, str]] = []  # (cycle, "msg"/"pkt") of every output
        self.latencies: list[int] = []
        self.class_en = ALL_EN  # driven with the next messages

    # ---- input
    def garbage(self) -> None:
        """Idle input: valids low, everything else random (the DUT must not look at it)."""
        d = self.dut
        d.s_msg_valid.value = 0
        d.s_msg_data.value = random.getrandbits(8 * MAX_MSG)
        d.s_msg_len.value = random.getrandbits(7)
        d.s_msg_seq.value = random.getrandbits(64)
        d.s_msg_sof_ts.value = random.getrandbits(64)
        d.s_msg_class_en.value = random.getrandbits(len(ItchClass))
        d.s_pkt_valid.value = 0
        for k, w in PKT_WIDTHS.items():
            getattr(d, f"s_pkt_{k}").value = random.getrandbits(w)

    def set_class_en(self, en: int) -> None:
        self.class_en = en

    async def step(self, msg: Msg | None = None, pkt: dict | None = None) -> None:
        """One cycle: a message and/or a packet event (or neither), then the clock edge."""
        self.garbage()
        d = self.dut
        if msg is not None:
            assert MIN_LEN <= len(msg.data) <= MAX_MSG, "moldudp64_rx delivers 6..64 bytes"
            d.s_msg_valid.value = 1
            d.s_msg_data.value = int.from_bytes(msg.data.ljust(MAX_MSG, b"\x00"), "little")
            d.s_msg_len.value = len(msg.data)
            d.s_msg_seq.value = msg.seq
            d.s_msg_sof_ts.value = msg.sof_ts
            d.s_msg_class_en.value = self.class_en
            self.sent.append(msg)
        if pkt is not None:
            d.s_pkt_valid.value = 1
            for k in PKT_PORTS:
                getattr(d, f"s_pkt_{k}").value = pkt[k]
            self.sent_pkts.append(pkt)
        await RisingEdge(d.clk)

    async def send(self, msgs: list[Msg], ipg: int | None = 0) -> None:
        """Messages one per cycle (ipg idle cycles between them; None: 0..2 at random)."""
        for m in msgs:
            await self.step(m)
            for _ in range(random.randint(0, 2) if ipg is None else ipg):
                await self.step()

    async def idle(self, cycles: int) -> None:
        for _ in range(cycles):
            await self.step()

    async def finish(self) -> None:
        await self.idle(LATENCY + 4)
        assert not self.sent and not self.sent_pkts, "input not seen by the monitor"
        assert not self.out_q, f"{self.out_q[0].msg.name or self.out_q[0].msg.data.hex()}: no result"
        assert not self.pkt_q, "packet event not forwarded"

    async def reset(self, cycles: int = 1) -> None:
        self.dut.rst.value = 1
        for _ in range(cycles):
            await self.step()
        self.dut.rst.value = 0

    # ---- output
    async def monitor(self) -> None:
        d = self.dut
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            self.cycle += 1
            self._check_outputs()
            if d.rst.value:  # the input of the cycle before is dropped (this cycle's: below)
                while self.out_q and self.out_q[-1].cycle >= self.cycle - 1:
                    self.out_q.pop()
                while self.pkt_q and self.pkt_q[-1][0] >= self.cycle - 1:
                    self.pkt_q.pop()
            if d.s_msg_valid.value:
                assert self.sent, "input message that the test did not send"
                m = self.sent.popleft()
                cfg = ItchConfig(class_en=int(d.s_msg_class_en.value), class_filter=0)
                ev, res = expect(m.data, cfg, Bitmap())
                if not d.rst.value:
                    self.out_q.append(Want(m, self.cycle, ev, res))
            if d.s_pkt_valid.value:
                assert self.sent_pkts, "input packet event that the test did not send"
                p = self.sent_pkts.popleft()
                if not d.rst.value:
                    self.pkt_q.append((self.cycle, p))

    def _due(self, q: deque, what: str) -> None:
        if q:
            cyc = q[0].cycle if isinstance(q[0], Want) else q[0][0]
            assert self.cycle - cyc < LATENCY, f"cycle {self.cycle}: {what} of input cycle {cyc} missing"

    def _check_outputs(self) -> None:
        d = self.dut
        if d.m_res_valid.value:
            assert self.out_q, f"cycle {self.cycle}: result with no message outstanding"
            w = self.out_q.popleft()
            name = w.msg.name or w.msg.data[:1].hex()
            lat = self.cycle - w.cycle
            self.latencies.append(lat)
            assert lat == LATENCY, f"{name}: result {lat} cycles after the input"
            got = Result(C(int(d.m_res_class.value)), Outcome(int(d.m_res_outcome.value)))
            assert got == w.res, f"{name}: result {got}, model {w.res}"
            self.results.append((w.msg, got))
            assert bool(d.m_ev_valid.value) == (w.ev is not None), f"{name}: m_ev_valid {int(d.m_ev_valid.value)}"
            if w.ev is not None:
                ev = self._event()
                want = self._want_event(w)
                for k in want:
                    assert ev[k] == want[k], f"{name}: m_ev_{k} {ev[k]:#x}, model {want[k]:#x}"
                self.got.append((w.msg, ev))
            self.order.append((self.cycle, "msg"))
        else:
            assert not d.m_ev_valid.value, f"cycle {self.cycle}: m_ev_valid without m_res_valid"
            self._due(self.out_q, "result")
        if d.m_pkt_valid.value:
            assert self.pkt_q, f"cycle {self.cycle}: packet event with none outstanding"
            cyc, p = self.pkt_q.popleft()
            assert self.cycle - cyc == LATENCY, f"packet event {self.cycle - cyc} cycles after the input"
            got = {k: int(getattr(d, f"m_pkt_{k}").value) for k in PKT_PORTS}
            assert got == p, f"packet event {got}, sent {p}"
            self.order.append((self.cycle, "pkt"))
        else:
            self._due(self.pkt_q, "packet event")

    def _event(self) -> dict[str, int]:
        d = self.dut
        ev = {k: int(getattr(d, f"m_ev_{k}").value) for k in EV_PORTS}
        ev |= {
            "class": int(d.m_ev_class.value), "type": int(d.m_ev_type.value), "len": int(d.m_ev_len.value),
            "seq": int(d.m_ev_seq.value), "sof_ts": int(d.m_ev_sof_ts.value), "locate": int(d.m_ev_locate.value),
            "tracking": int(d.m_ev_tracking.value), "ts": int(d.m_ev_ts.value), "raw": int(d.m_ev_raw.value),
        }  # fmt: skip
        return ev

    @staticmethod
    def _want_event(w: Want) -> dict[str, int]:
        e = w.ev
        want = {k: getattr(e, k) for k in EV_PORTS}
        want |= {
            "class": e.cls.value, "type": e.type, "len": e.length, "seq": w.msg.seq, "sof_ts": w.msg.sof_ts,
            "locate": e.locate, "tracking": e.tracking, "ts": e.timestamp,
            "raw": int.from_bytes(e.raw.ljust(MAX_MSG, b"\x00"), "little"),
        }  # fmt: skip
        return want


async def start(dut) -> Dec:
    """Idle inputs and reset first, then the clock."""
    dec = Dec(dut)
    dec.garbage()
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    await RisingEdge(dut.clk)
    cocotb.start_soon(dec.monitor())
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    return dec


def body(typ: str, n: int | None = None) -> bytes:
    """A message of type typ with random content, n bytes (default: the specification length)."""
    n = SPEC[typ].length if n is None else n
    return typ.encode() + random.randbytes(n - 1)


# Class of each type, written out from the specification (K12), independently of SPEC.
TYPE_CLASS = {**dict.fromkeys("AFECXDU", C.ORDER), **dict.fromkeys("PQB", C.TRADE),
              **dict.fromkeys("SRHYLVWKJh", C.ADMIN), **dict.fromkeys("INO", C.AUCTION)}  # fmt: skip


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_hand_messages(dut):
    """The 23 hand-written messages (one per type, from the specification tables), with idle
    cycles and then back to back: the header and every normalized field equal the hand-written
    values, besides the model check."""
    dec = await start(dut)
    for ipg in (3, 0):
        await dec.send([Msg(hexbytes(h), name=t) for t, (h, _) in HAND.items()], ipg=ipg)
    await dec.finish()
    assert len(dec.got) == 2 * len(HAND)
    for msg, ev in dec.got:
        values = HAND[msg.name][1]
        assert (ev["locate"], ev["tracking"], ev["ts"]) == (values["locate"], values["tracking"], values["timestamp"])
        norm = HAND_NORM.get(msg.name, {})
        assert {k: ev[k] for k in EV_PORTS} == {k: norm.get(k, 0) for k in EV_PORTS}, msg.name
        assert ev["class"] == TYPE_CLASS[msg.name]


R12 = 0x0102030405060708
R4 = 0x01020304


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_k3_big_endian_fields(dut):
    """K3: every multi-byte field of every decoded type, with distinct bytes, reads big-endian
    (a reversed field would read 0x0807060504030201); and the classic 00 00 25 1C = 9500."""
    dec = await start(dut)
    cases = {
        "A": (encode("A", locate=0x0102, tracking=0x0304, timestamp=0x050607080910, order_ref=R12, shares=R4,
                     price=0x05060708, stock=b"ABCDEFGH"),
              {"order_ref": R12, "shares": R4, "price": 0x05060708, "stock": 0x4847464544434241}),
        "F": (encode("F", order_ref=R12, shares=R4, price=R4, attribution=b"WXYZ"),
              {"order_ref": R12, "shares": R4, "price": R4, "attribution": 0x5A595857}),
        "E": (encode("E", order_ref=R12, executed_shares=R4, match=0x1112131415161718),
              {"order_ref": R12, "shares": R4, "match": 0x1112131415161718}),
        "C": (encode("C", order_ref=R12, executed_shares=R4, match=R12, exec_price=0x0A0B0C0D),
              {"order_ref": R12, "shares": R4, "match": R12, "price": 0x0A0B0C0D}),
        "X": (encode("X", order_ref=R12, cancelled_shares=R4), {"order_ref": R12, "shares": R4}),
        "D": (encode("D", order_ref=R12), {"order_ref": R12}),
        "U": (encode("U", orig_ref=R12, new_ref=0x1112131415161718, shares=R4, price=0x05060708),
              {"order_ref": R12, "new_ref": 0x1112131415161718, "shares": R4, "price": 0x05060708}),
        "P": (encode("P", order_ref=R12, shares=R4, price=R4, match=0x2122232425262728),
              {"order_ref": R12, "shares": R4, "price": R4, "match": 0x2122232425262728}),
        "Q": (encode("Q", shares=R12, stock=b"ABCDEFGH", cross_price=R4, match=0x3132333435363738),
              {"shares": R12, "stock": 0x4847464544434241, "price": R4, "match": 0x3132333435363738}),
        "B": (encode("B", match=R12), {"match": R12}),
        "9500": (hexbytes(HAND["A"][0]), {"shares": 9500, "price": 1_500_000}),
    }  # fmt: skip
    await dec.send([Msg(m, name=n) for n, (m, _) in cases.items()])
    await dec.finish()
    for msg, ev in dec.got:
        want = cases[msg.name][1]
        assert {k: ev[k] for k in want} == want, msg.name
    a = next(ev for msg, ev in dec.got if msg.name == "A")
    assert (a["locate"], a["tracking"], a["ts"]) == (0x0102, 0x0304, 0x050607080910)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_k12_type_codes(dut):
    """K12: each of the 23 type letters at its specification length gives its class and type
    byte (class table written out from the specification, not taken from the model)."""
    dec = await start(dut)
    lengths = {t: len(hexbytes(h)) for t, (h, _) in HAND.items()}  # hand-written messages
    await dec.send([Msg(body(t, lengths[t]), name=t) for t in TYPE_CLASS])
    await dec.finish()
    assert {msg.name: (ev["class"], ev["type"]) for msg, ev in dec.got} == {
        t: (c.value, ord(t)) for t, c in TYPE_CLASS.items()
    }


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_lengths(dut):
    """Every known type one byte short, at its length and one byte long: only the exact length
    decodes; the others are MALFORMED with the header kept and every field 0."""
    dec = await start(dut)
    msgs = [Msg(body(t, SPEC[t].length + d), name=f"{t}{d:+d}") for t in SPEC for d in (-1, 0, 1)]
    random.shuffle(msgs)
    await dec.send(msgs, ipg=None)
    await dec.finish()
    malformed = [ev for msg, ev in dec.got if not msg.name.endswith("+0")]
    assert len(malformed) == 2 * len(SPEC) and all(ev["class"] == C.MALFORMED for ev in malformed)
    assert all(not any(ev[k] for k in EV_PORTS) for ev in malformed)
    assert all(ev["locate"] == int.from_bytes(msg.data[1:3], "big") for msg, ev in dec.got)


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def tc_all_type_bytes(dut):
    """Every type byte 0..255 at every length 6..64 (15104 messages, back to back): known types
    decode only at their length, every other byte is UNKNOWN; header only from 11 bytes on."""
    dec = await start(dut)
    msgs = [Msg(bytes([b]) + random.randbytes(n - 1)) for b in range(256) for n in range(MIN_LEN, MAX_MSG + 1)]
    random.shuffle(msgs)
    await dec.send(msgs)
    await dec.finish()
    assert len(dec.results) == 256 * (MAX_MSG - MIN_LEN + 1)
    count = {c: sum(r.cls == c for _, r in dec.results) for c in C}
    assert count[C.MALFORMED] == 23 * (MAX_MSG - MIN_LEN), "every known type at every other length"
    assert count[C.UNKNOWN] == (256 - 23) * (MAX_MSG - MIN_LEN + 1)
    short = [ev for msg, ev in dec.got if len(msg.data) < 11]
    assert short and all((ev["locate"], ev["tracking"], ev["ts"]) == (0, 0, 0) for ev in short)


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def tc_field_extremes(dut):
    """Every type with all bytes 0 and all bytes 0xFF after the type byte; every Price(4) at the
    largest value 200,000.0000; timestamp 2^48 - 1; order references 2^64 - 1."""
    dec = await start(dut)
    msgs = []
    for t, spec in SPEC.items():
        msgs += [Msg(t.encode() + bytes(spec.length - 1), name=f"{t} zeros"),
                 Msg(t.encode() + b"\xff" * (spec.length - 1), name=f"{t} ones"),
                 Msg(encode(t, timestamp=(1 << 48) - 1), name=f"{t} ts max")]  # fmt: skip
        prices = {n: PRICE_MAX for n, _, _, k in spec.body if k == PRICE4}
        if prices:
            msgs.append(Msg(encode(t, **prices), name=f"{t} price max"))
        refs = {n: (1 << 64) - 1 for n, *_ in spec.body if n in ("order_ref", "orig_ref", "new_ref")}
        if refs:
            msgs.append(Msg(encode(t, **refs), name=f"{t} ref max"))
    await dec.send(msgs)
    await dec.finish()
    by = {msg.name: ev for msg, ev in dec.got}
    assert by["A ones"]["order_ref"] == (1 << 64) - 1 and by["A ones"]["price"] == (1 << 32) - 1
    assert by["U ones"]["shares"] == (1 << 32) - 1 and by["Q ones"]["shares"] == (1 << 64) - 1
    assert by["S ts max"]["ts"] == (1 << 48) - 1 and by["A price max"]["price"] == PRICE_MAX
    assert by["U ref max"]["order_ref"] == by["U ref max"]["new_ref"] == (1 << 64) - 1


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_q_shares_64(dut):
    """Cross Trade shares is 8 bytes (the only 8-byte shares among the decoded types): values that
    do not fit in 4 bytes come out whole."""
    dec = await start(dut)
    values = [(1 << 64) - 1, 1 << 32, (1 << 32) - 1, R12, 0x8000000000000001]
    await dec.send([Msg(encode("Q", shares=v), name=str(i)) for i, v in enumerate(values)])
    await dec.finish()
    assert [ev["shares"] for _, ev in dec.got] == values


def one_of_each_class() -> list[Msg]:
    """ORDER, TRADE, ADMIN, AUCTION, UNKNOWN (type 0x00), MALFORMED (A one byte short)."""
    return [Msg(body(t)) for t in "APRI"] + [Msg(bytes([0x00]) + random.randbytes(20)), Msg(body("A", 35))]


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_class_mask(dut):
    """Every value of s_msg_class_en with one message of every class: a disabled class is MASKED
    (no event). Then a new value with every message, one message per cycle: each message uses
    its own value (the decoder registers it with the message)."""
    dec = await start(dut)
    for en in range(1 << len(C)):
        dec.set_class_en(en)
        await dec.send(one_of_each_class())
    masked = sum(r.outcome == Outcome.MASKED for _, r in dec.results)
    n0 = len(dec.results)
    for _ in range(400):  # a new value with every message
        dec.set_class_en(random.getrandbits(len(C)))
        await dec.step(random.choice(one_of_each_class()))
    await dec.finish()
    assert masked == sum(len(C) - bin(en).count("1") for en in range(1 << len(C))), "masked count by hand"
    outs = [r.outcome for _, r in dec.results[n0:]]
    assert Outcome.MASKED in outs and Outcome.OUT in outs


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def tc_pkt_forward(dut):
    """Packet events pass through unchanged with the messages' latency, so a packet's event
    follows its messages: alone, right after its last message, in the same cycle as the next
    packet's first message (IPG 0), back to back, and with random field values."""
    dec = await start(dut)
    await dec.step(pkt=random_pkt())  # a heartbeat-like packet: no messages
    await dec.idle(3)
    for _ in range(30):
        msgs = [Msg(body(random.choice(list(SPEC)))) for _ in range(random.randint(1, 5))]
        await dec.send(msgs[:-1])
        await dec.step(msgs[-1])
        nxt = Msg(body("A"))
        await dec.step(nxt, pkt=random_pkt())  # this packet's event with the next one's first message
        await dec.step(pkt=random_pkt())  # the next packet's event right after its only message
        await dec.step(pkt=random_pkt())  # an empty packet back to back
        await dec.idle(random.randint(0, 2))
    await dec.finish()
    cycles = [c for c, k in dec.order]
    assert cycles == sorted(cycles)
    both = [c for c in set(cycles) if (c, "msg") in dec.order and (c, "pkt") in dec.order]
    assert len(both) == 30, "a packet event and the next packet's first message in the same cycle"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_latency(dut):
    """2 cycles from input to output (12 §2.4), for a lone message, a lone packet event and a
    stream at one message per cycle; the monitor checks it with exact equality on every output."""
    dec = await start(dut)
    await dec.step(Msg(body("A")))
    await dec.idle(5)
    await dec.step(pkt=random_pkt())
    await dec.idle(5)
    await dec.send([Msg(body(random.choice(list(SPEC)))) for _ in range(50)])
    await dec.finish()
    assert dec.latencies == [LATENCY] * 51


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_reset_mid_packet(dut):
    """A reset drops what is in the pipeline: a message or packet event presented in the first
    reset cycle or the cycle before has no output; one presented two cycles before comes out (in
    the first reset cycle). Two messages in consecutive cycles (the first with a packet event),
    the reset starting d = 0..3 cycles after the second (d = 0: in its cycle, so a 1-cycle reset
    must drop an input that arrives while it is high); 1- and 3-cycle resets; then traffic."""
    dec = await start(dut)
    for d in range(4):
        for cycles in (1, 3):
            await dec.idle(4)
            n, npkt = len(dec.results), sum(k == "pkt" for _, k in dec.order)
            await dec.step(Msg(body("A"), name=f"d{d} first"), pkt=random_pkt())
            if d == 0:  # the reset starts with the second message (and its packet event)
                dec.dut.rst.value = 1
                await dec.step(Msg(body("A"), name=f"d{d} second"), pkt=random_pkt())
                await dec.reset(cycles - 1)
                dec.dut.rst.value = 0
            else:
                await dec.step(Msg(body("A"), name=f"d{d} second"))
                await dec.idle(d - 1)
                await dec.reset(cycles)
            await dec.idle(4)
            # first message: input d + 1 cycles before the reset; second: d cycles
            want = int(d >= 1) + int(d >= 2)
            assert len(dec.results) - n == want, f"d={d}: {len(dec.results) - n} message(s) out, want {want}"
            assert sum(k == "pkt" for _, k in dec.order) - npkt == int(d >= 1), f"d={d}: packet events"
    await dec.send([Msg(body(random.choice(list(SPEC)))) for _ in range(20)])
    await dec.step(pkt=random_pkt())
    await dec.finish()


# The long tests run as their own pytest entries (parallel workers, one shared build).
LONG_TESTS: tuple[str, ...] = ()


def directed_tests() -> list[str]:
    return [name for name in globals() if name.startswith("tc_") and name not in LONG_TESTS]


@pytest.mark.parametrize("group", ["directed", *LONG_TESTS])
def test_itch_decoder(group):
    run_cocotb(
        toplevel="itch_decoder",
        filelists=["common.f", "core.f"],
        test_module="test_itch_decoder",
        testcase=directed_tests() if group == "directed" else group,
        extra_env={"GROUP": group},
    )
