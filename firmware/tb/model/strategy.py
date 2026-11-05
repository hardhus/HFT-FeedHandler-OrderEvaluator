"""Golden model of strategy_engine (audit/14 §2.3, §2.4, P4; decisions 1, 2, 6, 8, 11, 14, 16).
Pure Python, no simulator.

Decision: the model works on itch.Event values and packet boundaries as data, one message at a
time; host commands (arm, disarm, commit) are applied between messages, in the order the test says
they reach net_clk. Cycle timing is the unit test's business.

Rules:
- A rule matches a message when: it is enabled, armed, has no static error, has not fired in this
  packet; the type is in msg_mask (A, F, P only: decision 2); the locate equals the rule's; the
  event side is in side_match; price_op holds between the event price and price_threshold; and
  shares >= min_shares.
- Per message at most one rule fires: the lowest index that matches (P4). A rule fires at most once
  per packet.
- Fired orders wait for the packet event. Accepted (OK, HEARTBEAT, EOS): they leave in fire order,
  their rules are disarmed and their RULE_FIRED counters count. Any other status: they are dropped
  and the rules stay armed (decision 1). The order still leaves if its rule was disarmed or
  committed in between (decision 14).
- RULE_COMMIT replaces the rule and disarms it (decision 8). RULE_ARM and RULE_DISARM take masks.
- Static error (RULE_STATUS error bit, the rule never fires; decisions 6, 11, 16): locate 0, time in
  force not allowed, action_side code 3, qty 0, qty >= 1,000,000 in FIXED mode, threshold + offset
  outside 1..PRICE_LIMIT_MAX in THRESHOLD mode.
- The order: side from action_side (OPPOSITE of anything but B/S is 0, which risk rejects); qty =
  qty (FIXED) or min(shares, qty) (MIN); price = event price (EVENT) or threshold + offset
  (THRESHOLD); locate and stock from the event; tif from the rule.
"""

from dataclasses import dataclass, field
from enum import IntEnum

from model.itch import Event
from model.order import QTY_LIMIT, TIFS, OrderReq, Source, valid_price

N_RULES = 8
MSG_TYPES = b"AFP"  # msg_mask bit i: MSG_TYPES[i]
SIDE_BITS = b"BS"  # side_match bit i: SIDE_BITS[i]; 0 never matches, 3 any side


class PriceOp(IntEnum):
    LE = 0  # event price <= threshold
    GE = 1  # event price >= threshold


class ActionSide(IntEnum):
    BUY = 0
    SELL = 1
    OPPOSITE = 2  # the opposite of the event's side
    # 3: static error


class QtyMode(IntEnum):
    FIXED = 0
    MIN = 1  # min(event shares, qty)


class PriceMode(IntEnum):
    EVENT = 0
    THRESHOLD = 1  # price_threshold + price_offset


@dataclass(frozen=True)
class Rule:
    enable: bool = False
    msg_mask: int = 0  # 3 bits
    locate: int = 0
    side_match: int = 0  # 2 bits
    price_op: PriceOp = PriceOp.LE
    price_threshold: int = 0  # u32
    min_shares: int = 0  # u32
    action_side: int = ActionSide.BUY  # 2 bits
    qty_mode: QtyMode = QtyMode.FIXED
    qty: int = 0  # u32: the fixed quantity, or the cap in MIN mode
    price_mode: PriceMode = PriceMode.EVENT
    price_offset: int = 0  # s32
    tif: int = ord("0")


def static_error(r: Rule) -> bool:
    return (
        r.locate == 0
        or r.tif not in TIFS
        or r.action_side not in tuple(ActionSide)
        or r.qty == 0
        or (r.qty_mode == QtyMode.FIXED and r.qty >= QTY_LIMIT)
        or (r.price_mode == PriceMode.THRESHOLD and not valid_price(r.price_threshold + r.price_offset))
    )


def matches(r: Rule, ev: Event) -> bool:
    if ev.type not in MSG_TYPES or not r.msg_mask >> MSG_TYPES.index(ev.type) & 1:
        return False
    if ev.side not in SIDE_BITS or not r.side_match >> SIDE_BITS.index(ev.side) & 1:
        return False
    price_ok = ev.price <= r.price_threshold if r.price_op == PriceOp.LE else ev.price >= r.price_threshold
    return ev.locate == r.locate and price_ok and ev.shares >= r.min_shares


def order_side(r: Rule, ev: Event) -> int:
    if r.action_side == ActionSide.BUY:
        return ord("B")
    if r.action_side == ActionSide.SELL:
        return ord("S")
    return {ord("B"): ord("S"), ord("S"): ord("B")}.get(ev.side, 0)


def make_order(idx: int, r: Rule, ev: Event, seq: int, sof_ts: int, epoch: int) -> OrderReq:
    qty = r.qty if r.qty_mode == QtyMode.FIXED else min(ev.shares, r.qty)
    price = ev.price if r.price_mode == PriceMode.EVENT else r.price_threshold + r.price_offset
    return OrderReq(
        Source.RULE, order_side(r, ev), qty, price, ev.locate, ev.stock.to_bytes(8, "little"), r.tif, rule=idx,
        seq=seq, sof_ts=sof_ts, epoch=epoch,
    )  # fmt: skip


@dataclass
class Strategy:
    rules: list[Rule] = field(default_factory=lambda: [Rule()] * N_RULES)
    armed: int = 0  # RULE_ARMED
    fired: list[int] = field(default_factory=lambda: [0] * N_RULES)  # RULE_FIRED_<n>
    pending: list[OrderReq] = field(default_factory=list)
    fired_now: int = 0  # rules fired in the open packet

    def commit(self, idx: int, rule: Rule) -> None:
        self.rules[idx] = rule
        self.armed &= ~(1 << idx)

    def arm(self, mask: int) -> None:
        self.armed |= mask & (1 << N_RULES) - 1

    def disarm(self, mask: int) -> None:
        self.armed &= ~mask

    def error_bits(self) -> int:
        return sum(1 << i for i, r in enumerate(self.rules) if static_error(r))

    def message(self, ev: Event, seq: int, sof_ts: int, epoch: int) -> int | None:
        """One OUT message of the open packet; returns the rule that fired, if any."""
        for i, r in enumerate(self.rules):
            live = r.enable and self.armed >> i & 1 and not self.fired_now >> i & 1 and not static_error(r)
            if live and matches(r, ev):
                self.fired_now |= 1 << i
                self.pending.append(make_order(i, r, ev, seq, sof_ts, epoch))
                return i
        return None

    def packet(self, accepted: bool) -> list[OrderReq]:
        """The packet event; returns the orders that go to risk, in order."""
        out = self.pending if accepted else []
        if accepted:
            self.armed &= ~self.fired_now
            for o in out:
                self.fired[o.rule] += 1
        self.pending, self.fired_now = [], 0
        return out
