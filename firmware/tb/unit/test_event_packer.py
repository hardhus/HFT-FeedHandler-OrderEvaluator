"""event_packer against the ring model (tb/model/ring.py, audit/13 §2.2, audit/14 P8).

The driver gives what itch_decoder gives: output messages, then the packet event, with the spacing of
moldudp64_rx (a packet event, then no message for 2 cycles). It also gives order entries as order_tx_builder and
risk_check do (ORDER_SENT, RISK_REJECT, a lost reject), and keeps their ring credits as risk_check does. The
monitor is a model of the COMMIT=1 FIFO: it holds the written words until the commit or rollback and compares
each committed unit, byte by byte, with expect_entries(). Per cycle it also checks that writes and ends come in
the exact cycle the module header states, and the counters. Which packets are dropped comes from the FIFO model's
own full flag at the cycle of each write: forced by a test, or from its fill level when the reader is slow.

Order entries: the monitor keeps its own model of the order queue (audit/14 decisions 25, 28-31). It works out
from the inputs it sees in each cycle whether an order entry may be taken (no pending EOS, no message, no packet
event, no open packet), so the cycle of every order write, wait and retry is computed, not read from the RTL
(audit/13 S7). The overflow_before flag is kept in ring order: set by a dropped packet or by a lost reject when
its place comes, taken by the next committed entry.
"""

import bisect
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
from model.order import OrderReq, Source
from model.ring import MAX_PKT_ENTRIES, RingOrder, RingPacket, expect_entries, order_entry
from model.risk import Reason
from selftest.desc_hand import HAND_EOS, HAND_GAP, HAND_ITCH, HAND_ORDER_SENT, HAND_RAW, HAND_RISK_REJECT

PERIOD_PS = 6400
N_RANDOM = 100_000  # messages of tc_random_golden (audit/13 §2.10)
MAX_RANDOM = 3 * N_RANDOM  # a bin still empty here fails the coverage check instead of looping for ever
ORD_Q = 16  # RISK_RING_CREDITS: the order queue and the credits risk_check gives out
S = MoldStatus
REJECTED = [s for s in S if s not in ACCEPTED]
REJECTED_WITH_MSGS = (S.ABORT, S.UNSUPPORTED, S.MALFORMED)  # the others output no message (model/mold.py)
EV_PORTS = ("order_ref", "new_ref", "shares", "price", "match", "side", "stock", "attribution", "printable",
            "cross_type")  # fmt: skip
EV_GARBAGE = {"seq": 64, "sof_ts": 64, "epoch": 8, "locate": 16, "tracking": 16, "ts": 48, "type": 8, "len": 7,
              "raw": 8 * MAX_MSG, "order_ref": 64, "new_ref": 64, "shares": 64, "price": 32, "match": 64, "side": 8,
              "stock": 64, "attribution": 32, "printable": 8, "cross_type": 8, "class": 3}  # fmt: skip
PKT_GARBAGE = {"status": 4, "seq": 64, "gap": 64, "sof_ts": 64, "epoch": 8}
ORD_GARBAGE = {"source": 2, "rule": 3, "side": 8, "qty": 32, "price": 32, "locate": 16, "stock": 64, "tif": 8,
               "clordid": 112, "seq": 64, "sof_ts": 64, "epoch": 8}  # fmt: skip
SENT_GARBAGE = {**ORD_GARBAGE, "user_ref": 32, "tx_ts": 64}
REJ_GARBAGE = {**ORD_GARBAGE, "reason": 4, "ts": 64}
# Entry field -> port suffix (the rest have the same name).
ORD_FIELD_PORT = {"cfg_epoch": "epoch", "tx_ts": "tx_ts", "reject_ts": "ts"}
ALPHA = ("stock", "clordid")
ORDER_BINS = ["kind_ORDER_SENT", "kind_RISK_REJECT", "flag_on_ORDER_SENT", "flag_on_RISK_REJECT", "ord_idle",
              "ord_waits_packet", "ord_same_cycle", "ord_full_wait", "ord_retry_cancels_next", "ord_queue_full",
              "lost_head", "lost_after_sent", "lost_after_tail", "lost_before_cancel", "lost_tail_written_now",
              "lost_in_packet", "lost_taken_late"]  # fmt: skip

# The hardware and host orders of the hand-written entries (tb/selftest/test_ring_model.py).
HW_ORDER = OrderReq(
    Source.RULE, ord("B"), 0x51525354, 0x61626364, 0x4142, b"MSFT    ", ord("E"), rule=7, seq=0x0102030405060708,
    sof_ts=0x1112131415161718, epoch=0x3C,
)  # fmt: skip
HOST_ORDER = OrderReq(
    Source.HOST, ord("S"), 999_999, 0x7735939C, 0xFFFE, b"AB      ", ord("3"), clordid=b"ABCxyz 0123   ",
    seq=0x0102030405060708, sof_ts=0x1112131415161718, epoch=0xC3,
)  # fmt: skip


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


@dataclass
class Ord:
    """An order entry in the monitor's queue model."""

    ring: RingOrder
    arrived: int  # output cycle of its input
    lost_after: bool = False  # a lost reject stands right after it
    written: int | None = None  # output cycle of its write
    tries: int = 0


def random_order(reject: bool, cancel: bool = False) -> RingOrder:
    host = random.random() < 0.5
    req = OrderReq(
        Source.HOST if host else Source.RULE, random.choice(b"BS"), random.getrandbits(32), random.getrandbits(32),
        random.getrandbits(16), bytes(random.choice(b"ABCDXYZ ") for _ in range(8)), random.choice(b"035E"),
        rule=0 if host else random.randrange(8), clordid=bytes(random.choice(b"0aZ ") for _ in range(14)) if host else None,
        seq=0 if host else random.getrandbits(64), sof_ts=0 if host else random.getrandbits(64), epoch=random.getrandbits(8),
    )  # fmt: skip
    if reject:
        reason = random.choice([Reason.KILL, Reason.TX_OFF]) if cancel else random.choice([r for r in Reason if r])
        return RingOrder(req, reason, ts=random.getrandbits(64), cancel=cancel)
    return RingOrder(req, user_ref=random.getrandbits(32), ts=random.getrandbits(64))


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
        self.counted = [0, 0, 0]  # what the counters must show now: they move 3 cycles after the event
        self.count_q: list[tuple[int, int, int, int]] = []  # (cycle shown, commit entries, drop pkts, entries)
        self.committed: list[bytes] = []  # every committed entry (keep)
        self.done: list[Pkt] = []  # every finished packet (keep)
        self.packets = 0
        self.max_unit = 0
        self.on_end = None  # called with (pkt, committed unit) at each end
        self.paused = False  # during a reset: the model is cleared with the DUT
        # Order entries: driver side
        self.order_src = None  # called by step() for its order inputs when a test gives none
        self.to_sent: list[RingOrder] = []  # driven, not seen by the monitor yet
        self.to_rej: list[RingOrder] = []
        self.outstanding = 0  # credits in use (driven entries whose ring_credit_ret has not been seen)
        # Order entries: monitor side
        self.oq: list[Ord] = []  # the queue, the one in stage 1 first
        self.os1 = False  # oq[0] is in stage 1
        self.lost_pend = False  # a lost reject at the head of the queue
        self.m_eos = False  # a pending EOS entry in this cycle's stage 0
        self.m_empty = True  # no message of an open packet
        self.credit_due = False  # ring_credit_ret expected in this cycle
        self.orders: list[Ord] = []  # every written order entry (keep)
        self.lost = 0
        self.lost_held = False  # the pending lost reject has waited for a free cycle
        self.on_order = None  # called with a bin name at each order event the model sees

    def garbage(self) -> None:
        d = self.dut
        d.s_ev_valid.value = 0
        d.s_pkt_valid.value = 0
        d.s_sent_valid.value = 0
        d.s_rej_valid.value = 0
        d.s_rej_lost.value = 0
        for k, w in EV_GARBAGE.items():
            getattr(d, f"s_ev_{k}").value = random.getrandbits(w)
        for k, w in PKT_GARBAGE.items():
            getattr(d, f"s_pkt_{k}").value = random.getrandbits(w)
        for k, w in SENT_GARBAGE.items():
            getattr(d, f"s_sent_{k}").value = random.getrandbits(w)
        for k, w in REJ_GARBAGE.items():
            getattr(d, f"s_rej_{k}").value = random.getrandbits(w)

    def credits(self) -> int:
        return ORD_Q - self.outstanding

    def _drive_order(self, port: str, o: RingOrder) -> None:
        """The ports as the producer gives them: the fields of the entry the model writes for it."""
        fields = desc_gen.decode(order_entry(o, 0))
        for name, value in fields.items():
            if name in ("seq", "sof_ts", "cfg_epoch", "source", "rule", "side", "tif", "locate", "qty", "price",
                        "stock", "clordid", "user_ref", "reason", "tx_ts", "reject_ts"):  # fmt: skip
                v = wire(value) if name in ALPHA else value
                getattr(self.dut, f"s_{port}_{ORD_FIELD_PORT.get(name, name)}").value = v
        getattr(self.dut, f"s_{port}_valid").value = 1

    def _try_entry(self, pkt: Pkt, out_cycle: int) -> None:
        self.attempts[out_cycle] = pkt
        if pkt.full_at == pkt.tried:
            self.force_full.add(out_cycle)
        if pkt.busy_at == pkt.tried:
            self.force_busy.add(out_cycle)
        pkt.tried += 1

    async def step(
        self, ev: Event | None = None, pkt: Pkt | None = None, index: int = 0, sent: RingOrder | None = None,
        rej: RingOrder | None = None, lost: bool = False,
    ) -> None:  # fmt: skip
        """One cycle: a message of pkt (with its index), or pkt's event, or idle; and order inputs."""
        d = self.dut
        self.garbage()
        out = self.d + 1
        if sent is None and rej is None and not lost and self.order_src is not None:
            sent, rej, lost = self.order_src(self)
        n = (sent is not None) + (rej is not None)
        assert n <= self.credits(), "the test sends more order entries than it has credits"
        if sent is not None:
            assert sent.reason == Reason.NONE and not sent.dropped
            self._drive_order("sent", sent)
            self.to_sent.append(sent)
        if rej is not None:
            assert rej.reason != Reason.NONE and not rej.dropped
            self._drive_order("rej", rej)
            self.to_rej.append(rej)
        self.outstanding += n
        d.s_rej_lost.value = int(lost)
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

    def _hit(self, name: str) -> None:
        if self.on_order:
            self.on_order(name)

    def _check(self, full: bool, busy: bool) -> None:
        d, m = self.dut, self.m
        got = (int(d.cnt_commit_entries.value), int(d.cnt_drop_pkts.value), int(d.cnt_drop_entries.value))
        while self.count_q and self.count_q[0][0] <= m:
            _, *moved = self.count_q.pop(0)
            self.counted = [a + b for a, b in zip(self.counted, moved, strict=True)]
        want = tuple(self.counted)
        assert got == want, f"cycle {m}: counters (commit, drop pkts, drop entries) {got}, model {want}"
        wr, commit, rollback = bool(d.fifo_wr_en.value), bool(d.fifo_commit.value), bool(d.fifo_rollback.value)
        queued = int(d.cnt_ord_queued.value)
        assert queued == len(self.oq), f"cycle {m}: cnt_ord_queued {queued}, model {len(self.oq)}"
        ret = bool(d.ring_credit_ret.value)
        assert ret == self.credit_due, f"cycle {m}: ring_credit_ret {ret}, model {self.credit_due}"
        if ret:
            self.outstanding -= 1

        had_s1 = self.os1
        ord_ok = had_s1 and not (full or busy)
        pkt = self.attempts.pop(m, None)
        assert not (had_s1 and pkt is not None), f"cycle {m}: an order entry and a packet entry in one cycle"
        if pkt is not None and (full or busy):
            pkt.dropped = True
        pkt_wr = pkt is not None and not pkt.dropped
        assert wr == (pkt_wr or ord_ok), f"cycle {m}: fifo_wr_en {wr}, packet {pkt}, order write {ord_ok}"
        if pkt_wr:
            self.unit.append(int(d.fifo_din.value).to_bytes(128, "little"))
            self.max_unit = max(self.max_unit, len(self.unit))

        ended = self.ends.pop(m, None)
        if ended is None:
            assert (commit, rollback) == (ord_ok, False), f"cycle {m}: commit {commit} rollback {rollback}"
        else:
            self._end(ended, commit, rollback)
        self._orders(m, had_s1, ord_ok)
        if self.unread and random.random() < self.p_read:
            self.unread -= 1

    def _orders(self, m: int, had_s1: bool, ord_ok: bool) -> None:
        """The order queue in this cycle: stage 1's write, stage 0's take, this cycle's inputs."""
        d = self.dut
        ev_v, pkt_v = bool(d.s_ev_valid.value), bool(d.s_pkt_valid.value)
        sent_v, rej_v, lost = bool(d.s_sent_valid.value), bool(d.s_rej_valid.value), bool(d.s_rej_lost.value)
        n0 = len(self.oq)
        written = None
        if had_s1:
            head = self.oq[0]
            head.tries += 1
            if ord_ok:
                written = self.oq.pop(0)
                want = order_entry(written.ring, FLAG_OVERFLOW_BEFORE if self.pending else 0)
                got = int(d.fifo_din.value).to_bytes(128, "little")
                assert got == want, f"cycle {m}: order entry differs from the model, {written.ring}"
                kind = Kind(want[20])
                self._hit(f"kind_{kind.name}")
                if self.pending:
                    self._hit(f"flag_on_{kind.name}")
                self.pending = False
                written.written = m
                if m == written.arrived + 2:
                    self._hit("ord_idle")
                if head.tries > 1:
                    self._hit("ord_full_wait")
                self.unread += 1
                self.commit_entries += 1
                bisect.insort(self.count_q, (m + 1, 1, 0, 0))
                if self.keep:
                    self.committed.append(want)
                    self.orders.append(written)
        self.credit_due = ord_ok
        free = not self.m_eos and not ev_v and not pkt_v and self.m_empty
        lost_head = self.lost_pend or (written is not None and written.lost_after)
        if lost_head and free:
            self.pending = True
            if self.lost_held:
                self._hit("lost_taken_late")
        self.lost_pend = lost_head and not free
        self.lost_held = self.lost_pend
        pick = free and n0 > int(had_s1)
        if pick and had_s1 and not ord_ok:
            self._hit("ord_retry_cancels_next")
        if not free and n0 > int(had_s1) and not self.m_empty:
            self._hit("ord_waits_packet")
        self.os1 = pick and not (had_s1 and not ord_ok)

        new_sent = Ord(self.to_sent.pop(0), m) if sent_v else None
        new_rej = Ord(self.to_rej.pop(0), m) if rej_v else None
        if lost:
            self.lost += 1
            if new_sent is not None:
                new_sent.lost_after = True
                self._hit("lost_after_sent")
            elif self.oq:
                self.oq[-1].lost_after = True
                self._hit("lost_after_tail")
            else:
                self.lost_pend = True
                self._hit("lost_tail_written_now" if written is not None and n0 == 1 else "lost_head")
            if new_rej is not None and new_sent is None:
                self._hit("lost_before_cancel")
            if not self.m_empty:
                self._hit("lost_in_packet")
        if new_sent is not None and new_rej is not None:
            self._hit("ord_same_cycle")
        self.oq += [o for o in (new_sent, new_rej) if o is not None]
        if len(self.oq) == ORD_Q:
            self._hit("ord_queue_full")
        assert len(self.oq) <= ORD_Q, f"cycle {m}: more order entries than credits"

        status = MoldStatus(int(d.s_pkt_status.value)) if pkt_v else None
        gap = int(d.s_pkt_gap.value) if pkt_v else 0
        self.m_eos = (not self.m_eos and not ev_v and pkt_v and status in ACCEPTED and gap != 0
                      and status == S.EOS)  # fmt: skip
        if pkt_v:
            self.m_empty = True
        elif ev_v:
            self.m_empty = False

    def clear_orders(self) -> None:
        """The order queue model after a reset of the DUT."""
        self.oq, self.os1, self.lost_pend, self.m_eos, self.m_empty = [], False, False, False, True
        self.lost_held = False
        self.credit_due = False
        self.to_sent.clear()
        self.to_rej.clear()
        self.outstanding = 0

    def _end(self, pkt: Pkt, commit: bool, rollback: bool) -> None:
        keep = pkt.status in ACCEPTED and not pkt.dropped
        assert (commit, rollback) == (keep, not keep), f"cycle {self.m}: commit {commit} rollback {rollback}, {pkt}"
        r = expect_entries([pkt.ring()], self.pending)
        self.pending = r.overflow_pending
        # The end comes 1 cycle after the event, 2 with a pending EOS entry; the counters 3 after the event.
        late = pkt.gap != 0 and pkt.status == MoldStatus.EOS
        bisect.insort(self.count_q, (self.m + (1 if late else 2), len(r.entries) if keep else 0, r.drop_pkts,
                                     r.drop_entries))  # fmt: skip
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
        """Idle until every packet has ended, every order entry is written and every counter has been checked."""
        self.order_src = None
        # Bounded: each queued entry needs one word of room, which a reader at p_read frees in 1/p_read cycles.
        for _ in range(64 + int(4 * (self.unread + ORD_Q) / max(self.p_read, 0.01))):
            if not self.oq and not self.to_sent and not self.to_rej:
                break
            await self.step()
        assert not self.oq, f"order entries never written: {self.oq}"
        for _ in range(4):
            await self.step()
        assert not self.attempts and not self.ends, f"left over: {self.attempts} {self.ends}"
        assert not self.unit, "words written and never committed or rolled back"
        assert self.outstanding == 0, f"{self.outstanding} ring credits never given back"


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
    await p.step()  # the monitor's first cycle: it sees the inputs of every step after this one
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
    p.counted = [0, 0, 0]
    p.count_q.clear()
    p.force_full.clear()
    p.clear_orders()
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


# ---- Order entries (audit/14 P8; decisions 25, 28-31) ----


async def idle(p: Packer, n: int = 4) -> None:
    for _ in range(n):
        await p.step()


def kinds_flags(p: Packer) -> list[tuple[Kind, int]]:
    return [(Kind(d["kind"]), d["flags"]) for d in decoded(p)]


async def lost_on_empty_queue(p: Packer) -> None:
    await p.step(lost=True)
    await p.step()  # a free cycle: its place comes
    await p.send(Pkt(events=orders(1)))


async def lost_after_sent(p: Packer) -> None:
    await p.step(sent=random_order(False), lost=True)
    await p.step(sent=random_order(False))
    await idle(p)


async def lost_after_tail(p: Packer) -> None:
    p.force_full.add(p.d + 2)  # the entry waits in stage 1
    await p.step(sent=random_order(False))
    await p.step()
    await p.step(lost=True)
    await p.step(sent=random_order(False))
    await idle(p)


async def lost_before_cancel(p: Packer) -> None:
    await p.step(rej=random_order(True, cancel=True), lost=True)
    await idle(p)


async def lost_tail_written_now(p: Packer) -> None:
    await p.step(sent=random_order(False))
    await p.step()
    await p.step(lost=True)  # the queue's only entry is written in this cycle
    await p.step(sent=random_order(False))
    await idle(p)


async def lost_in_packet_before_order(p: Packer) -> None:
    pkt = Pkt(events=orders(2))
    await p.step(pkt.events[0], pkt, 0, lost=True)
    await p.step(pkt.events[1], pkt, 1, sent=random_order(False))
    await p.step(pkt=pkt)
    await idle(p)


async def lost_in_packet_after_order(p: Packer) -> None:
    pkt = Pkt(events=orders(2))
    await p.step(pkt.events[0], pkt, 0, sent=random_order(False))
    await p.step(pkt.events[1], pkt, 1, lost=True)
    await p.step(pkt=pkt)
    await idle(p)
    await p.send(Pkt(events=orders(1)))


async def lost_taken_late(p: Packer) -> None:
    await p.step(lost=True)
    await p.send(Pkt(events=orders(2)))  # its first message is in the first cycle the lost reject could go
    await p.send(Pkt(events=orders(1)))


SENT, REJ, ITCH = Kind.ORDER_SENT, Kind.RISK_REJECT, Kind.ITCH
LOST_CASES = {
    lost_on_empty_queue: [(ITCH, 1)],
    lost_after_sent: [(SENT, 0), (SENT, 1)],
    lost_after_tail: [(SENT, 0), (SENT, 1)],
    lost_before_cancel: [(REJ, 1)],
    lost_tail_written_now: [(SENT, 0), (SENT, 1)],
    lost_in_packet_before_order: [(ITCH, 0), (ITCH, 0), (SENT, 1)],
    lost_in_packet_after_order: [(ITCH, 0), (ITCH, 0), (SENT, 0), (ITCH, 1)],
    lost_taken_late: [(ITCH, 0), (ITCH, 0), (ITCH, 1)],
}


async def same_cycle(p: Packer) -> tuple[RingOrder, RingOrder, int]:
    a, b, d = random_order(False), random_order(True), p.d
    await p.step(sent=a, rej=b)
    await idle(p)
    return a, b, d


async def full_wait(p: Packer) -> tuple[RingOrder, RingOrder, int]:
    """The first entry finds the FIFO full in stage 1 twice; the entry taken behind it is cancelled."""
    a, b, d = random_order(False), random_order(True), p.d
    p.force_full |= {d + 2, d + 3}
    await p.step(sent=a)
    await p.step(rej=b)
    await idle(p, 6)
    return a, b, d


async def waits_for_packet(p: Packer) -> tuple[RingOrder, RingOrder, int]:
    pkt = Pkt(events=orders(3))
    a, b = random_order(False), random_order(True)
    await p.step(pkt.events[0], pkt, 0, sent=a)
    await p.step(pkt.events[1], pkt, 1, rej=b)
    await p.step(pkt.events[2], pkt, 2)
    d = p.d
    await p.step(pkt=pkt)
    await idle(p)
    return a, b, d


async def queue_full(p: Packer) -> None:
    """A full FIFO with no reader: 16 order entries wait, a packet in the meantime drops."""
    depth, p_read = p.depth, p.p_read
    p.depth, p.p_read = p.unread, 0.0
    for _ in range(ORD_Q):
        await p.step(sent=random_order(False))
    await p.send(Pkt(events=orders(2)))
    await idle(p, 8)
    assert int(p.dut.cnt_ord_queued.value) == ORD_Q
    p.depth, p.p_read = depth, p_read if p_read else 1.0


ORDER_SCENARIOS = [*LOST_CASES, same_cycle, full_wait, waits_for_packet, queue_full]


@cocotb.test()
async def tc_order_hand_entries(dut):
    """ORDER_SENT and RISK_REJECT against their hand-written bytes (selftest/desc_hand.py); the second has
    overflow_before from the dropped packet before it."""
    p = await start(dut)
    await p.step(sent=RingOrder(HW_ORDER, user_ref=0x31323334, ts=0x7172737475767778))
    await idle(p)
    await p.send(Pkt(events=[order()], full_at=0))
    await p.step(rej=RingOrder(HOST_ORDER, Reason.TX_BUSY, ts=0x8182838485868788))
    await p.drain()
    assert p.committed == [clear_writer_fields(HAND_ORDER_SENT), clear_writer_fields(HAND_RISK_REJECT)]


@cocotb.test()
async def tc_order_latency(dut):
    """An order entry taken at edge k is written in the cycle after edge k + 1 (output cycle d + 2 of a step d)
    and its credit comes back one cycle later. In one cycle the ORDER_SENT goes first (decision 28)."""
    p = await start(dut)
    d = p.d
    await p.step(sent=random_order(False))
    await idle(p)
    assert p.orders[0].written == d + 2
    a, b, d = await same_cycle(p)
    assert [o.ring for o in p.orders[1:]] == [a, b]
    assert [o.written for o in p.orders[1:]] == [d + 2, d + 3]
    await p.drain()
    assert [k for k, _ in kinds_flags(p)] == [SENT, SENT, REJ]


@cocotb.test()
async def tc_order_waits_for_the_packet(dut):
    """Order entries that come during a packet wait for its commit: never inside its unit. The first free cycle
    is the one after the event."""
    p = await start(dut)
    a, b, d = await waits_for_packet(p)
    await p.drain()
    assert [k for k, _ in kinds_flags(p)] == [ITCH, ITCH, ITCH, SENT, REJ]
    assert [o.ring for o in p.orders] == [a, b]
    assert [o.written for o in p.orders] == [d + 2, d + 3]


@cocotb.test()
async def tc_order_waits_on_a_full_fifo(dut):
    """A full FIFO keeps the entry queued (decision 25): it is written once, when there is room, and the one
    taken behind it is cancelled, not written twice."""
    p = await start(dut)
    a, b, d = await full_wait(p)
    await p.drain()
    assert [o.ring for o in p.orders] == [a, b]
    assert [o.written for o in p.orders] == [d + 4, d + 5]
    assert len(p.committed) == 2


@cocotb.test()
async def tc_order_queue_holds_all_credits(dut):
    """16 entries wait in a FIFO with no reader while a packet drops; none is lost. The dropped packet's flag
    goes on the first of them (decision 31), and every credit comes back."""
    p = await start(dut)
    await queue_full(p)
    await p.drain()
    assert len(p.orders) == ORD_Q and p.drop_pkts == 1
    assert kinds_flags(p) == [(SENT, 1)] + [(SENT, 0)] * (ORD_Q - 1)


@cocotb.test()
async def tc_dropped_packet_flags_the_order_entry(dut):
    """Decision 31: the next committed entry after a dropped packet is an order entry: it takes the flag."""
    p = await start(dut)
    await p.send(Pkt(events=orders(1), full_at=0))
    await p.step(rej=random_order(True))
    await idle(p)
    await p.send(Pkt(events=orders(1)))
    await p.drain()
    assert kinds_flags(p) == [(REJ, 1), (ITCH, 0)]


@cocotb.test()
async def tc_lost_reject_flag_place(dut):
    """Decision 29: a lost reject's overflow_before goes on the next committed entry after its place: behind
    that cycle's ORDER_SENT or the queue's last entry, before a reject of the same cycle, after an open packet,
    after a packet that takes the first free cycle."""
    p = await start(dut)
    for case, want in LOST_CASES.items():
        p.committed.clear()
        await case(p)
        await p.drain()
        assert kinds_flags(p) == want, case.__name__
        assert not p.pending, case.__name__
    assert p.lost == len(LOST_CASES)


@cocotb.test()
async def tc_reset_with_orders_queued(dut):
    """rst with order entries queued, one in stage 1, and a lost reject pending clears them: no write, no credit
    given back, cnt_ord_queued 0; the next entry goes as after any reset."""
    p = await start(dut, depth=0, p_read=0.0)
    for _ in range(5):
        await p.step(sent=random_order(False))
    await p.step(lost=True)
    await idle(p, 2)
    p.paused = True
    await FallingEdge(dut.clk)
    p.garbage()
    dut.rst.value = 1
    for _ in range(3):
        await RisingEdge(dut.clk)
        p.d += 1
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    p.clear_orders()
    p.pending = False
    p.depth, p.p_read = 256, 1.0
    await RisingEdge(dut.clk)
    p.d += 1
    assert int(dut.cnt_ord_queued.value) == 0
    p.paused = False
    await p.step(sent=random_order(False))
    await p.drain()
    assert kinds_flags(p) == [(SENT, 0)]


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
             "outcome_OUT", "after_0_rejected", *ORDER_BINS]  # fmt: skip
    cov = Coverage("event_packer", bins)

    def order_src(pk: Packer) -> tuple[RingOrder | None, RingOrder | None, bool]:
        # Order entries as risk_check and order_tx_builder give them, within the credits.
        sent = random_order(False) if random.random() < 0.04 and pk.credits() >= 1 else None
        rej = None
        if random.random() < 0.04 and pk.credits() >= 1 + (sent is not None):
            rej = random_order(True, cancel=random.random() < 0.3)
        return sent, rej, random.random() < 0.01

    p.on_order = cov.hit
    p.order_src = order_src

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
    def packet_bins_empty() -> list[str]:
        return [b for b in cov.empty() if b not in ORDER_BINS]

    while sent < N_RANDOM or (packet_bins_empty() and sent < MAX_RANDOM):
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
    # Completion: the order bins a seed may leave empty, from the directed scenarios on an empty FIFO.
    if any(b in ORDER_BINS for b in cov.empty()):
        dut._log.info(f"order bins empty after the random run: {cov.empty()}; directed scenarios")
        p.p_read = 1.0
        while p.unread:
            await p.step()
        for scenario in ORDER_SCENARIOS:
            await scenario(p)
            await p.drain()
    assert p.drop_pkts > 0
    dut._log.info(f"{len(p.orders) if p.keep else 'n/a'} order entries, {p.lost} lost rejects")
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
