"""Vectors of the C++ co-simulation smoke test (audit/15 §2.7 P11, decision 6): one scenario through HFT_Core,
with every expected value from the Python models (no second golden in C++).

Scenario: the software contract after reset (ID, VERSION, RESET_SEEN; NET; SYMBOL; FEED with ITCH_CTRL; RING; the
order path: limits, LOCATE_ALLOW, budget, one rule, the TX template, TX_USERREF_NEXT, tx_en, kill released last),
then a packet whose Add Order fires the rule, a packet with a bad FCS, a duplicate of the first, a packet the
disarmed rule ignores (one message filtered by SYMBOL), and a host order through the mailbox. Expected: every ring
entry (ring_idx and both ctrl bytes stamped), every TX frame, and the snapshot counters.

Timestamps (sof_ts, tx_ts, reject_ts) depend on the cycle and the models do not predict them: they are 0 in the
expected entry and listed as masked fields on its line. The C++ test checks that each masked field is non-decreasing
in ring order and lies between TS_SNAPSHOT of the first and the last snapshot; every other byte must be equal. A host
order's sof_ts is 0 (no packet), so it is not masked.

Format: one command per line, fields separated by spaces, numbers in hex without 0x, '#' starts a comment.
  memory <iova> <bytes>          host memory the AXI write slave answers (the ring region)
  csr_w <addr> <data>            AXI-Lite write, BRESP must be OKAY
  csr_r <addr> <data> <mask>     AXI-Lite read, OKAY and (rdata & mask) == data
  poll <addr> <data> <mask>      csr_r repeated until it matches (a bounded number of reads)
  tx_ready <percent>             probability of m_axis_tx_tready per net cycle
  rx <frame> [bad_fcs]           one frame on s_axis_rx_* (bad_fcs: tuser on its last beat)
  idle <cycles>                  net clock cycles
  snapshot                       the STATS snapshot protocol; TS_SNAPSHOT is recorded
  drain <entries> <frames>       wait until that many ring entries are in memory and frames were sent
  expect_ring <entry> [<name>@<offset>:<size> ...]
  expect_tx <frame>
  end
Usage: python firmware/tb/common/cosim_vectors.py <out file>   (firmware/tb on PYTHONPATH)
"""

import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from common.csr_env import COMMANDS, LOCATE_ALLOW, MBOX_SEND, RISK_CTRL, SYMBOL, TX_CTRL, FeedCfg, LimCfg, MboxOrder
from common.csr_env import NetCfg, RuleCfg, TxCfg
from model import mold, net
from model.csr import addr
from model.desc_gen import FIELDS, Kind
from model.itch import Bitmap, Event, ItchClass, ItchConfig, Outcome, encode, expect
from model.mold import ACCEPTED, FeedConfig, FeedState, MoldStatus
from model.net import RxConfig, RxReason, ip_int, pad_to, udp_frame
from model.order import OrderReq, Source, TxConfig, frame, ouch_of, template
from model.ring import RingOrder, RingPacket, expect_entries, stamp
from model.strategy import PriceMode, PriceOp, QtyMode, Rule, make_order

LOCAL_MAC = "02:00:00:00:00:01"
FEED_IP, FEED_PORT = "10.0.0.2", 5000
SESSION = b"SESSION001"
LOC, OTHER = 5, 70  # the subscribed locate (the rule's), one that is not
RING_IOVA = 0x0000_0001_0000_0000  # not 0, 4 KiB aligned (above 4 GiB: RING_BASE_HI is used)
RING_LOG2 = 6
EPOCH = 1  # one FEED_COMMIT before the traffic
NET = NetCfg(rx_en=1, mac=int(LOCAL_MAC.replace(":", ""), 16), ip=(ip_int(FEED_IP),) + (0,) * 7,
             port=(FEED_PORT,) + (0,) * 7, en=(1,) + (0,) * 7)  # fmt: skip
FEED = FeedCfg(enable=1, session_check=1, filter_mask=0x01, session=int.from_bytes(SESSION, "big"),
               class_en=(1 << len(ItchClass)) - 1, class_filter=1 << ItchClass.ORDER)  # fmt: skip
ITCH = ItchConfig(FEED.class_en, FEED.class_filter)
RX = RxConfig(True, LOCAL_MAC, False, ((FEED_IP, FEED_PORT, True),))  # the model's view of NET
MOLD_CFG = FeedConfig(True, True, SESSION, FEED.filter_mask)  # the model's view of FEED
LIMITS = LimCfg(max_qty=999_999, max_notional=(1 << 64) - 1, refill=1, period=1, burst=1000)
BUDGET = 1000
RULE = RuleCfg(enable=1, msg_mask=0b001, locate=LOC, side_match=0b11, price_ge=0, threshold=0xFFFF_FFFF,
               min_shares=0, action_side=0, qty_min=0, qty=100, price_mode=0, offset=0, tif=ord("0"))  # fmt: skip
MODEL_RULE = Rule(True, RULE.msg_mask, LOC, RULE.side_match, PriceOp(RULE.price_ge), RULE.threshold, RULE.min_shares,
                  RULE.action_side, QtyMode(RULE.qty_min), RULE.qty, PriceMode(RULE.price_mode), 0, RULE.tif)  # fmt: skip
TXC = TxCfg(template(), ord("Y"), ord("P"), ord("N"), ord("N"))
TX_MODEL = TxConfig(TXC.template, TXC.display, TXC.capacity, TXC.ise, TXC.cross_type)
USERREF = 1
MBOX = MboxOrder(side=ord("S"), qty=250, price=1_234_500, locate=LOC, stock=b"AAPL    ", tif=ord("3"),
                 clordid=b"HOSTORDER00001")  # fmt: skip
TS_FIELDS = ("sof_ts", "tx_ts", "reject_ts")


def add_order(locate: int, ref: int, stock: str = "AAPL") -> bytes:
    return encode("A", locate=locate, tracking=7, timestamp=34_200_000_000_000 + ref, order_ref=ref, side="B",
                  shares=100 + ref, stock=stock.ljust(8), price=1_850_000)  # fmt: skip


def feed_frame(seq: int, msgs: list[bytes]) -> bytes:
    return pad_to(udp_frame(mold.pack(SESSION, seq, msgs), dst_mac=LOCAL_MAC, dst_ip=FEED_IP, dport=FEED_PORT))


@dataclass
class Vectors:
    lines: list[str] = field(default_factory=list)
    bitmap: Bitmap = field(default_factory=Bitmap)
    items: list[RingPacket | RingOrder] = field(default_factory=list)  # ring entries in order
    frames: list[bytes] = field(default_factory=list)  # TX frames in order
    user_ref: int = USERREF
    state: FeedState = field(default_factory=FeedState)  # moldudp64_rx's feed state in the model
    count: Counter[str] = field(default_factory=Counter)  # snapshot counters, from the models

    def rx(self, seq: int, msgs: list[bytes], bad_fcs: bool = False) -> list[tuple[int, Event]]:
        """One feed frame through the chain models (net, mold, ITCH): counters, and for an accepted packet its ring
        entries; returns its OUT messages (seq, event)."""
        f = feed_frame(seq, msgs)
        self.lines.append(f"rx {f.hex()}" + (" bad_fcs" if bad_fcs else ""))
        r = net.expect(f, bad_fcs, RX)
        self.count["STATS.RX_PKTS"] += 1
        if r.reason != RxReason.OK:
            self.count[f"STATS.DROP_{r.reason.name}"] += 1
        if r.payload is None:
            return []
        res, self.state = mold.expect(r.payload, r.aborted, 0, MOLD_CFG, self.state)
        self.count[f"FEED.MOLD_{res.status.name}"] += 1
        if res.status not in ACCEPTED:
            return []
        out = []
        for s, m in res.msgs:
            ev, result = expect(m, ITCH, self.bitmap)
            self.count[f"ITCH.ITCH_{result.outcome.name}"] += 1
            if result.outcome == Outcome.OUT:
                assert ev is not None
                out.append((s, ev))
        self.items.append(RingPacket(res.status, seq=seq, gap=res.gap, epoch=EPOCH, msgs=tuple(out)))
        return out

    def w(self, a: int, d: int) -> None:
        self.lines.append(f"csr_w {a:x} {d & 0xFFFF_FFFF:x}")

    def r(self, a: int, d: int, mask: int = 0xFFFF_FFFF) -> None:
        self.lines.append(f"csr_r {a:x} {d:x} {mask:x}")

    def ws(self, writes: list[tuple[int, int]]) -> None:
        for a, d in writes:
            self.w(a, d)

    def order_sent(self, req: OrderReq) -> None:
        self.items.append(RingOrder(req, user_ref=self.user_ref))
        self.count[f"RISK.ORDERS_SENT_{'HOST' if req.source == Source.HOST else 'HW'}"] += 1
        self.frames.append(frame(TX_MODEL, ouch_of(req, self.user_ref, TX_MODEL)))
        self.user_ref += 1


def masks(entry: bytes, req: OrderReq | None) -> list[str]:
    """The timestamp fields of this entry kind, except a host order's sof_ts (0, no packet)."""
    kind = Kind(entry[20])
    out = []
    for name in TS_FIELDS:
        if name not in FIELDS[kind] or (name == "sof_ts" and req is not None and req.source == Source.HOST):
            continue
        ofs, size, _ = FIELDS[kind][name]
        assert not any(entry[ofs : ofs + size]), f"{kind.name}.{name} must be 0 in the expected entry"
        out.append(f"{name}@{ofs}:{size}")
    return out


def scenario() -> Vectors:
    v = Vectors()
    v.lines.append(f"memory {RING_IOVA:x} {128 << RING_LOG2:x}")
    v.lines.append("tx_ready 64")  # 100: the MAC takes every beat (the smoke test also runs with backpressure)
    # Software contract after reset (core README), RESET_SEEN = 0 first (decision 10).
    v.r(addr("GLOBAL.ID"), 0x4846_5431)
    v.r(addr("GLOBAL.VERSION"), 0x0005_0005)
    v.r(addr("GLOBAL.RESET_SEEN"), 1)
    v.w(addr("GLOBAL.RESET_SEEN"), 0)
    v.ws(NET.writes())
    v.w(addr("NET.NET_COMMIT"), 1)
    v.bitmap.set(LOC)
    v.w(SYMBOL.addr + 4 * (LOC >> 5), v.bitmap.words[LOC >> 5])
    v.ws(FEED.writes())
    v.w(addr("FEED.FEED_COMMIT"), 1)
    v.lines.append(f"poll {addr('NET.NET_STATUS'):x} 0 1")  # commit_busy = 0: both commits in effect
    v.lines.append(f"poll {addr('FEED.FEED_STATUS'):x} 0 1")
    v.ws([(addr("RING.RING_BASE_LO"), RING_IOVA & 0xFFFF_FFFF), (addr("RING.RING_BASE_HI"), RING_IOVA >> 32),
          (addr("RING.RING_SIZE_LOG2"), RING_LOG2), (addr("RING.CONS_IDX"), 0), (addr("RING.RING_CTRL"), 1)])  # fmt: skip
    v.r(addr("RING.RING_STATUS"), 0)
    v.r(addr("RING.RING_BUSY"), 1)
    v.ws(LIMITS.writes())
    v.w(COMMANDS["RISK_COMMIT"], 1)
    v.w(LOCATE_ALLOW.addr + 4 * (LOC >> 5), 1 << (LOC & 31))
    v.w(COMMANDS["BUDGET_ADD"], BUDGET)
    v.ws([*RULE.writes(), (addr("STRATEGY.RULE_SEL"), 0)])
    v.w(COMMANDS["RULE_COMMIT"], 1)
    v.r(addr("STRATEGY.RULE_STATUS"), 0, 1)  # error bit 0: the rule is valid
    v.w(COMMANDS["RULE_ARM"], 1)
    v.ws(TXC.writes())
    v.w(COMMANDS["TX_COMMIT"], 1)
    v.r(addr("TX.TX_STATUS"), 1, 1)  # cfg_valid
    v.w(COMMANDS["USERREF"], USERREF)
    v.w(TX_CTRL, 1)
    v.w(RISK_CTRL, 0)  # kill released last
    v.lines.append("snapshot")

    # A: an admin message (raw entry) and an Add Order of LOC that fires the rule.
    msgs_a = [encode("S", timestamp=3, event_code="O"), add_order(LOC, 1)]
    out_a = v.rx(100, msgs_a)
    v.lines.append("idle 600")
    assert [ev.cls for _, ev in out_a] == [ItchClass.ADMIN, ItchClass.ORDER]
    seq_trigger, ev = out_a[1]
    v.order_sent(make_order(0, MODEL_RULE, ev, seq_trigger, 0, EPOCH))  # one-shot: the rule is disarmed now
    # B: a bad FCS (MOLD_ABORT, nothing in the ring); C: A again (DUP, nothing in the ring).
    assert not v.rx(102, [add_order(LOC, 2)], bad_fcs=True)
    v.lines.append("idle 300")
    assert not v.rx(100, msgs_a)
    v.lines.append("idle 300")
    # D: an Add Order of LOC (the disarmed rule ignores it) and one of OTHER (filtered by SYMBOL).
    assert len(v.rx(102, [add_order(LOC, 3), add_order(OTHER, 4, "MSFT")])) == 1
    v.lines.append("idle 600")
    # A host order through the mailbox.
    v.ws(MBOX.writes())
    v.w(MBOX_SEND, 1)
    v.order_sent(OrderReq(Source.HOST, MBOX.side, MBOX.qty, MBOX.price, MBOX.locate, MBOX.stock, MBOX.tif,
                          clordid=MBOX.clordid, epoch=EPOCH))  # fmt: skip

    exp = expect_entries(v.items)
    assert not (exp.drop_pkts or exp.drop_orders)
    reqs = [None if isinstance(i, RingPacket) else i.req for i in v.items for _ in range(_n_entries(i))]
    v.lines.append(f"drain {len(exp.entries):x} {len(v.frames):x}")
    v.lines.append("snapshot")
    for idx, (entry, req) in enumerate(zip(exp.entries, reqs, strict=True)):
        v.lines.append(" ".join([f"expect_ring {stamp(entry, idx, RING_LOG2).hex()}", *masks(entry, req)]))
    for f in v.frames:
        v.lines.append(f"expect_tx {f.hex()}")
    # Counters of the last snapshot, from the models; every one this scenario can move, 0s included.
    v.count["RING.RING_COMMIT_ENTRIES"] = len(exp.entries)
    names = ["STATS.RX_PKTS", *(f"STATS.DROP_{r.name}" for r in list(RxReason)[1:]),
             *(f"FEED.MOLD_{s.name}" for s in MoldStatus), *(f"ITCH.ITCH_{o.name}" for o in Outcome),
             "RISK.ORDERS_SENT_HW", "RISK.ORDERS_SENT_HOST", "RING.RING_COMMIT_ENTRIES"]  # fmt: skip
    for name in names:
        v.r(addr(f"{name}.LO"), v.count[name])
        v.r(addr(f"{name}.HI"), 0)
    v.r(addr("RISK.BUDGET"), BUDGET - 2)
    v.r(addr("RING.PROD_IDX"), len(exp.entries))
    v.r(addr("RING.RING_DONE_IDX"), len(exp.entries))
    v.lines.append("end")
    return v


def _n_entries(item: RingPacket | RingOrder) -> int:
    if isinstance(item, RingOrder):
        return 1
    return len(item.msgs) + int(bool(item.gap)) + int(item.status == MoldStatus.EOS)


def write(path: Path) -> Vectors:
    v = scenario()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# generated by firmware/tb/common/cosim_vectors.py; do not edit\n" + "\n".join(v.lines) + "\n",
                    encoding="utf-8")  # fmt: skip
    return v


if __name__ == "__main__":
    write(Path(sys.argv[1]))
