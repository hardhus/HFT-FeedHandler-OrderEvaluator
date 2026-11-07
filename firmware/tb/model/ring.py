"""Golden model of the host ring path (audit/13 §2.2, §2.4, §2.7). Pure Python, no simulator.

Decision: expect_entries() applies the packet rules to the messages and packet events as data; it
never looks at FIFO pointers or bus beats. Which packets did not fit is an input (RingPacket.dropped),
taken by the test from its own timeline, because it depends on when the host reads.

Rules, packet by packet:
- accepted (OK, HEARTBEAT, EOS): the message entries, then a GAP entry if gap > 0, then an EOS entry
  if the status is EOS. Any other status: nothing, and nothing is counted (itch_stats' rule).
- dropped: an accepted packet that did not fit writes nothing, is counted whole, and the next
  entry that is committed carries overflow_before. A packet that would have been rolled back
  anyway is no loss: it is not counted and sets no flag, even if it did not fit.
- order entries (ORDER_SENT, RISK_REJECT; audit/14 §2.7, P8, decisions 25, 28-31): written between
  packets, each one its own unit. They wait for room and are never dropped by the packer: risk_check
  reserves a ring credit for every accepted order, so an ORDER_SENT and a cancel's RISK_REJECT
  always get their entry. Only a decision's RISK_REJECT that found no credit is lost: counted in
  drop_orders (RING_DROP_ORDERS) and it sets overflow_before on the next committed entry after its
  place, like a dropped packet (an order entry takes a dropped packet's flag too). Its place is an
  input: the test puts it in the list where decision 29 says it was (after that cycle's
  ORDER_SENT, before a cancel's RISK_REJECT of the same cycle).

Ring protocol: entry i goes to base + (i mod N) * 128 with phase ~bit(i, log2 N), so the first pass
has phase 1 and memory that starts at 0 is not valid. Producer and consumer indexes wrap at 2**32
and are compared by difference. An entry is valid when BOTH lines show the expected phase: PCIe
does not order the bytes of one write in host memory (audit/02 §5.7).
"""

from collections.abc import Callable
from dataclasses import dataclass

from model import desc_gen
from model.desc_gen import ENTRY_BYTES, FLAG_OVERFLOW_BEFORE, Kind
from model.itch import DECODED, Event
from model.mold import ACCEPTED, SEQ_MOD, UDP_PAYLOAD_MAX, MoldStatus
from model.mold import HDR_LEN as MOLD_HDR_LEN
from model.mold import MIN_LEN as MOLD_MIN_LEN
from model.order import OrderReq, Source, hw_clordid
from model.risk import Reason

IDX_MOD = 1 << 32
RAW_LO = 38  # message bytes in ITCH_RAW line 0; the rest are in line 1
# Most entries of one packet: the shortest supported blocks in the largest payload, GAP and EOS.
# The commit FIFO must hold this many, or a packet that fits nowhere would always drop.
MAX_PKT_ENTRIES = (UDP_PAYLOAD_MAX - MOLD_HDR_LEN) // (2 + MOLD_MIN_LEN) + 2


@dataclass(frozen=True)
class RingPacket:
    status: MoldStatus
    seq: int = 0  # header sequence number
    gap: int = 0
    sof_ts: int = 0
    epoch: int = 0  # cfg_epoch on the packet's first beat
    msgs: tuple[tuple[int, Event], ...] = ()  # (seq, event) of its OUT messages, in order
    dropped: bool = False  # did not fit in the commit FIFO (from the test's timeline)


@dataclass(frozen=True)
class RingOrder:
    """An ORDER_SENT (reason NONE: user_ref and ts = tx_ts) or RISK_REJECT (ts = reject_ts) entry."""

    req: OrderReq
    reason: Reason = Reason.NONE
    user_ref: int = 0
    ts: int = 0
    dropped: bool = False  # a decision's reject that found no ring credit (from the test's timeline)
    cancel: bool = False  # a RISK_REJECT of an order cancelled in the TX queue (decisions 7, 20)


@dataclass
class RingExpect:
    """entries: what reaches the commit FIFO's reader, without ring_idx and ctrl (stamp() fills them).
    drop_msgs: the message entries inside drop_entries, for the identity with ITCH_OUT.
    drop_orders: order entries that did not fit (RING_DROP_ORDERS)."""

    entries: list[bytes]
    drop_pkts: int = 0
    drop_entries: int = 0
    drop_msgs: int = 0
    drop_orders: int = 0
    overflow_pending: bool = False


def message_entry(seq: int, sof_ts: int, epoch: int, flags: int, ev: Event) -> bytes:
    common = {"seq": seq, "sof_ts": sof_ts, "flags": flags, "cfg_epoch": epoch, "itch_class": int(ev.cls)}
    if ev.cls in DECODED:
        return desc_gen.encode(
            Kind.ITCH, **common, msg_type=ev.type, len=ev.length, locate=ev.locate, tracking=ev.tracking,
            itch_ts=ev.timestamp, side=ev.side, printable=ev.printable, cross_type=ev.cross_type, price=ev.price,
            attribution=ev.attribution.to_bytes(4, "little"), order_ref=ev.order_ref, new_ref=ev.new_ref,
            shares=ev.shares, match=ev.match, stock=ev.stock.to_bytes(8, "little"),
        )  # fmt: skip
    raw = ev.raw.ljust(64, b"\0")
    return desc_gen.encode(Kind.ITCH_RAW, **common, len=ev.length, raw_lo=raw[:RAW_LO], raw_hi=raw[RAW_LO:])


def packet_entries(pkt: RingPacket, flags: int) -> list[bytes]:
    """The entries of an accepted packet; flags goes on the first one only."""
    out: list[bytes] = []

    def next_flags() -> int:
        return 0 if out else flags

    for seq, ev in pkt.msgs:
        out.append(message_entry(seq, pkt.sof_ts, pkt.epoch, next_flags(), ev))
    if pkt.gap:
        out.append(
            desc_gen.encode(
                Kind.GAP, seq=(pkt.seq - pkt.gap) % SEQ_MOD, sof_ts=pkt.sof_ts, flags=next_flags(),
                cfg_epoch=pkt.epoch, first_seq=pkt.seq, gap=pkt.gap,
            )
        )  # fmt: skip
    if pkt.status == MoldStatus.EOS:
        out.append(desc_gen.encode(Kind.EOS, seq=pkt.seq, sof_ts=pkt.sof_ts, flags=next_flags(), cfg_epoch=pkt.epoch))
    return out


def order_entry(o: RingOrder, flags: int) -> bytes:
    req = o.req
    common = {
        "seq": req.seq, "sof_ts": req.sof_ts, "flags": flags, "cfg_epoch": req.epoch, "source": int(req.source),
        "rule": req.rule, "side": req.side, "tif": req.tif, "locate": req.locate, "qty": req.qty,
        "price": req.price % (1 << 32), "stock": req.stock,
    }  # fmt: skip
    if o.reason == Reason.NONE:
        clordid = req.clordid if req.source == Source.HOST else hw_clordid(req.rule, o.user_ref)
        return desc_gen.encode(Kind.ORDER_SENT, **common, user_ref=o.user_ref, tx_ts=o.ts, clordid=clordid)
    clordid = req.clordid if req.source == Source.HOST else b" " * 14
    return desc_gen.encode(Kind.RISK_REJECT, **common, reason=int(o.reason), reject_ts=o.ts, clordid=clordid)


def expect_entries(items: list[RingPacket | RingOrder], overflow_pending: bool = False) -> RingExpect:
    """Reference output of event_packer for a run of packets and order entries, in the order the
    packer takes them. overflow_pending: a drop before this run whose flag has not gone out yet;
    the result holds the value for the next run."""
    result = RingExpect([], overflow_pending=overflow_pending)
    for pkt in items:
        if isinstance(pkt, RingOrder):
            if pkt.dropped and (pkt.reason == Reason.NONE or pkt.cancel):
                raise ValueError("an ORDER_SENT or a cancel's RISK_REJECT has a reserved credit and never drops")
            if pkt.dropped:
                result.drop_orders += 1
                result.overflow_pending = True
            else:
                result.entries.append(order_entry(pkt, FLAG_OVERFLOW_BEFORE if result.overflow_pending else 0))
                result.overflow_pending = False
            continue
        if pkt.status not in ACCEPTED:
            continue
        flags = FLAG_OVERFLOW_BEFORE if result.overflow_pending else 0
        entries = packet_entries(pkt, flags)
        if pkt.dropped:
            result.drop_pkts += 1
            result.drop_entries += len(entries)
            result.drop_msgs += len(pkt.msgs)
            result.overflow_pending = True
        elif entries:
            result.entries += entries
            result.overflow_pending = False
    return result


def phase_of(prod: int, size_log2: int) -> int:
    return ~(prod >> size_log2) & 1


def slot_addr(base: int, idx: int, size_log2: int) -> int:
    return base + (idx & ((1 << size_log2) - 1)) * ENTRY_BYTES


def stamp(entry: bytes, prod: int, size_log2: int) -> bytes:
    """The entry as event_ring_writer writes it: ring_idx and both ctrl bytes filled."""
    out = bytearray(entry)
    ofs, size, _ = desc_gen.FIELDS[Kind.EOS]["ring_idx"]  # common: the same in every kind
    out[ofs : ofs + size] = (prod % IDX_MOD).to_bytes(size, "little")
    ctrl = desc_gen.VERSION | (desc_gen.CTRL_PHASE if phase_of(prod, size_log2) else 0)
    for b in desc_gen.CTRL_BYTES:
        out[b] = ctrl
    return bytes(out)


class RingError(Exception):
    """The ring broke the protocol: a wrong version, different ctrl bytes or a skipped ring_idx."""


class RingConsumer:
    """The host: polls the expected slot, both lines' phase first, then checks the entry and moves
    on. read(addr, n) reads host memory; write_cons(idx) is the posted CONS_IDX write, made every
    update_every entries (default N/4, the host rule)."""

    def __init__(
        self,
        read: Callable[[int, int], bytes],
        write_cons: Callable[[int], None],
        base: int,
        size_log2: int,
        update_every: int | None = None,
        start: int = 0,
    ) -> None:
        self.read, self.write_cons, self.base, self.size_log2 = read, write_cons, base, size_log2
        self.update_every = update_every or max(1, (1 << size_log2) // 4)
        self.idx = start  # next entry, unbounded (ring_idx and CONS_IDX are its low 32 bits)
        self.torn = 0  # polls that saw one line of an entry and not the other

    def poll(self) -> bytes | None:
        """The next entry, or None if it is not complete yet."""
        entry = self.read(slot_addr(self.base, self.idx, self.size_log2), ENTRY_BYTES)
        want = phase_of(self.idx, self.size_log2)
        seen = [bool(entry[b] & desc_gen.CTRL_PHASE) == bool(want) for b in desc_gen.CTRL_BYTES]
        if not all(seen):
            self.torn += any(seen)
            return None
        ctrl0, ctrl1 = (entry[b] for b in desc_gen.CTRL_BYTES)
        if ctrl0 != ctrl1:
            raise RingError(f"entry {self.idx}: ctrl bytes differ, {ctrl0:#x} and {ctrl1:#x}")
        if ctrl0 & desc_gen.CTRL_VERSION_MASK != desc_gen.VERSION:
            raise RingError(f"entry {self.idx}: version {ctrl0 & desc_gen.CTRL_VERSION_MASK}")
        ofs, size, _ = desc_gen.FIELDS[Kind.EOS]["ring_idx"]
        ring_idx = int.from_bytes(entry[ofs : ofs + size], "little")
        if ring_idx != self.idx % IDX_MOD:
            raise RingError(f"entry {self.idx}: ring_idx {ring_idx}")
        self.idx += 1
        if self.idx % self.update_every == 0:
            self.write_cons(self.idx % IDX_MOD)
        return entry

    def flush(self) -> None:
        """Publish the consumer index now (a slow host's last write before it pauses)."""
        self.write_cons(self.idx % IDX_MOD)


@dataclass(frozen=True)
class Pacing:
    """How a test's consumer runs: one poll every `period` cycles; after `burst` entries (0: never)
    it stops for `pause` cycles."""

    period: int = 1
    burst: int = 0
    pause: int = 0

    def wait_after(self, done: int) -> int:
        """Cycles to wait after a poll, with `done` entries read so far."""
        return self.pause if self.burst and done and done % self.burst == 0 else self.period


PACINGS = {
    "fast": Pacing(),
    "slow": Pacing(period=64),
    "pausing": Pacing(burst=64, pause=4096),
}


class RingMemory:
    """Writer side of the protocol over a bytearray: for tests of the consumer, and the reference for
    the full rule (prod - cons == N: wait)."""

    def __init__(self, size_log2: int, base: int = 0, start: int = 0) -> None:
        self.size_log2, self.base, self.prod = size_log2, base, start
        self.mem = bytearray(ENTRY_BYTES << size_log2)
        self.cons = start % IDX_MOD

    def read(self, addr: int, n: int) -> bytes:
        return bytes(self.mem[addr - self.base : addr - self.base + n])

    def write_cons(self, idx: int) -> None:
        self.cons = idx

    def full(self) -> bool:
        return (self.prod - self.cons) % IDX_MOD == 1 << self.size_log2

    def write(self, entry: bytes) -> bool:
        """Write the next entry; False if the ring is full."""
        if self.full():
            return False
        addr = slot_addr(0, self.prod, self.size_log2)
        self.mem[addr : addr + ENTRY_BYTES] = stamp(entry, self.prod, self.size_log2)
        self.prod += 1
        return True
