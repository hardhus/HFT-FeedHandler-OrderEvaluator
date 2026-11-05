"""Golden model of risk_check (audit/14 §2.5, P6, P7; decisions 5, 7, 9, 12, 15). Pure Python, no
simulator.

Decision: decide() takes one order, the cycle of the decision and the TX port's state, and returns
the reason; the time base is net_clk cycles, so the token bucket is checked to the cycle.

Rules:
- Reasons in priority order (decision 9): KILL > TX_OFF > INVALID > LOCATE > QTY > NOTIONAL >
  BUDGET > RATE > TX_BUSY. The first one that holds is the reason; NONE accepts.
- INVALID: order.valid_order() is false, or the UserRefNum counter has no number left for it.
- QTY: qty > max_qty. NOTIONAL: qty * price > max_notional (u64). BUDGET: budget == 0.
  RATE: no token. TX_BUSY: the TX queue is full.
- An accepted order spends one budget unit and one token; a rejected one spends nothing.
- Reset (decision 5): kill 1, tx_en 0, budget 0, limits 0, locate bitmap 0, bucket empty.
- Token bucket (decision 15): commit() sets the limits, fills the bucket to rate_burst and restarts
  the period at that cycle. Every rate_period cycles after it (ticks at start + k * rate_period,
  k >= 1) adds rate_refill tokens, up to rate_burst; a decision at cycle c sees the ticks at or
  before c. rate_period 0: no refill.
- budget_add() saturates at 2**32 - 1.
"""

from dataclasses import dataclass, field
from enum import IntEnum

from model.itch import Bitmap
from model.order import OrderReq, TxPort, valid_order

BUDGET_MAX = 0xFFFF_FFFF


class Reason(IntEnum):
    """risk_reason_e and the RISK_REJECT `reason` byte."""

    NONE = 0
    KILL = 1
    TX_OFF = 2
    INVALID = 3
    LOCATE = 4
    QTY = 5
    NOTIONAL = 6
    BUDGET = 7
    RATE = 8
    TX_BUSY = 9


@dataclass(frozen=True)
class Limits:
    max_qty: int = 0  # u32
    max_notional: int = 0  # u64
    rate_refill: int = 0  # u32
    rate_period: int = 0  # u32, cycles
    rate_burst: int = 0  # u32


@dataclass
class Risk:
    kill: bool = True
    tx_en: bool = False
    budget: int = 0
    limits: Limits = field(default_factory=Limits)
    allow: Bitmap = field(default_factory=Bitmap)  # LOCATE_ALLOW
    tokens: int = 0
    start: int = 0  # cycle of the last commit
    ticks: int = 0  # ticks already added to tokens
    rejects: dict[Reason, int] = field(default_factory=lambda: {r: 0 for r in Reason if r != Reason.NONE})
    accepted: int = 0

    def commit(self, limits: Limits, cycle: int) -> None:
        self.limits, self.tokens, self.start, self.ticks = limits, limits.rate_burst, cycle, 0

    def budget_add(self, n: int) -> None:
        self.budget = min(BUDGET_MAX, self.budget + n)

    def tokens_at(self, cycle: int) -> int:
        """Refill up to `cycle` and return the tokens."""
        lim = self.limits
        if lim.rate_period:
            ticks = (cycle - self.start) // lim.rate_period
            self.tokens = min(lim.rate_burst, self.tokens + lim.rate_refill * (ticks - self.ticks))
            self.ticks = ticks
        return self.tokens

    def reason(self, o: OrderReq, cycle: int, tx: TxPort) -> Reason:
        lim = self.limits
        checks = (
            (Reason.KILL, self.kill),
            (Reason.TX_OFF, not self.tx_en),
            (Reason.INVALID, not valid_order(o) or tx.refs_left() == 0),
            (Reason.LOCATE, not self.allow.subscribed(o.locate)),
            (Reason.QTY, o.qty > lim.max_qty),
            (Reason.NOTIONAL, o.qty * o.price > lim.max_notional),
            (Reason.BUDGET, self.budget == 0),
            (Reason.RATE, self.tokens_at(cycle) == 0),
            (Reason.TX_BUSY, not tx.free()),
        )
        return next((r for r, hit in checks if hit), Reason.NONE)

    def decide(self, o: OrderReq, cycle: int, tx: TxPort) -> Reason:
        """One order at `cycle`: on NONE it is spent for and queued on tx."""
        r = self.reason(o, cycle, tx)
        if r == Reason.NONE:
            self.budget -= 1
            self.tokens -= 1
            self.accepted += 1
            tx.accept(o)
        else:
            self.rejects[r] += 1
        return r

    def set_tx_en(self, on: bool, tx: TxPort) -> list[OrderReq]:
        """tx_en; its fall cancels the queued orders, each counted as TX_OFF (decision 7). Whether
        their budget and token come back is A6 (audit/14, decided at S4): not here yet."""
        self.tx_en = on
        if on:
            return []
        out = tx.tx_off()
        self.rejects[Reason.TX_OFF] += len(out)
        return out
