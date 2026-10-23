"""itch_decoder: ITCH 5.0 classifier, field decoder and symbol filter (audit/12).

Every input message is checked against the golden model (model/itch.py), in order:
- exactly one result per message (class, outcome) and an event exactly when the outcome is
  OUT: class, type, length, seq, sof_ts, the common header, every normalized field (0 where it
  does not apply) and the raw vector;
- every packet event passes through unchanged;
- latency with exact equality on every result, event and packet event: 2 cycles;
- the symbol bitmap: a model copy, updated by each accepted write in its cycle after that
  cycle's lookup (READ_FIRST: a lookup in the write's cycle sees the old word); every read
  response against it, every response exactly 1 cycle after its command; sym_req_ready in every
  cycle: 0 in reset and for exactly 2048 cycles after it (the sweep), when the model is all 0.
s_msg_class_en/s_msg_class_filter come with each message (moldudp64_rx samples them on the
packet's first beat); they are random in idle cycles, as is every command field without
sym_req_valid. A reset drops what is in the pipeline: inputs of the reset cycle and the cycle
before.
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
SWEEP = 2048  # cycles of the bitmap sweep after a reset (one word per cycle)
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


@dataclass
class Cmd:
    wr: bool
    addr: int
    wdata: int = 0
    wstrb: int = 0xF
    rdata: int | None = None  # read response, filled by the monitor
    cycle: int = -1  # cycle it was accepted


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
        self.class_filter = 0
        self.bm = Bitmap()  # model of the bitmap RAM
        self.sweep_left = 0  # cycles of the sweep still to come (sym_req_ready low)
        self.pending: Cmd | None = None  # driven until accepted
        self.rsp_q: deque[Cmd] = deque()  # accepted, response due
        self.done: list[Cmd] = []  # every completed command

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
        d.s_msg_class_filter.value = random.getrandbits(len(ItchClass))
        d.sym_req_valid.value = 0
        d.sym_req_wr.value = random.getrandbits(1)
        d.sym_req_addr.value = random.getrandbits(11)
        d.sym_req_wdata.value = random.getrandbits(32)
        d.sym_req_wstrb.value = random.getrandbits(4)
        d.s_pkt_valid.value = 0
        for k, w in PKT_WIDTHS.items():
            getattr(d, f"s_pkt_{k}").value = random.getrandbits(w)

    def set_class_en(self, en: int) -> None:
        self.class_en = en

    async def step(self, msg: Msg | None = None, pkt: dict | None = None) -> None:
        """One cycle: a message and/or a packet event (or neither) and the pending command (if
        any), then the clock edge."""
        self.garbage()
        d = self.dut
        if self.pending is not None:
            c = self.pending
            d.sym_req_valid.value = 1
            d.sym_req_wr.value = int(c.wr)
            d.sym_req_addr.value = c.addr
            d.sym_req_wdata.value = c.wdata
            d.sym_req_wstrb.value = c.wstrb
        if msg is not None:
            assert MIN_LEN <= len(msg.data) <= MAX_MSG, "moldudp64_rx delivers 6..64 bytes"
            d.s_msg_valid.value = 1
            d.s_msg_data.value = int.from_bytes(msg.data.ljust(MAX_MSG, b"\x00"), "little")
            d.s_msg_len.value = len(msg.data)
            d.s_msg_seq.value = msg.seq
            d.s_msg_sof_ts.value = msg.sof_ts
            d.s_msg_class_en.value = self.class_en
            d.s_msg_class_filter.value = self.class_filter
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

    def issue(self, cmd: Cmd) -> Cmd:
        """Drive cmd from the next step on until it is accepted (no wait)."""
        assert self.pending is None, "one command at a time"
        self.pending = cmd
        return cmd

    async def command(self, cmd: Cmd) -> Cmd:
        """Issue cmd and step until its response is out; returns it (rdata for a read)."""
        self.issue(cmd)
        while self.pending is not None or self.rsp_q:
            await self.step()
        return cmd

    async def write(self, addr: int, wdata: int, wstrb: int = 0xF) -> None:
        await self.command(Cmd(True, addr, wdata, wstrb))

    async def read(self, addr: int) -> int:
        return (await self.command(Cmd(False, addr))).rdata

    async def subscribe(self, locates: list[int], on: bool = True) -> None:
        """Set (or clear) the bits of these locates, a whole-word write each (from the model)."""
        for loc in locates:
            word = self.bm.read(loc >> 5)
            bit = 1 << (loc & 31)
            await self.write(loc >> 5, word | bit if on else word & ~bit)

    async def wait_sweep(self) -> None:
        while self.sweep_left or self.dut.rst.value:
            await self.step()

    async def finish(self) -> None:
        while self.pending is not None:
            await self.step()
        await self.idle(LATENCY + 4)
        assert not self.rsp_q, "command response missing"
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
            rst = bool(d.rst.value)
            ready = not rst and self.sweep_left == 0
            assert bool(d.sym_req_ready.value) == ready, f"cycle {self.cycle}: sym_req_ready, model {ready}"
            if rst:  # the input of the cycle before is dropped (this cycle's: below)
                while self.out_q and self.out_q[-1].cycle >= self.cycle - 1:
                    self.out_q.pop()
                while self.pkt_q and self.pkt_q[-1][0] >= self.cycle - 1:
                    self.pkt_q.pop()
                self.sweep_left = SWEEP
                self.bm = Bitmap()  # all 0 after the sweep; lookups during it give 0 too
            elif self.sweep_left:
                self.sweep_left -= 1
            if d.s_msg_valid.value:  # the lookup reads the bitmap before this cycle's write
                assert self.sent, "input message that the test did not send"
                m = self.sent.popleft()
                cfg = ItchConfig(int(d.s_msg_class_en.value), int(d.s_msg_class_filter.value))
                ev, res = expect(m.data, cfg, self.bm)
                if not rst:
                    self.out_q.append(Want(m, self.cycle, ev, res))
            if d.sym_req_valid.value and ready:
                c = self.pending
                assert c is not None, "command that the test did not issue"
                c.cycle = self.cycle
                if c.wr:
                    self.bm.write(c.addr, c.wdata, c.wstrb)
                else:
                    c.rdata = self.bm.read(c.addr)
                self.rsp_q.append(c)
                self.pending = None
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
        if d.sym_rsp_valid.value:
            assert self.rsp_q, f"cycle {self.cycle}: command response with none outstanding"
            c = self.rsp_q.popleft()
            assert self.cycle - c.cycle == 1, f"command response {self.cycle - c.cycle} cycles after it"
            if not c.wr:
                got = int(d.sym_rsp_rdata.value)
                assert got == c.rdata, f"read word {c.addr}: {got:#010x}, model {c.rdata:#010x}"
            self.done.append(c)
        else:
            assert not self.rsp_q, f"cycle {self.cycle}: command response missing"

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


def with_locate(typ: str, locate: int, n: int | None = None) -> bytes:
    """A message of type typ (n bytes, default its length) whose header carries this locate."""
    m = bytearray(body(typ, n))
    m[1:3] = locate.to_bytes(2, "big")
    return bytes(m)


def each_class_at(locate: int) -> list[Msg]:
    """One message of every class with this locate: ORDER, TRADE, ADMIN, AUCTION, UNKNOWN (type
    0x00, 21 bytes), MALFORMED (A one byte short)."""
    msgs = [Msg(with_locate(t, locate), name=f"{c.name}@{locate}") for c, t in
            ((C.ORDER, "A"), (C.TRADE, "P"), (C.ADMIN, "R"), (C.AUCTION, "I"))]  # fmt: skip
    unknown = bytearray(bytes([0x00]) + random.randbytes(20))
    unknown[1:3] = locate.to_bytes(2, "big")
    msgs += [Msg(bytes(unknown), name=f"UNKNOWN@{locate}"), Msg(with_locate("A", locate, 35), name=f"MALFORMED@{locate}")]
    return msgs


def outcomes(dec: Dec, since: int) -> dict[str, Outcome]:
    return {m.name: r.outcome for m, r in dec.results[since:]}


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def tc_symbol_filter(dut):
    """Bit i of word w is locate 32w + i: subscribed locates at word edges (1, 31, 32, 63, 64,
    65535) pass, their neighbours are FILTERED, locate 0 always passes; a cleared bit filters
    again. Outcomes by hand, besides the model."""
    dec = await start(dut)
    await dec.wait_sweep()
    dec.class_filter = ALL_EN
    subs = [1, 31, 32, 63, 64, 65535, 1000]
    await dec.subscribe(subs)
    locs = [0, 1, 2, 30, 31, 32, 33, 62, 63, 64, 65, 999, 1000, 1001, 65534, 65535]
    n = len(dec.results)
    await dec.send([Msg(with_locate("A", loc), name=str(loc)) for loc in locs])
    await dec.idle(LATENCY + 1)
    want = {str(loc): Outcome.OUT if loc == 0 or loc in subs else Outcome.FILTERED for loc in locs}
    assert outcomes(dec, n) == want
    await dec.subscribe([32, 65535], on=False)
    n = len(dec.results)
    await dec.send([Msg(with_locate("P", loc), name=str(loc)) for loc in (31, 32, 33, 65535)])
    await dec.idle(LATENCY + 1)
    assert outcomes(dec, n) == {"31": Outcome.OUT, "32": Outcome.FILTERED, "33": Outcome.FILTERED,
                                "65535": Outcome.FILTERED}  # fmt: skip
    for w in range(2048):  # every word, through the lookup: one subscribed locate each
        await dec.write(w, 1 << (w % 32))
    n = len(dec.results)
    await dec.send([Msg(with_locate("A", 32 * w + b), name=f"{w}.{b}") for w in range(2048) for b in
                    (w % 32, (w + 1) % 32)])  # fmt: skip
    await dec.finish()
    got = outcomes(dec, n)
    assert sum(o == Outcome.OUT for o in got.values()) == 2048, "one subscribed locate per word"
    assert got["0.0"] == Outcome.OUT and got["0.1"] == Outcome.FILTERED


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def tc_class_filter(dut):
    """Every value of s_msg_class_filter with one unsubscribed message of every class: only the
    filtered classes are FILTERED (count by hand); a disabled class is MASKED before the filter;
    a message shorter than the header (locate 0) and locate 0 itself are never filtered."""
    dec = await start(dut)
    await dec.wait_sweep()  # nothing subscribed
    filtered = 0
    n = len(dec.results)
    for f in range(1 << len(C)):
        dec.class_filter = f
        await dec.send(each_class_at(7))
    await dec.idle(LATENCY + 1)
    filtered = sum(r.outcome == Outcome.FILTERED for _, r in dec.results[n:])
    assert filtered == sum(bin(f).count("1") for f in range(1 << len(C)))
    dec.class_filter = ALL_EN
    dec.set_class_en(0)
    n = len(dec.results)
    await dec.send(each_class_at(7))
    await dec.idle(LATENCY + 1)
    assert all(r.outcome == Outcome.MASKED for _, r in dec.results[n:])
    dec.set_class_en(ALL_EN)
    short = [Msg(bytes([0x00]) + b"\xff" * (k - 1), name=f"short{k}") for k in range(MIN_LEN, 11)]
    n = len(dec.results)
    await dec.send(short + each_class_at(0))
    await dec.finish()
    assert all(r.outcome == Outcome.OUT for _, r in dec.results[n:])


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_bitmap_write_during_lookup(dut):
    """A lookup in the cycle a command writes its word sees the old word (READ_FIRST, one clock);
    from the next cycle on, the new one. Set and clear, the looked-up bit and another bit of the
    same word, with the message in the cycle before, the same cycle and the cycle after."""
    dec = await start(dut)
    await dec.wait_sweep()
    dec.class_filter = ALL_EN
    loc, w = 100, 100 >> 5  # word 3, bit 4
    seq = []
    for val, name in ((1 << 4, "set"), (0, "clear"), (1 << 4 | 1 << 9, "set+other"), (1 << 9, "clear, other kept")):
        await dec.step(Msg(with_locate("A", loc), name=f"{name}: before"))
        dec.issue(Cmd(True, w, val))
        await dec.step(Msg(with_locate("A", loc), name=f"{name}: same cycle"))
        await dec.step(Msg(with_locate("A", loc), name=f"{name}: after"))
        await dec.idle(2)
        seq.append(name)
    await dec.finish()
    got = {m.name: r.outcome for m, r in dec.results}
    O, F = Outcome.OUT, Outcome.FILTERED
    want = {"set": (F, F, O), "clear": (O, O, F), "set+other": (F, F, O), "clear, other kept": (O, O, F)}
    for name in seq:
        assert tuple(got[f"{name}: {k}"] for k in ("before", "same cycle", "after")) == want[name], name
    assert all(c.cycle >= 0 for c in dec.done) and len(dec.done) == 4


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_sweep_after_reset(dut):
    """The RAM has no reset: after a reset every word is written 0 (2048 cycles). During the
    sweep sym_req_ready is 0 (a command waits and is accepted in the first cycle after it) and a
    lookup gives 0, also for words still holding 1; afterwards every word reads 0. A reset during
    the sweep starts it again. Power-up: the first sweep clears Verilator's random RAM."""
    dec = await start(dut)
    await dec.wait_sweep()
    for w in range(2048):  # power-up sweep: every word 0
        assert await dec.read(w) == 0, f"word {w} after power-up"
    for w in range(2048):
        dec.issue(Cmd(True, w, 0xFFFFFFFF))
        await dec.step()
    await dec.finish()
    assert await dec.read(2047) == 0xFFFFFFFF
    dec.class_filter = ALL_EN
    for again in (False, True):
        await dec.reset(1)
        start_cycle = dec.cycle + 1  # first cycle after the reset
        if again:  # reset again in the middle of the sweep
            await dec.idle(1000)
            await dec.reset(2)
            start_cycle = dec.cycle + 1
        cmd = dec.issue(Cmd(False, 2047))
        n = len(dec.results)
        for i in range(SWEEP):  # every cycle a message on a word not yet swept (from the top)
            await dec.step(Msg(with_locate("A", 65535 - 32 * i), name=f"sweep {i}"))
        await dec.idle(4)
        assert all(r.outcome == Outcome.FILTERED for _, r in dec.results[n:]), "lookup during the sweep"
        assert cmd.cycle == start_cycle + SWEEP, f"command accepted {cmd.cycle - start_cycle} cycles after the reset"
        assert cmd.rdata == 0
    for w in range(2048):
        assert await dec.read(w) == 0, f"word {w} after the sweep"
    await dec.finish()


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_sym_read_write(dut):
    """Every byte strobe on random words (the result by hand), back-to-back commands (one per
    cycle, valid held), and commands interleaved with message traffic through the filter."""
    dec = await start(dut)
    await dec.wait_sweep()
    for strb in range(16):
        w = random.randrange(2048)
        old = random.getrandbits(32)
        await dec.write(w, old)
        new = random.getrandbits(32)
        await dec.write(w, new, strb)
        mask = sum(0xFF << 8 * b for b in range(4) if strb >> b & 1)
        assert await dec.read(w) == (old & ~mask) | (new & mask), f"strobe {strb:04b}"
    words = random.sample(range(2048), 64)
    for w in words:  # back to back: the next command in the cycle after acceptance
        dec.issue(Cmd(True, w, random.getrandbits(32), random.getrandbits(4)))
        await dec.step()
    for w in words:
        dec.issue(Cmd(False, w))
        await dec.step()
    dec.class_filter = ALL_EN
    for i in range(3000):  # traffic with commands at random, the filter on
        if dec.pending is None and random.random() < 0.3:
            w = random.randrange(2048)
            dec.issue(Cmd(random.random() < 0.6, w, random.getrandbits(32), random.getrandbits(4)))
        loc = random.choice([0, random.randrange(65536), 32 * random.choice(words) + random.randrange(32)])
        await dec.step(Msg(with_locate(random.choice("AFEPQ"), loc)) if random.random() < 0.8 else None)
    await dec.finish()
    assert sum(c.wr for c in dec.done) > 100 and sum(not c.wr for c in dec.done) > 100
    assert {r.outcome for _, r in dec.results} >= {Outcome.OUT, Outcome.FILTERED}


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
