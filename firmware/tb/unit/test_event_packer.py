"""event_packer against the ring model (tb/model/ring.py, audit/13 §2.2).

The driver gives what itch_decoder gives: output messages, then the packet event, with the spacing of
moldudp64_rx (a packet event, then no message for 2 cycles). The monitor is a model of the COMMIT=1 FIFO:
it holds the written words until the commit or rollback and compares each committed unit, byte by byte, with
expect_entries(). Per cycle it also checks that writes and ends come in the exact cycle the module header
states, and the counters. Which packets are dropped comes from the FIFO model's own full flag at the cycle of
each write: forced by a test, or from its fill level when the reader is slow.
"""

import random
from dataclasses import dataclass, field
from pathlib import Path

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

from common.clocks import start_clock
from common.coverage import Coverage
from common.runner import run_cocotb
from model import desc_gen
from model.desc_gen import FLAG_OVERFLOW_BEFORE, Kind
from model.itch import Bitmap, Event, ItchClass, ItchConfig, Outcome, encode, expect, itch_stream, wire
from model.mold import ACCEPTED, MAX_MSG, MoldStatus
from model.ring import MAX_PKT_ENTRIES, RingPacket, expect_entries
from selftest.desc_hand import HAND_EOS, HAND_GAP, HAND_ITCH, HAND_RAW

PERIOD_PS = 6400
N_RANDOM = 100_000  # messages of tc_random_golden (audit/13 §2.10)
MAX_RANDOM = 3 * N_RANDOM  # a bin still empty here fails the coverage check instead of looping for ever
S = MoldStatus
REJECTED = [s for s in S if s not in ACCEPTED]
REJECTED_WITH_MSGS = (S.ABORT, S.UNSUPPORTED, S.MALFORMED)  # the others output no message (model/mold.py)
EV_PORTS = ("order_ref", "new_ref", "shares", "price", "match", "side", "stock", "attribution", "printable",
            "cross_type")  # fmt: skip
EV_GARBAGE = {"seq": 64, "sof_ts": 64, "epoch": 8, "locate": 16, "tracking": 16, "ts": 48, "type": 8, "len": 7,
              "raw": 8 * MAX_MSG, "order_ref": 64, "new_ref": 64, "shares": 64, "price": 32, "match": 64, "side": 8,
              "stock": 64, "attribution": 32, "printable": 8, "cross_type": 8, "class": 3}  # fmt: skip
PKT_GARBAGE = {"status": 4, "seq": 64, "gap": 64, "sof_ts": 64, "epoch": 8}


@dataclass
class Pkt:
    status: MoldStatus = S.OK
    seq: int = field(default_factory=lambda: random.getrandbits(64))
    gap: int = 0
    sof_ts: int = field(default_factory=lambda: random.getrandbits(64))
    epoch: int = field(default_factory=lambda: random.getrandbits(8))
    events: list[Event] = field(default_factory=list)
    full_at: int | None = None  # force the FIFO full at this entry (0 = the first) of the packet
    busy_at: int | None = None  # the same, with the FIFO busy
    dropped: bool = False  # set by the monitor: a write of this packet found no room
    tried: int = 0  # entries scheduled so far

    def ring(self) -> RingPacket:
        msgs = tuple(((self.seq + i) % (1 << 64), ev) for i, ev in enumerate(self.events))
        return RingPacket(self.status, self.seq, self.gap, self.sof_ts, self.epoch, msgs, self.dropped)


class Packer:
    """Driver and FIFO model. Cycles: inputs driven after edge d are sampled at edge d + 1, and event_packer's
    outputs for them hold in the cycle after edge d + 1: output cycle d + 1."""

    def __init__(self, dut, depth: int = 256, p_read: float = 1.0, keep: bool = True) -> None:
        self.dut, self.depth, self.p_read, self.keep = dut, depth, p_read, keep
        self.d = 0  # driver's edge count
        self.m = 0  # monitor's edge count
        self.attempts: dict[int, Pkt] = {}
        self.ends: dict[int, Pkt] = {}
        self.force_full: set[int] = set()
        self.force_busy: set[int] = set()
        self.eos_slot = -1  # input cycle of a pending EOS entry
        self.unit: list[bytes] = []
        self.unread = 0  # committed words the reader has not taken
        self.pending = False  # overflow_before waiting for a committed entry
        self.commit_entries = self.drop_pkts = self.drop_entries = 0
        self.committed: list[bytes] = []  # every committed entry (keep)
        self.done: list[Pkt] = []  # every finished packet (keep)
        self.packets = 0
        self.max_unit = 0
        self.on_end = None  # called with (pkt, committed unit) at each end
        self.paused = False  # during a reset: the model is cleared with the DUT

    def garbage(self) -> None:
        d = self.dut
        d.s_ev_valid.value = 0
        d.s_pkt_valid.value = 0
        for k, w in EV_GARBAGE.items():
            getattr(d, f"s_ev_{k}").value = random.getrandbits(w)
        for k, w in PKT_GARBAGE.items():
            getattr(d, f"s_pkt_{k}").value = random.getrandbits(w)

    def _try_entry(self, pkt: Pkt, out_cycle: int) -> None:
        self.attempts[out_cycle] = pkt
        if pkt.full_at == pkt.tried:
            self.force_full.add(out_cycle)
        if pkt.busy_at == pkt.tried:
            self.force_busy.add(out_cycle)
        pkt.tried += 1

    async def step(self, ev: Event | None = None, pkt: Pkt | None = None, index: int = 0) -> None:
        """One cycle: a message of pkt (with its index), or pkt's event, or idle."""
        d = self.dut
        self.garbage()
        out = self.d + 1
        if ev is not None:
            d.s_ev_valid.value = 1
            d.s_ev_class.value = int(ev.cls)
            d.s_ev_type.value = ev.type
            d.s_ev_len.value = ev.length
            d.s_ev_seq.value = (pkt.seq + index) % (1 << 64)
            d.s_ev_sof_ts.value = pkt.sof_ts
            d.s_ev_epoch.value = pkt.epoch
            d.s_ev_locate.value = ev.locate
            d.s_ev_tracking.value = ev.tracking
            d.s_ev_ts.value = ev.timestamp
            for k in EV_PORTS:
                getattr(d, f"s_ev_{k}").value = getattr(ev, k)
            d.s_ev_raw.value = int.from_bytes(ev.raw.ljust(MAX_MSG, b"\0"), "little")
            self._try_entry(pkt, out)
        elif pkt is not None:
            d.s_pkt_valid.value = 1
            d.s_pkt_status.value = int(pkt.status)
            d.s_pkt_seq.value = pkt.seq
            d.s_pkt_gap.value = pkt.gap
            d.s_pkt_sof_ts.value = pkt.sof_ts
            d.s_pkt_epoch.value = pkt.epoch
            if self.d == self.eos_slot:
                assert pkt.status not in ACCEPTED and not pkt.events, "only an empty rejected packet may come here"
                self.done_without_end(pkt)
            else:
                self._schedule_end(pkt, out)
        await RisingEdge(d.clk)
        self.d += 1

    def _schedule_end(self, pkt: Pkt, out: int) -> None:
        if pkt.status not in ACCEPTED:
            self.ends[out] = pkt
            return
        extra = bool(pkt.gap) + (pkt.status == S.EOS)
        for i in range(extra):
            self._try_entry(pkt, out + i)
        self.ends[out + max(extra - 1, 0)] = pkt
        if extra == 2:
            self.eos_slot = self.d + 1

    def done_without_end(self, pkt: Pkt) -> None:
        self.packets += 1
        if self.keep:
            self.done.append(pkt)

    async def send(self, pkt: Pkt, gaps: bool = False, after: int = 2) -> None:
        """A packet: its messages one per cycle (gaps: 0..2 idle cycles among them), its event, then `after`
        idle cycles (2: the shortest the contract allows before a message)."""
        for i, ev in enumerate(pkt.events):
            await self.step(ev, pkt, i)
            for _ in range(random.randint(0, 2) if gaps else 0):
                await self.step()
        await self.step(pkt=pkt)
        for _ in range(after):
            await self.step()

    async def monitor(self) -> None:
        d = self.dut
        while True:
            await RisingEdge(d.clk)
            self.m += 1
            await FallingEdge(d.clk)
            full = self.m in self.force_full or self.unread + len(self.unit) >= self.depth
            busy = self.m in self.force_busy
            d.fifo_full.value = int(full)
            d.fifo_wr_busy.value = int(busy)
            await Timer(1, "ns")
            await ReadOnly()
            if not self.paused:
                self._check(full, busy)

    def _check(self, full: bool, busy: bool) -> None:
        d, m = self.dut, self.m
        got = (int(d.cnt_commit_entries.value), int(d.cnt_drop_pkts.value), int(d.cnt_drop_entries.value))
        want = (self.commit_entries, self.drop_pkts, self.drop_entries)
        assert got == want, f"cycle {m}: counters (commit, drop pkts, drop entries) {got}, model {want}"
        wr, commit, rollback = bool(d.fifo_wr_en.value), bool(d.fifo_commit.value), bool(d.fifo_rollback.value)

        pkt = self.attempts.pop(m, None)
        if pkt is not None and (full or busy):
            pkt.dropped = True
        assert wr == (pkt is not None and not pkt.dropped), f"cycle {m}: fifo_wr_en {wr}, packet {pkt}"
        if wr:
            self.unit.append(int(d.fifo_din.value).to_bytes(128, "little"))
            self.max_unit = max(self.max_unit, len(self.unit))

        ended = self.ends.pop(m, None)
        if ended is None:
            assert not (commit or rollback), f"cycle {m}: a commit or rollback with no packet ending"
        else:
            self._end(ended, commit, rollback)
        if self.unread and random.random() < self.p_read:
            self.unread -= 1

    def _end(self, pkt: Pkt, commit: bool, rollback: bool) -> None:
        keep = pkt.status in ACCEPTED and not pkt.dropped
        assert (commit, rollback) == (keep, not keep), f"cycle {self.m}: commit {commit} rollback {rollback}, {pkt}"
        r = expect_entries([pkt.ring()], self.pending)
        self.pending = r.overflow_pending
        self.drop_pkts += r.drop_pkts
        self.drop_entries += r.drop_entries
        if keep:
            assert self.unit == r.entries, f"cycle {self.m}: committed entries differ from the model, {pkt}"
            self.commit_entries += len(r.entries)
            self.unread += len(self.unit)
            if self.keep:
                self.committed += self.unit
        self.packets += 1
        if self.keep:
            self.done.append(pkt)
        if self.on_end:
            self.on_end(pkt, r, self.unit if keep else [])
        self.unit = []

    async def drain(self) -> None:
        """Idle until every packet has ended and every counter has been checked."""
        for _ in range(4):
            await self.step()
        assert not self.attempts and not self.ends, f"left over: {self.attempts} {self.ends}"
        assert not self.unit, "words written and never committed or rolled back"


async def start(dut, **kw) -> Packer:
    """Idle inputs and reset first, then the clock."""
    p = Packer(dut, **kw)
    p.garbage()
    dut.fifo_full.value = 0
    dut.fifo_wr_busy.value = 0
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    p.garbage()
    await RisingEdge(dut.clk)
    cocotb.start_soon(p.monitor())
    return p


def out_event(msg: bytes) -> Event:
    return expect(msg, ItchConfig(), Bitmap())[0]


def order(ref: int = 1) -> Event:
    return out_event(encode("A", order_ref=ref, stock="AAPL    ", side="B"))


def admin() -> Event:
    return out_event(encode("S", event_code="O"))


def orders(n: int) -> list[Event]:
    return [order(i + 1) for i in range(n)]


def decoded(p: Packer) -> list[dict]:
    return [desc_gen.decode(e) for e in p.committed]


def clear_writer_fields(entry: bytes) -> bytes:
    """A hand-written entry as event_packer gives it: ring_idx and the ctrl bytes are the writer's."""
    out = bytearray(entry)
    out[16:20] = bytes(4)
    out[63] = out[127] = 0
    return bytes(out)


@cocotb.test()
async def tc_hand_entries(dut):
    """Each kind against its hand-written bytes (selftest/desc_hand.py), independent of the model."""
    p = await start(dut)
    seq, ts = 0x0102030405060708, 0x1112131415161718
    trade = Event(
        ItchClass.TRADE, 0x50, 44, bytes(44), 0x3132, 0x3334, 0x414243444546, order_ref=0x6162636465666768,
        new_ref=0x7172737475767778, shares=0x8182838485868788, price=0x51525354, match=0x9192939495969798,
        side=0x42, stock=wire(b"AAPL    "), attribution=wire(b"ABCD"), printable=0x59, cross_type=0x4F,
    )  # fmt: skip
    raw = bytes(range(64))
    # overflow_before 1 in the ITCH and EOS entries: the packet before each one is dropped.
    await p.send(Pkt(events=[order()], full_at=0))
    await p.send(Pkt(S.OK, seq, 0, ts, 0x5A, [trade]))
    await p.send(Pkt(S.OK, seq, 0, ts, 0xA5, [Event(ItchClass.UNKNOWN, raw[0], 64, raw)]))
    await p.send(Pkt(S.HEARTBEAT, 0xA1A2A3A4A5A6A7A8, 0xB1B2B3B4B5B6B7B8, ts, 7))
    await p.send(Pkt(events=[order()], full_at=0))
    await p.send(Pkt(S.EOS, seq, 0, ts, 0xFF))
    await p.drain()
    got = p.committed
    assert got[0] == clear_writer_fields(HAND_ITCH)
    assert got[1] == clear_writer_fields(HAND_RAW)
    # GAP: the hand-written entry states seq directly; here it is first_seq - gap.
    assert got[2][8:] == clear_writer_fields(HAND_GAP)[8:]
    assert got[2][:8] == ((0xA1A2A3A4A5A6A7A8 - 0xB1B2B3B4B5B6B7B8) % (1 << 64)).to_bytes(8, "little")
    assert got[3] == clear_writer_fields(HAND_EOS)


@cocotb.test()
async def tc_classes_pick_the_kind(dut):
    """Each class goes to its kind; a message that is filtered or masked has no event and no entry."""
    p = await start(dut)
    msgs = {
        ItchClass.ORDER: encode("A"), ItchClass.TRADE: encode("P"), ItchClass.ADMIN: encode("S"),
        ItchClass.AUCTION: encode("N"), ItchClass.UNKNOWN: bytes([0xFF]) + bytes(11), ItchClass.MALFORMED: encode("A")[:-1],
    }  # fmt: skip
    for cls, msg in msgs.items():
        assert out_event(msg).cls == cls
        await p.send(Pkt(events=[out_event(msg)]))
    masked = expect(encode("A"), ItchConfig(class_en=0), Bitmap())[0]
    assert masked is None
    await p.send(Pkt(events=[]))  # the masked message's packet: nothing for the packer
    await p.drain()
    want = [Kind.ITCH, Kind.ITCH, Kind.ITCH_RAW, Kind.ITCH_RAW, Kind.ITCH_RAW, Kind.ITCH_RAW]
    assert [d["kind"] for d in decoded(p)] == want
    assert [d["itch_class"] for d in decoded(p)] == [int(c) for c in msgs]


@cocotb.test()
async def tc_statuses(dut):
    """Every status: the messages of an accepted packet stay, of any other go, and nothing is counted."""
    p = await start(dut)
    for status in S:
        events = orders(3) if status == S.OK or status in REJECTED_WITH_MSGS else []
        await p.send(Pkt(status, events=events))
    await p.drain()
    assert len(p.committed) == 3 + 1  # OK's messages, EOS's entry
    assert (p.drop_pkts, p.drop_entries) == (0, 0)


@cocotb.test()
async def tc_gap_and_eos_order(dut):
    """GAP after the messages, EOS last, seq of the missing message, in the event's sof_ts and epoch."""
    p = await start(dut)
    await p.send(Pkt(S.OK, 100, 5, 7, 9, orders(2)))
    await p.send(Pkt(S.EOS, 200, 3, 8, 10))
    await p.send(Pkt(S.HEARTBEAT, 2, 5, 9, 11))
    await p.send(Pkt(S.HEARTBEAT, 300))  # no gap, no entry
    await p.drain()
    d = decoded(p)
    assert [e["kind"] for e in d] == [Kind.ITCH, Kind.ITCH, Kind.GAP, Kind.GAP, Kind.EOS, Kind.GAP]
    assert (d[2]["seq"], d[2]["first_seq"], d[2]["gap"], d[2]["sof_ts"], d[2]["cfg_epoch"]) == (95, 100, 5, 7, 9)
    assert (d[3]["seq"], d[3]["first_seq"], d[3]["gap"]) == (197, 200, 3)
    assert (d[4]["seq"], d[4]["sof_ts"], d[4]["cfg_epoch"]) == (200, 8, 10)
    assert d[5]["seq"] == (1 << 64) - 3


@cocotb.test()
async def tc_rejected_packet_right_after_gap_and_eos(dut):
    """A packet of 1 or 2 beats has no message and may end 1 cycle after an event: in the cycle of the pending
    EOS entry, or the one after. Neither disturbs that entry."""
    p = await start(dut)
    await p.send(Pkt(S.EOS, 100, 2), after=0)
    await p.send(Pkt(S.RUNT), after=0)
    await p.send(Pkt(S.DISABLED), after=2)
    await p.send(Pkt(S.EOS, 200, 2), after=1)
    await p.send(Pkt(S.OTHER_FEED), after=2)
    await p.send(Pkt(S.OK, events=orders(1)))
    await p.drain()
    assert [d["kind"] for d in decoded(p)] == [Kind.GAP, Kind.EOS, Kind.GAP, Kind.EOS, Kind.ITCH]


@cocotb.test()
async def tc_drop_is_whole(dut):
    """A write that finds no room, at the first, a middle or the last message, at GAP or EOS: nothing of the
    packet is delivered and all its entries are counted. The next packet is whole."""
    p = await start(dut)
    for full_at in (0, 2, 4, 5, 6):  # 5 messages, GAP, EOS
        await p.send(Pkt(S.EOS, gap=1, events=[], full_at=None))  # a clean packet in between
        seen = len(p.committed)
        await p.send(Pkt(S.OK, events=orders(5), gap=1, full_at=full_at if full_at < 6 else None))
        await p.drain()
        assert len(p.committed) == seen + (6 if full_at == 6 else 0), f"full_at {full_at}"
    assert p.drop_pkts == 4
    assert p.drop_entries == 4 * 6


@cocotb.test()
async def tc_busy_drops_as_full_does(dut):
    """A FIFO in reset (busy, not full) drops the packet the same way."""
    p = await start(dut)
    await p.send(Pkt(events=orders(3), busy_at=1))
    await p.send(Pkt(events=orders(1)))
    await p.drain()
    assert (p.drop_pkts, p.drop_entries) == (1, 3)
    assert [d["flags"] for d in decoded(p)] == [FLAG_OVERFLOW_BEFORE]


@cocotb.test()
async def tc_overflow_flag(dut):
    """overflow_before on the first committed entry after a drop, once, kept over rejected packets and empty
    commits, and on a GAP or EOS when that is the first."""
    p = await start(dut)
    await p.send(Pkt(events=orders(2), full_at=0))
    await p.send(Pkt(S.ABORT, events=orders(2)))
    await p.send(Pkt(S.DUP))
    await p.send(Pkt(S.HEARTBEAT))
    await p.send(Pkt(events=orders(2)))
    await p.send(Pkt(events=orders(1)))
    await p.drain()
    assert [d["flags"] for d in decoded(p)] == [FLAG_OVERFLOW_BEFORE, 0, 0]

    p.committed.clear()
    await p.send(Pkt(events=orders(1), full_at=0))
    await p.send(Pkt(events=orders(1), full_at=0))
    await p.send(Pkt(S.HEARTBEAT, 500, 1))  # GAP takes it
    await p.send(Pkt(events=orders(1), full_at=0))
    await p.send(Pkt(S.EOS, 600))  # EOS takes it
    await p.send(Pkt(events=orders(1), full_at=0))
    await p.send(Pkt(S.EOS, 700, 1))  # GAP takes it, not its EOS
    await p.drain()
    assert [d["flags"] for d in decoded(p)] == [FLAG_OVERFLOW_BEFORE, FLAG_OVERFLOW_BEFORE, FLAG_OVERFLOW_BEFORE, 0]


@cocotb.test()
async def tc_rejected_packet_that_does_not_fit_is_no_loss(dut):
    """User decision (audit/13 S1): no count, no flag, for a packet that would have been rolled back anyway."""
    p = await start(dut)
    for status in REJECTED_WITH_MSGS:
        await p.send(Pkt(status, events=orders(3), full_at=1))
    await p.send(Pkt(events=orders(1)))
    await p.drain()
    assert (p.drop_pkts, p.drop_entries) == (0, 0)
    assert [d["flags"] for d in decoded(p)] == [0]


@cocotb.test()
async def tc_flag_goes_with_the_packet_that_commits(dut):
    """A flagged first entry that is rolled back or dropped does not use up the flag."""
    p = await start(dut)
    await p.send(Pkt(events=orders(1), full_at=0))
    await p.send(Pkt(S.MALFORMED, events=orders(2)))  # its first entry was written with the flag
    await p.send(Pkt(events=orders(3), full_at=2))  # the same, and it drops
    await p.send(Pkt(events=orders(2)))
    await p.drain()
    assert [d["flags"] for d in decoded(p)] == [FLAG_OVERFLOW_BEFORE, 0]


@cocotb.test()
async def tc_largest_packet_fits_an_empty_fifo(dut):
    """The largest packet (RING_MAX_PKT_ENTRIES entries: 181 messages, GAP, EOS) in a 256-word FIFO with no
    reader: nothing drops; a FIFO one word too small drops it whole."""
    p = await start(dut, depth=256, p_read=0.0)
    assert MAX_PKT_ENTRIES == 183
    await p.send(Pkt(S.EOS, gap=1, events=orders(181)))
    await p.drain()
    assert p.max_unit == MAX_PKT_ENTRIES
    assert (len(p.committed), p.drop_pkts) == (MAX_PKT_ENTRIES, 0)

    p.depth = p.unread + MAX_PKT_ENTRIES - 1
    await p.send(Pkt(S.EOS, gap=1, events=orders(181)))
    await p.drain()
    assert (len(p.committed), p.drop_pkts, p.drop_entries) == (MAX_PKT_ENTRIES, 1, MAX_PKT_ENTRIES)


@cocotb.test()
async def tc_slow_reader(dut):
    """A FIFO that fills because its reader is slow: whole packets drop, the model and counters agree."""
    p = await start(dut, depth=32, p_read=0.1)
    for _ in range(300):
        await p.send(Pkt(events=orders(random.randint(1, 8))))
    await p.drain()
    assert p.drop_pkts > 0 and p.commit_entries > 0


@cocotb.test()
async def tc_reset_mid_packet(dut):
    """rst with a packet half written and a flag pending clears all of it, counters included."""
    p = await start(dut)
    await p.send(Pkt(events=orders(1), full_at=0))
    await p.send(Pkt(S.OK, events=orders(1)))
    half = Pkt(events=orders(2))
    await p.step(half.events[0], half, 0)
    await p.step(half.events[1], half, 1)

    p.paused = True
    await FallingEdge(dut.clk)
    p.garbage()  # the last step left its message driven
    dut.rst.value = 1
    for _ in range(3):
        await RisingEdge(dut.clk)
        p.d += 1
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    p.attempts.clear()
    p.ends.clear()
    p.unit, p.unread, p.pending = [], 0, False
    p.commit_entries = p.drop_pkts = p.drop_entries = 0
    p.force_full.clear()
    await RisingEdge(dut.clk)
    p.d += 1
    p.paused = False
    p.committed.clear()
    await p.send(Pkt(S.HEARTBEAT, 10, 1))  # no flag, and a commit of this GAP alone
    await p.send(Pkt(events=orders(1)))
    await p.drain()
    d = decoded(p)
    assert [e["kind"] for e in d] == [Kind.GAP, Kind.ITCH]
    assert [e["flags"] for e in d] == [0, 0]
    assert p.commit_entries == 2


def scenarios() -> list[tuple[MoldStatus, bool, bool, str | None]]:
    """(status, has messages, has a gap, where the FIFO is full): every legal combination."""
    out = []
    for gap in (False, True):
        for where in (None, "msg", *(("gap",) if gap else ())):
            out.append((S.OK, True, gap, where))
        for where in (None, *(("gap",) if gap else ())):
            out.append((S.OK, False, gap, where))
            out.append((S.HEARTBEAT, False, gap, where))
        for where in (None, "eos", *(("gap",) if gap else ())):
            out.append((S.EOS, False, gap, where))
    for status in REJECTED_WITH_MSGS:
        out += [(status, True, False, None), (status, True, False, "msg")]
    for status in set(REJECTED) - set(REJECTED_WITH_MSGS):
        out.append((status, False, False, None))
    return out


def scenario_bin(s: tuple) -> str:
    status, msgs, gap, where = s
    return f"{status.name}_{'msgs' if msgs else 'empty'}_{'gap' if gap else 'nogap'}_full_{where}"


@cocotb.test(timeout_time=30, timeout_unit="sec")
async def tc_random_golden(dut):
    """10^5 messages through every legal packet shape with random spacing and FIFO pressure. The shapes come
    from a shuffled bag and the reader speed from a fixed cycle, so coverage does not depend on the seed."""
    p = await start(dut, depth=256, p_read=0.5, keep=False)
    bitmap = Bitmap()
    bitmap.set(1)
    cfg = ItchConfig(class_filter=0x3F)
    stream = itch_stream(random.Random(random.getrandbits(32)), cfg, bitmap)
    shapes = scenarios()
    kinds = ["ITCH", "ITCH_RAW", "GAP", "EOS"]
    classes = [c.name for c in ItchClass]
    bins = [scenario_bin(s) for s in shapes] + [f"kind_{k}" for k in kinds] + [f"class_{c}" for c in classes]
    bins += ["flag_on_ITCH", "flag_on_ITCH_RAW", "flag_on_GAP", "flag_on_EOS", "pressure_drop", "outcome_FILTERED",
             "outcome_OUT", "after_0_rejected"]  # fmt: skip
    cov = Coverage("event_packer", bins)

    def on_end(pkt, r, unit) -> None:
        for e in unit:
            kind = Kind(e[20])
            cov.hit(f"kind_{kind.name}")
            if e[21] & FLAG_OVERFLOW_BEFORE:
                cov.hit(f"flag_on_{kind.name}")
        if pkt.dropped and pkt.full_at is None and pkt.status in ACCEPTED:
            cov.hit("pressure_drop")

    p.on_end = on_end
    sent = next_log = 0
    bag: list = []
    while sent < N_RANDOM or (cov.empty() and sent < MAX_RANDOM):
        if sent >= next_log:
            dut._log.info(f"{sent} messages, {p.drop_pkts} packets dropped, empty bins: {cov.empty()}")
            next_log += N_RANDOM // 10
        if not bag:
            bag = shapes[:]
            random.shuffle(bag)
        shape = bag.pop()
        status, has_msgs, gap, where = shape
        cov.hit(scenario_bin(shape))
        pkt = Pkt(status, gap=random.randint(1, 1 << 40) if gap else 0)
        if has_msgs:
            n = random.randint(1, 30)
            while len(pkt.events) < n:
                item = next(stream)
                ev, res = expect(item.msg, cfg, bitmap)
                if res.outcome != Outcome.MASKED:
                    cov.hit(f"outcome_{res.outcome.name}")
                if ev is not None:
                    cov.hit(f"class_{ev.cls.name}")
                    pkt.events.append(ev)
        count = len(pkt.events)
        if where == "msg":
            pkt.full_at = random.randrange(count)
        elif where == "gap":
            pkt.full_at = count
        elif where == "eos":
            pkt.full_at = count + bool(gap)
        # Bursts of pressure. A packet commits 1.3 entries on average, so it takes some 200 packets of a nearly
        # stopped reader to fill the FIFO for real: 384 of them fill it whatever the seed.
        p.p_read = 0.01 if p.packets % 1024 < 384 else 1.0
        await p.send(pkt, gaps=random.random() < 0.3, after=2 + random.randint(0, 2))
        if random.random() < 0.05:
            # A short rejected packet right after an event: in the shortest spacing the framer can give.
            await p.send(Pkt(S.RUNT), after=2)
            cov.hit("after_0_rejected")
        sent += count
    await p.drain()
    assert p.drop_pkts > 0
    dut._log.info(f"{p.packets} packets, {p.commit_entries} entries, {p.drop_pkts} packets dropped")
    cov.check(Path.cwd())


LONG_TESTS = ("tc_random_golden",)


def directed_tests() -> list[str]:
    return [name for name in globals() if name.startswith("tc_") and name not in LONG_TESTS]


@pytest.mark.parametrize("group", ["directed", *LONG_TESTS])
def test_event_packer(group):
    run_cocotb(
        toplevel="event_packer",
        filelists=["common.f", "core.f"],
        test_module="test_event_packer",
        testcase=directed_tests() if group == "directed" else group,
        extra_env={"GROUP": group},
    )
