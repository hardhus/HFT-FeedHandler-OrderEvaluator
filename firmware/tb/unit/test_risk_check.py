"""risk_check against the risk and order models (tb/model/risk.py, tb/model/order.py; audit/14 §2.5, §2.10;
decisions 5, 7, 9, 10, 12, 13, 15, 20, 21, 22, 24, 25).

Cycles: inputs driven for step s are sampled at edge s; the monitor reads the outputs after edge s. In step s, as
the module header states: a handover (m_tx_ready at s, m_tx_valid after edge s-1); a kill rise or tx_en fall
cancels what is queued; the decision of the order taken at step s-3, with the step-s controls and the state
before edge s (TX_BUSY: 8 live orders before this step's handover; RING_FULL: no ring credit); its RISK_REJECT
if it gets a credit (else it is dropped: ev_rej_dropped), else the oldest cancelled order's; then the commands
(limits, budget, UserRefNum) and a ring credit given back (the packer model: every written entry and every
handover's ORDER_SENT returns its credit later, with probability ret_p per cycle); then a new order is taken
(hardware first) and its allow bit read from the bitmap before this step's bitmap write. The expected outputs
of step s are checked after edge s, every field, every cycle.
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
from model.itch import wire
from model.order import PRICE_LIMIT_MAX, QTY_LIMIT, USER_REF_MAX, OrderReq, Source, TxConfig, TxPort, template
from model.risk import BUDGET_MAX, Limits, Reason, Risk

PERIOD_PS = 6400
N_RANDOM = 100_000  # cycles of tc_random_golden
SWEEP = 2048  # allow-bitmap words cleared after reset
QD = 8
CFG = TxConfig(template())
SPACES = b" " * 14
OPEN = Limits(max_qty=999_999, max_notional=1 << 63, rate_refill=1, rate_period=1, rate_burst=1000)


class TbTx(TxPort):
    """The model's TX port plus the cancelled orders that still hold their slots until written out."""

    def __init__(self) -> None:
        super().__init__(CFG)
        self.held: deque[tuple[OrderReq, Reason, int]] = deque()
        self.full_now = False  # TX_BUSY of the decision being made (set by the TB)

    def free(self) -> bool:
        return not self.full_now


def order(source=Source.HOST, **kw) -> OrderReq:
    base = {
        "source": source, "side": ord("B"), "qty": 100, "price": 1_000_000, "locate": 42, "stock": b"MSFT    ",
        "tif": ord("0"), "clordid": b"CL 1          " if source == Source.HOST else None,
    }  # fmt: skip
    return OrderReq(**{**base, **kw})


def hw(**kw) -> OrderReq:
    kw.setdefault("rule", 3)
    kw.setdefault("seq", random.getrandbits(64))
    kw.setdefault("sof_ts", random.getrandbits(64))
    kw.setdefault("epoch", random.getrandbits(8))
    return order(Source.RULE, **kw)


BINS = [
    *[f"reason_{r.name}" for r in Reason if r != Reason.NONE],
    "accept_hw", "accept_host", "notional_equal", "token_tick_exact", "budget_saturate", "cancel_kill",
    "cancel_tx_off", "cancel_both", "drain_preempted", "host_paused_by_drain", "handover_on_cancel", "userref_ok",
    "userref_ignored_live", "userref_ignored_queue", "userref_ignored_zero", "refs_exhausted",
    "lookup_same_cycle_write", "invalid_host_clordid", "kill_masks_invalid", "second_cancel_while_draining",
    "accept_while_draining", "rej_dropped", "accept_last_credit", "last_budget_add_zero", "last_budget_add",
]  # fmt: skip


@dataclass
class Exp:
    """Outputs after one edge."""

    tx: tuple | None = None  # (user_ref, order) on m_tx_*
    rej: tuple | None = None  # (reason, order, ts) on m_rej_*
    budget: int = 0
    ignored: bool = False
    dropped: bool = False


@dataclass
class Env:
    dut: object
    cov: Coverage | None = None
    risk: Risk = field(default_factory=Risk)
    tx: TbTx = field(default_factory=TbTx)
    s: int = 0  # the last edge
    kill: bool = True
    tx_en: bool = False
    pipe: dict = field(default_factory=dict)  # decision step -> (order, allowed)
    host: deque = field(default_factory=deque)  # host orders waiting (the mailbox FIFO)
    exp: dict = field(default_factory=dict)  # edge -> Exp
    tx_valid: dict = field(default_factory=dict)  # edge -> m_tx_valid expected
    sent: list = field(default_factory=list)  # (edge, user_ref, order)
    rejects: list = field(default_factory=list)  # (edge, reason, order, ts)
    tx_seen: list = field(default_factory=list)  # (edge, user_ref, order) the monitor saw on m_tx_*
    paused: bool = False
    pending: int = 0  # entries the packer model holds: their credits come back later
    ret_p: float = 1.0  # chance per cycle that one credit comes back
    drops: list = field(default_factory=list)  # (edge, reason, order) of decision rejects with no credit

    def hit(self, name: str) -> None:
        if self.cov is not None:
            self.cov.hit(name)

    def idle_inputs(self) -> None:
        d = self.dut
        d.s_hw_valid.value = 0
        d.cfg_lim_wr.value = 0
        d.cmd_budget_add.value = 0
        d.cmd_userref_wr.value = 0
        d.sym_req_valid.value = 0
        d.ring_credit_ret.value = 0
        for name, w in (("rule", 3), ("side", 8), ("qty", 32), ("price", 32), ("locate", 16), ("stock", 64),
                        ("tif", 8), ("seq", 64), ("sof_ts", 64), ("epoch", 8)):  # fmt: skip
            getattr(d, f"s_hw_{name}").value = random.getrandbits(w)
        for name, w in (("max_qty", 32), ("max_notional", 64), ("rate_refill", 32), ("rate_period", 32),
                        ("rate_burst", 32)):  # fmt: skip
            getattr(d, f"cfg_{name}").value = random.getrandbits(w)
        d.cmd_budget_amount.value = random.getrandbits(32)
        d.cmd_userref_value.value = random.getrandbits(32)
        for name, w in (("wr", 1), ("addr", 11), ("wdata", 32), ("wstrb", 4)):
            getattr(d, f"sym_req_{name}").value = random.getrandbits(w)

    def drive_host(self) -> None:
        d = self.dut
        d.s_host_valid.value = int(bool(self.host))
        o = self.host[0] if self.host else order(side=random.getrandbits(8), clordid=random.randbytes(14))
        d.s_host_side.value = o.side
        d.s_host_qty.value = o.qty
        d.s_host_price.value = o.price
        d.s_host_locate.value = o.locate
        d.s_host_stock.value = wire(o.stock)
        d.s_host_tif.value = o.tif
        d.s_host_clordid.value = wire(o.clordid)

    async def step(self, hw_order: OrderReq | None = None, ready: bool = True, kill: bool | None = None,
                   tx_en: bool | None = None, limits: Limits | None = None, budget_add: int | None = None,
                   userref: int | None = None, sym: tuple[int, int, int] | None = None) -> None:  # fmt: skip
        """One step. sym: (word, data, strb) written to the allow bitmap."""
        d, r, tx = self.dut, self.risk, self.tx
        s = self.s + 1
        self.idle_inputs()
        ts, epoch = random.getrandbits(64), random.getrandbits(8)
        d.ts_now.value = ts
        d.epoch_now.value = epoch
        d.m_tx_ready.value = int(ready)
        e = Exp()

        # 1. handover
        queued_before, held_before = len(tx.queue), len(tx.held)
        handover = ready and self.tx_valid.get(s - 1, False)
        if handover:
            self.pending += 1  # its ORDER_SENT
            snt = tx.send()
            self.sent.append((s, snt.user_ref, snt.req))
            assert snt.user_ref == self.exp[s - 1].tx[0]

        # 2. controls and cancels
        if kill is not None:
            self.kill = kill
        if tx_en is not None:
            self.tx_en = tx_en
        d.ctl_kill.value = int(self.kill)
        d.ctl_tx_en.value = int(self.tx_en)
        rise, fall = self.kill and not r.kill, not self.tx_en and r.tx_en
        cancelled = r.controls(tx, kill=self.kill, tx_en=self.tx_en)
        if cancelled:
            self.hit("cancel_both" if rise and fall else "cancel_kill" if rise else "cancel_tx_off")
            if handover:
                self.hit("handover_on_cancel")
            if held_before:
                self.hit("second_cancel_while_draining")
        new_held = [(o, Reason.KILL if rise else Reason.TX_OFF, ts) for o in cancelled]

        # 3. the decision
        rej = None
        spent_last = False  # an accept spends the last budget unit
        if s in self.pipe:
            o, allowed = self.pipe.pop(s)
            assert queued_before + held_before <= QD, "held + live above 8"
            tx.full_now = queued_before == QD  # decision 24
            reason = r.reason(o, s, tx, allowed)
            self._cover_decision(o, reason, s, held_before)
            if reason == Reason.NONE:
                if held_before:
                    self.hit("accept_while_draining")
                if r.credits == 1:
                    self.hit("accept_last_credit")
                spent_last = r.budget == 1
                r.decide(o, s, tx, allowed)
                self.hit("accept_hw" if o.source == Source.RULE else "accept_host")
            else:
                r.decide(o, s, tx, allowed)  # counts the reject
                if r.last_written:
                    rej = (reason, o, ts)
                else:
                    self.drops.append((s, reason, o))
                    e.dropped = True
                    self.hit("rej_dropped")
        if rej is None and held_before:
            o, reason, cts = tx.held.popleft()
            rej = (reason, o, cts)
        elif rej is not None and held_before:
            self.hit("drain_preempted")
        tx.held.extend(new_held)
        e.rej = rej
        if rej is not None:
            self.rejects.append((s, *rej))
            self.pending += 1

        # 4. commands
        if limits is not None:
            d.cfg_lim_wr.value = 1
            for name in ("max_qty", "max_notional", "rate_refill", "rate_period", "rate_burst"):
                getattr(d, f"cfg_{name}").value = getattr(limits, name)
            r.commit(limits, s + 1)
        if budget_add is not None:
            d.cmd_budget_add.value = 1
            d.cmd_budget_amount.value = budget_add
            if r.budget + budget_add > BUDGET_MAX:
                self.hit("budget_saturate")
            if spent_last:
                self.hit("last_budget_add_zero" if budget_add == 0 else "last_budget_add")
            r.budget_add(budget_add)
        if userref is not None:
            d.cmd_userref_wr.value = 1
            d.cmd_userref_value.value = userref
            ok = self.kill and queued_before + held_before == 0 and userref != 0
            if ok:
                tx.user_ref_next = userref
                self.hit("userref_ok")
            else:
                self.hit("userref_ignored_live" if not self.kill else
                         "userref_ignored_queue" if queued_before + held_before else "userref_ignored_zero")  # fmt: skip
            e.ignored = not ok

        if self.pending and random.random() < self.ret_p:
            d.ring_credit_ret.value = 1
            self.pending -= 1
            r.credit_return()

        # 5. take a new order; its allow bit before this step's bitmap write
        taken = None
        if hw_order is not None:
            taken = hw_order
            d.s_hw_valid.value = 1
            for name in ("rule", "side", "qty", "price", "locate", "tif", "seq", "sof_ts", "epoch"):
                getattr(d, f"s_hw_{name}").value = getattr(hw_order, name)
            d.s_hw_stock.value = wire(hw_order.stock)
        self.drive_host()
        host_ready = hw_order is None and held_before == 0
        if sym is not None:
            d.sym_req_valid.value = 1
            d.sym_req_wr.value = 1
            d.sym_req_addr.value, d.sym_req_wdata.value, d.sym_req_wstrb.value = sym
        await ReadOnly()
        assert int(d.s_host_ready.value) == int(host_ready), (
            f"step {s}: s_host_ready {int(d.s_host_ready.value)}, hw {int(d.s_hw_valid.value)} held {held_before} "
            f"m_tx_valid {int(d.m_tx_valid.value)} rst {int(d.rst.value)} host {len(self.host)} q {len(tx.queue)}"
        )
        sym_ok = sym is not None and bool(int(d.sym_req_ready.value))
        if taken is None and self.host and host_ready:
            taken = replace(self.host.popleft(), epoch=epoch)
        elif self.host and held_before and hw_order is None:
            self.hit("host_paused_by_drain")
        if taken is not None:
            self.pipe[s + 3] = (taken, r.allow.subscribed(taken.locate))
        if sym_ok:
            if taken is not None and taken.locate >> 5 == sym[0]:
                self.hit("lookup_same_cycle_write")
            r.allow.write(*sym)

        # 6. expected outputs after edge s
        valid = not tx.held and bool(tx.queue)
        self.tx_valid[s] = valid
        e.tx = (tx.user_ref_next, tx.queue[0]) if valid else None
        e.budget = r.budget
        self.exp[s] = e
        await RisingEdge(d.clk)
        self.s = s

    def _cover_decision(self, o: OrderReq, reason: Reason, s: int, held: int) -> None:
        if reason != Reason.NONE:
            self.hit(f"reason_{reason.name}")
        if reason == Reason.KILL and self.cov is not None and not (0 < o.qty < QTY_LIMIT):
            self.hit("kill_masks_invalid")
        if reason == Reason.INVALID and self.tx.refs_left() == 0:
            self.hit("refs_exhausted")
        if reason == Reason.INVALID and o.source == Source.HOST and o.clordid and b"-" in o.clordid:
            self.hit("invalid_host_clordid")
        lim = self.risk.limits
        if reason == Reason.NONE and o.qty * o.price == lim.max_notional:
            self.hit("notional_equal")
        r = self.risk
        if reason in (Reason.NONE, Reason.RATE) and lim.rate_period and s > r.start \
                and (s - r.start) % lim.rate_period == 0:  # fmt: skip
            self.hit("token_tick_exact")

    async def idle(self, n: int = 1, **kw) -> None:
        for _ in range(n):
            await self.step(**kw)

    async def drain(self) -> None:
        """Until nothing is in flight and every credit is back."""
        self.ret_p = 1.0
        while self.pipe or self.tx.held or self.tx.queue or self.host or self.pending:
            await self.step()
        await self.idle(3)

    async def monitor(self) -> None:
        d = self.dut
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            if not self.paused and self.s in self.exp:
                self._check(self.exp.pop(self.s))

    def _check(self, e: Exp) -> None:
        d, s = self.dut, self.s
        assert int(d.m_tx_valid.value) == (e.tx is not None), f"edge {s}: m_tx_valid, model {e.tx}"
        if e.tx is not None:
            ref, o = e.tx
            got = (int(d.m_tx_user_ref.value), int(d.m_tx_source.value), int(d.m_tx_rule.value),
                   int(d.m_tx_side.value), int(d.m_tx_qty.value), int(d.m_tx_price.value), int(d.m_tx_locate.value),
                   int(d.m_tx_stock.value), int(d.m_tx_tif.value), int(d.m_tx_clordid.value), int(d.m_tx_seq.value),
                   int(d.m_tx_sof_ts.value), int(d.m_tx_epoch.value))  # fmt: skip
            assert got == (ref & 0xFFFFFFFF, *fields(o)), f"edge {s}: m_tx {got}, model {(ref, o)}"
            if not self.tx_seen or self.tx_seen[-1][1:] != (ref, o):
                self.tx_seen.append((s, ref, o))
        assert int(d.m_rej_valid.value) == (e.rej is not None), f"edge {s}: m_rej_valid, model {e.rej}"
        if e.rej is not None:
            reason, o, ts = e.rej
            got = (int(d.m_rej_reason.value), int(d.m_rej_source.value), int(d.m_rej_rule.value),
                   int(d.m_rej_side.value), int(d.m_rej_qty.value), int(d.m_rej_price.value),
                   int(d.m_rej_locate.value), int(d.m_rej_stock.value), int(d.m_rej_tif.value),
                   int(d.m_rej_clordid.value), int(d.m_rej_seq.value), int(d.m_rej_sof_ts.value),
                   int(d.m_rej_epoch.value), int(d.m_rej_ts.value))  # fmt: skip
            assert got == (int(reason), *fields(o), ts), f"edge {s}: m_rej {got}, model {e.rej}"
        assert int(d.st_budget.value) == e.budget, f"edge {s}: st_budget {int(d.st_budget.value)}, model {e.budget}"
        assert int(d.ev_userref_ignored.value) == e.ignored, f"edge {s}: ev_userref_ignored"
        assert int(d.ev_rej_dropped.value) == e.dropped, f"edge {s}: ev_rej_dropped"

    async def open_path(self, limits: Limits = OPEN, budget: int = 1000, allow: tuple[int, ...] = (42,)) -> None:
        """The reset contract's order: limits, bitmap, budget, then tx_en, kill released last."""
        await self.step(limits=limits)
        for loc in allow:
            await self.step(sym=(loc >> 5, 1 << (loc & 31), 0xF))
        await self.step(budget_add=budget)
        await self.step(tx_en=True)
        await self.step(kill=False)


def fields(o: OrderReq) -> tuple:
    clordid = o.clordid if o.clordid is not None else SPACES
    return (int(o.source), o.rule, o.side, o.qty, o.price & 0xFFFFFFFF, o.locate, wire(o.stock), o.tif,
            wire(clordid), o.seq, o.sof_ts, o.epoch)  # fmt: skip


async def start(dut, cov: Coverage | None = None, sweep: bool = True) -> Env:
    """Reset (kill 1, tx_en 0), then the bitmap sweep unless sweep=False."""
    e = Env(dut, cov)
    e.idle_inputs()
    e.drive_host()
    dut.ctl_kill.value = 1
    dut.ctl_tx_en.value = 0
    dut.m_tx_ready.value = 0
    dut.ts_now.value = 0
    dut.epoch_now.value = 0
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    cocotb.start_soon(e.monitor())
    if sweep:
        await e.idle(SWEEP + 2)
    return e


# ---------------------------------------------------------------- directed


@cocotb.test()
async def tc_reset_state(dut):
    """After reset: kill, everything 0, the bitmap swept; a host order is rejected KILL."""
    e = await start(dut)
    e.host.append(order())
    await e.drain()
    assert [(r, o.qty) for _, r, o, _ in e.rejects] == [(Reason.KILL, 100)]
    assert int(dut.st_budget.value) == 0 and int(dut.m_tx_valid.value) == 0


@cocotb.test()
async def tc_latency_and_fields(dut):
    """Hand values: an order taken at step k is on m_tx_* from edge k+3, with UserRefNum 1 and every field; a
    reject is on m_rej_* at edge k+3 too."""
    e = await start(dut)
    await e.open_path()
    o = hw(rule=5, side=ord("S"), qty=250, price=999_900, locate=42, stock=b"ZVZZT   ", tif=ord("E"),
           seq=0x0102030405060708, sof_ts=0x1112131415161718, epoch=0x5A)  # fmt: skip
    k = e.s + 1
    await e.step(hw_order=o, ready=False)
    await e.idle(5, ready=False)
    ((edge, ref, got),) = e.tx_seen
    assert (edge, ref) == (k + 3, 1)
    assert (got.rule, chr(got.side), got.qty, got.price, got.stock, chr(got.tif)) == (5, "S", 250, 999_900,
                                                                                      b"ZVZZT   ", "E")  # fmt: skip
    assert (got.seq, got.sof_ts, got.epoch) == (0x0102030405060708, 0x1112131415161718, 0x5A)
    k = e.s + 1
    await e.step(hw_order=hw(locate=43), ready=False)
    await e.idle(3, ready=False)
    assert e.rejects[-1][:2] == (k + 3, Reason.LOCATE)
    await e.drain()
    assert [(ref, o.qty) for _, ref, o in e.sent] == [(1, 250)]


@cocotb.test()
async def tc_each_reason(dut):
    """Each check broken alone on an open path, by hand."""
    e = await start(dut)
    await e.open_path(limits=Limits(max_qty=1000, max_notional=1000 * 2_000_000, rate_refill=0, rate_period=0,
                                    rate_burst=100), budget=100)  # fmt: skip
    cases = [
        (hw(price=0), Reason.INVALID), (hw(price=PRICE_LIMIT_MAX + 1), Reason.INVALID), (hw(qty=0), Reason.INVALID),
        (hw(qty=QTY_LIMIT), Reason.INVALID), (hw(side=ord("T")), Reason.INVALID), (hw(tif=ord("6")), Reason.INVALID),
        (hw(locate=43), Reason.LOCATE), (hw(qty=1001), Reason.QTY),
        (hw(qty=1000, price=2_000_001), Reason.NOTIONAL), (hw(qty=1000, price=2_000_000), Reason.NONE),
    ]  # fmt: skip
    for o, _ in cases:
        await e.step(hw_order=o)
    e.host.append(order(clordid=b"CL-1          "))
    await e.drain()
    got = [r for _, r, _, _ in e.rejects]
    assert got == [r for _, r in cases if r != Reason.NONE] + [Reason.INVALID]
    assert len(e.sent) == 1


@cocotb.test()
async def tc_priority(dut):
    """Every check broken, then fixed one by one from the top: the reason walks down decision 9's order. A
    control change applies to the decisions from its step on, so it comes before the next order is taken."""
    e = await start(dut)
    await e.step(limits=Limits(max_qty=1000, max_notional=10**9, rate_refill=0, rate_period=0, rate_burst=1))
    bad = hw(price=0, qty=2000, locate=43)
    plan = [
        ({}, bad, Reason.KILL),
        ({"kill": False}, bad, Reason.TX_OFF),
        ({"tx_en": True}, bad, Reason.INVALID),
        ({}, replace(bad, price=1_000_000), Reason.LOCATE),
        ({"sym": (1, 1 << 10, 0xF)}, replace(bad, price=1_000_000, locate=42), Reason.LOCATE),  # not seen yet
        ({}, replace(bad, price=1_000_000, locate=42), Reason.QTY),
        ({}, hw(qty=1000, price=1_000_001), Reason.NOTIONAL),
        ({}, hw(qty=1000, price=1000), Reason.BUDGET),
        ({"budget_add": 5}, hw(qty=1000, price=1000), Reason.NONE),
        ({}, hw(qty=1000, price=1000), Reason.RATE),
    ]
    for before, o, _ in plan:
        sym = before.pop("sym", None)
        if before:
            await e.step(**before)
        await e.step(hw_order=o, sym=sym)  # the bitmap write in the take step is not seen by this order
        await e.idle(3)
    await e.drain()
    assert [r for _, r, _, _ in e.rejects] == [r for _, _, r in plan if r != Reason.NONE]
    assert len(e.sent) == 1


@cocotb.test()
async def tc_token_bucket(dut):
    """Decision 15, hand cycles: a commit at step c applies from s0 = c+1 with burst 2, +1 every 5 cycles. Orders
    taken at c-2, c-1, c are decided at s0, s0+1, s0+2: two tokens, then none. Taken at c+2, c+3: decided at
    s0+4 (no tick yet) and s0+5 (the first tick)."""
    e = await start(dut)
    lim = Limits(max_qty=1000, max_notional=1 << 63, rate_refill=1, rate_period=5, rate_burst=2)
    await e.open_path(limits=lim)
    await e.idle(4)
    await e.step(hw_order=hw(qty=1))
    await e.step(hw_order=hw(qty=2))
    await e.step(hw_order=hw(qty=3), limits=lim)
    s0 = e.s + 1
    await e.step()
    await e.step(hw_order=hw(qty=4))
    await e.step(hw_order=hw(qty=5))
    await e.drain()
    assert [(s - s0, r, o.qty) for s, r, o, _ in e.rejects] == [(2, Reason.RATE, 3), (4, Reason.RATE, 4)]
    assert [o.qty for _, _, o in e.sent] == [1, 2, 5]


@cocotb.test()
async def tc_budget(dut):
    """Budget 0 rejects; the add after a decision in its step; saturation at 2^32 - 1."""
    e = await start(dut)
    await e.open_path(budget=1)
    await e.step(hw_order=hw())
    await e.step(hw_order=hw())
    await e.idle(3)
    await e.step(budget_add=BUDGET_MAX)
    await e.step(budget_add=7)
    await e.drain()
    assert [r for _, r, _, _ in e.rejects] == [Reason.BUDGET]
    assert int(dut.st_budget.value) == BUDGET_MAX


@cocotb.test()
async def tc_cancel(dut):
    """Decisions 7, 20, 21: a kill rise and a tx_en fall cancel the queue, one RISK_REJECT per cycle with the
    cancel's timestamp; no refund; the order handed over in the cancel cycle is not cancelled."""
    e = await start(dut)
    await e.open_path(budget=10)
    for q in (1, 2, 3, 4):
        await e.step(hw_order=hw(qty=q), ready=False)
    await e.idle(3, ready=False)
    budget = e.risk.budget
    await e.step(kill=True, ready=True)  # handover of qty 1 and the kill in one step
    await e.idle(6)
    assert [o.qty for _, _, o in e.sent] == [1]
    assert [(r, o.qty) for _, r, o, _ in e.rejects] == [(Reason.KILL, 2), (Reason.KILL, 3), (Reason.KILL, 4)]
    assert len({ts for _, _, _, ts in e.rejects}) == 1
    assert e.risk.budget == budget
    await e.step(kill=False)
    for q in (5, 6):
        await e.step(hw_order=hw(qty=q), ready=False)
    await e.idle(3, ready=False)
    await e.step(tx_en=False, ready=False)
    await e.drain()
    assert [(r, o.qty) for _, r, o, _ in e.rejects[3:]] == [(Reason.TX_OFF, 5), (Reason.TX_OFF, 6)]


@cocotb.test()
async def tc_userref(dut):
    """Decision 22: the write takes effect only while killed with an empty queue and a value not 0; the counter
    adds 1 per handover and stops at 2^32 (INVALID)."""
    e = await start(dut)
    await e.step(userref=0)  # ignored: 0
    await e.step(userref=USER_REF_MAX)  # taken: one number left
    await e.open_path()
    await e.step(userref=5)  # ignored: not killed
    await e.step(hw_order=hw(qty=1))
    await e.step(hw_order=hw(qty=2))
    await e.drain()
    assert [(ref, o.qty) for _, ref, o in e.sent] == [(USER_REF_MAX, 1)]
    assert [(r, o.qty) for _, r, o, _ in e.rejects] == [(Reason.INVALID, 2)]


@cocotb.test()
async def tc_tx_busy(dut):
    """Eight queued orders fill the queue; the ninth is TX_BUSY; cancelled orders hold their slots."""
    e = await start(dut)
    await e.open_path()
    for q in range(1, 10):
        await e.step(hw_order=hw(qty=q), ready=False)
    await e.idle(3, ready=False)
    assert [(r, o.qty) for _, r, o, _ in e.rejects] == [(Reason.TX_BUSY, 9)]
    await e.drain()
    assert [o.qty for _, _, o in e.sent] == list(range(1, 9))


@cocotb.test()
async def tc_release_right_after_kill(dut):
    """Decision 24, by hand: 8 orders queued and cancelled by a kill; kill released at once; an order taken in the
    release step is accepted although the 8 cancelled orders still hold slots."""
    e = await start(dut)
    await e.open_path()
    for q in range(1, 9):
        await e.step(hw_order=hw(qty=q), ready=False)
    await e.idle(3, ready=False)
    await e.step(kill=True, ready=False)
    await e.step(kill=False, hw_order=hw(qty=9), ready=False)
    await e.idle(3, ready=False)
    # the drain starts in the release step; the accept of qty 9 does not hold it up
    assert [(r, o.qty) for _, r, o, _ in e.rejects] == [(Reason.KILL, q) for q in range(1, 5)]
    await e.drain()
    assert [r for _, r, _, _ in e.rejects] == [Reason.KILL] * 8
    assert [o.qty for _, _, o in e.sent] == [9]


@cocotb.test()
async def tc_cancel_across_the_wrap(dut):
    """Regression (S3): a cancel whose live orders cross the end of the 16 slots must mark every one of them. The
    slot offset was computed one bit wider than the slot index, so slots after the wrap kept an older cancel's
    reason and timestamp. 14 orders go through (the read pointer at 14), then 4 are queued (slots 14, 15, 0, 1)
    and cancelled: all 4 carry this cancel's timestamp."""
    e = await start(dut)
    await e.open_path()
    for q in range(14):
        await e.step(hw_order=hw(qty=q + 1))
    await e.drain()
    for q in range(4):
        await e.step(hw_order=hw(qty=100 + q), ready=False)
    await e.idle(3, ready=False)
    await e.step(kill=True, ready=False)
    await e.drain()
    rej = [(r, o.qty) for _, r, o, _ in e.rejects]
    assert rej == [(Reason.KILL, 100 + q) for q in range(4)]
    assert len({ts for _, _, _, ts in e.rejects}) == 1


@cocotb.test()
async def tc_ring_credits(dut):
    """Decision 25, by hand: with no credit coming back, 16 orders are accepted and handed over (each reserves
    its ORDER_SENT's entry); the 17th is RING_FULL and its reject is dropped, as is a LOCATE reject after it;
    once one credit is back, a reject is written; once one more is back, an order is accepted."""
    e = await start(dut)
    await e.open_path()
    await e.drain()
    e.ret_p = 0.0
    for q in range(16):
        await e.step(hw_order=hw(qty=q + 1))
    await e.step(hw_order=hw(qty=17))
    await e.step(hw_order=hw(qty=18, locate=43))
    await e.idle(4)
    assert [(r, o.qty) for _, r, o in e.drops] == [(Reason.RING_FULL, 17), (Reason.LOCATE, 18)]
    assert len(e.sent) == 16 and e.risk.credits == 0 and e.pending == 16
    e.ret_p = 1.0
    await e.step()  # one credit back
    e.ret_p = 0.0
    await e.step(hw_order=hw(qty=19, locate=43))
    await e.idle(4)
    assert e.rejects[-1][1:3] == (Reason.LOCATE, e.rejects[-1][2]) and e.rejects[-1][2].qty == 19
    e.ret_p = 1.0
    await e.step()
    e.ret_p = 0.0
    await e.step(hw_order=hw(qty=20))
    await e.idle(4)
    assert e.sent[-1][2].qty == 20
    await e.drain()


@cocotb.test()
async def tc_sweep(dut):
    """During the sweep after reset the bitmap reads 0 and commands wait."""
    e = await start(dut, sweep=False)
    await e.open_path()  # the bitmap write waits for the sweep (not accepted here)
    await e.step(hw_order=hw())
    await e.idle(SWEEP)
    await e.step(sym=(1, 1 << 10, 0xF))
    await e.step(hw_order=hw())
    await e.drain()
    assert [r for _, r, _, _ in e.rejects] == [Reason.LOCATE]
    assert len(e.sent) == 1


# ---------------------------------------------------------------- random

LOCATES = (1, 42, 43, 0xFFFF)


def random_order() -> OrderReq:
    kw = {
        "side": random.choice(b"BBSST"), "qty": random.choice((0, 1, 100, 999, 1000, 1001, 999_999, 1_000_000)),
        "price": random.choice((0, 1, 1000, 2_000_000, PRICE_LIMIT_MAX, PRICE_LIMIT_MAX + 1)),
        "locate": random.choice(LOCATES), "tif": random.choice(b"00035E6"), "stock": random.randbytes(8),
    }  # fmt: skip
    if random.random() < 0.5:
        return hw(rule=random.randrange(8), **kw)
    cl = bytes(random.choice(b"ABCxyz019 ") for _ in range(14))
    if random.random() < 0.05:
        cl = cl[:5] + b"-" + cl[6:]
    return order(clordid=cl, **kw)


def random_limits() -> Limits:
    return Limits(
        max_qty=random.choice((0, 999, 1000, 999_999)), max_notional=random.choice((0, 10**6, 2 * 10**9, 1 << 63)),
        rate_refill=random.choice((0, 1, 3)), rate_period=random.choice((0, 1, 7, 50)),
        rate_burst=random.choice((0, 1, 5, 100)),
    )  # fmt: skip


@cocotb.test()
async def tc_random_golden(dut):
    """A fixed phase from reset that hits every bin, then N_RANDOM random cycles against the models."""
    cov = Coverage("risk_check", BINS)
    e = await start(dut, cov)
    await every_bin(e)
    await e.drain()
    await e.open_path(budget=10**6, allow=(1, 42, 0xFFFF))
    ready_p = 1.0
    while e.s < N_RANDOM:
        if e.s % 2000 == 0:
            ready_p = random.choice((1.0, 0.5, 0.05))
            e.ret_p = random.choice((1.0, 1.0, 0.3, 0.02))
        kw = {"ready": random.random() < ready_p}
        o = random_order()
        if o.source == Source.RULE and random.random() < 0.3:
            kw["hw_order"] = o
        elif o.source == Source.HOST and random.random() < 0.3 and len(e.host) < 4:
            e.host.append(o)
        x = random.random()
        if x < 0.004:
            kw["kill"] = not e.kill
        elif x < 0.008:
            kw["tx_en"] = not e.tx_en
        elif x < 0.012:
            kw["limits"] = random_limits()
        elif x < 0.02:
            kw["budget_add"] = random.choice((0, 1, 10, 1000, BUDGET_MAX))
        elif x < 0.024:
            kw["userref"] = random.choice((0, 1, 1000, USER_REF_MAX - 2))
        elif x < 0.03:
            loc = random.choice(LOCATES)
            kw["sym"] = (loc >> 5, random.getrandbits(32), random.getrandbits(4))
        await e.step(**kw)
    await e.drain()
    dut._log.info(f"{len(e.sent)} sent, {len(e.rejects)} rejected")
    cov.check(Path.cwd())


async def every_bin(e: Env) -> None:
    """From reset (kill, budget 0, limits 0, bitmap 0, counter 1): a fixed sequence that hits every bin (the
    per-cycle checks run on it too)."""
    await e.step(userref=0)  # userref_ignored_zero
    await e.step(hw_order=hw(qty=0))  # reason_KILL, kill_masks_invalid
    await e.idle(3)
    lim = Limits(max_qty=1000, max_notional=1000 * 1000, rate_refill=1, rate_period=4, rate_burst=1)
    await e.open_path(limits=lim, budget=0)
    await e.step(hw_order=hw(qty=10, price=1000))  # reason_BUDGET (notional 10,000 passes)
    await e.idle(3)
    for add in (0, 5):  # the last unit spent with an add in the decision's step (taken at s, decided at s + 3)
        await e.step(budget_add=1)
        await e.idle(4)  # a token
        await e.step(hw_order=hw(qty=10, price=1000))
        await e.idle(2)
        await e.step(budget_add=add)  # last_budget_add_zero (0 left), last_budget_add (5 left)
        await e.step(hw_order=hw(qty=10, price=1000))  # with 0 left: reason_BUDGET; with 5: no budget reject
        await e.idle(3)
    await e.step(userref=9)  # userref_ignored_live
    await e.step(budget_add=BUDGET_MAX)
    await e.step(budget_add=7)  # budget_saturate
    for o in (hw(price=0), hw(locate=43), hw(qty=1001), hw(qty=1000, price=1001), hw(qty=1000, price=1000)):
        await e.step(hw_order=o)  # INVALID, LOCATE, QTY, NOTIONAL, then notional_equal and accept_hw
    for _ in range(12):  # a token every 4 cycles: RATE, and token_tick_exact
        await e.step(hw_order=hw(qty=10))
    e.host.append(order(clordid=b"CL-1          "))  # invalid_host_clordid
    e.host.append(order())  # accept_host
    await e.drain()
    await e.step(tx_en=False)
    await e.step(hw_order=hw())  # reason_TX_OFF
    await e.idle(3)
    big = Limits(max_qty=1000, max_notional=1 << 63, rate_refill=1, rate_period=1, rate_burst=100)
    await e.step(limits=big, tx_en=True)
    for q in range(1, 10):  # 8 queued, the 9th reason_TX_BUSY
        await e.step(hw_order=hw(qty=q), ready=False)
    await e.idle(3, ready=False)
    await e.step(kill=True, ready=True)  # cancel_kill, handover_on_cancel: 7 held
    e.host.append(order())
    await e.step(ready=False)  # host_paused_by_drain
    for k in range(6):  # each decision's reject preempts the drain: drain_preempted
        await e.step(hw_order=hw(), ready=False, kill=False if k == 1 else None)
    await e.idle(3, ready=False)
    await e.drain()
    for q in range(1, 9):
        await e.step(hw_order=hw(qty=q), ready=False)
    await e.idle(3, ready=False)
    await e.step(kill=True, tx_en=False, ready=False)  # cancel_both: 8 held
    for _ in range(5):  # released at once; accepts from the 4th step on, while the held ones are written out
        await e.step(hw_order=hw(), kill=False, tx_en=True, ready=False)
    await e.step(tx_en=False, ready=False)  # cancel_tx_off of live orders behind held ones
    await e.step(userref=5, ready=False, kill=True)  # userref_ignored_queue (the cancelled still held)
    await e.drain()
    await e.step(tx_en=True)
    await e.step(hw_order=hw(), sym=(42 >> 5, 0, 0xF))  # lookup_same_cycle_write (sees the old bit)
    await e.step(sym=(42 >> 5, 1 << (42 & 31), 0xF))
    await e.idle(4)
    await e.step(userref=USER_REF_MAX)  # userref_ok: one number left
    await e.step(kill=False)
    await e.step(hw_order=hw())
    await e.step(hw_order=hw())  # refs_exhausted
    await e.drain()
    await e.step(kill=True)
    await e.idle(3)
    await e.step(userref=1)
    await e.step(kill=False)
    await e.drain()
    e.ret_p = 0.0
    for _ in range(18):  # accept_last_credit, reason_RING_FULL, rej_dropped
        await e.step(hw_order=hw())
    await e.drain()


# ---------------------------------------------------------------- pytest

LONG_TESTS = ("tc_random_golden",)


def directed_tests() -> list[str]:
    return [name for name in globals() if name.startswith("tc_") and name not in LONG_TESTS]


@pytest.mark.parametrize("group", ["directed", *LONG_TESTS])
def test_risk_check(group):
    run_cocotb(
        toplevel="risk_check",
        filelists=["common.f", "core.f"],
        test_module="test_risk_check",
        testcase=directed_tests() if group == "directed" else group,
        extra_env={"GROUP": group},
    )
