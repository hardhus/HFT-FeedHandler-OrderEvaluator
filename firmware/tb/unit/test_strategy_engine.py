"""strategy_engine against the strategy model (tb/model/strategy.py; audit/14 §2.3, §2.4, §2.10).

Cycles: inputs driven after edge d are sampled at edge d + 1. In one input cycle the model takes, in this order,
the packet event, the message, then the commands (commit, disarm, arm), as the module header states. The monitor
checks every cycle: an accepted packet's orders appear from output cycle d + 2 on, one per cycle, in fire order,
with every field; st_armed and st_error after edge d + 1 equal the model's state after input cycle d. The
directed tests check hand-written values on top of that.
"""

import os
import random
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cocotb
import pytest
from cocotb.triggers import ReadOnly, RisingEdge

from common.clocks import start_clock
from common.coverage import Coverage
from common.runner import run_cocotb
from model.itch import Bitmap, Event, ItchConfig, encode, expect, wire
from model.mold import ACCEPTED, MoldStatus
from model.order import PRICE_LIMIT_MAX, OrderReq
from model.strategy import (
    N_RULES,
    ActionSide,
    PriceMode,
    PriceOp,
    QtyMode,
    Rule,
    Strategy,
    matches,
    static_error,
)

PERIOD_PS = 6400
N_RANDOM = 100_000  # messages of tc_random_golden
S = MoldStatus
REJECTED = [s for s in S if s not in ACCEPTED]
EPOCH_W = 8
ALL = (1 << N_RULES) - 1

# strat_rule_t, MSB first (hft_pkg.sv): name, width.
RULE_FIELDS = (("enable", 1), ("msg_mask", 3), ("locate", 16), ("side_match", 2), ("price_ge", 1),
               ("price_threshold", 32), ("min_shares", 32), ("action_side", 2), ("qty_min", 1), ("qty", 32),
               ("price_threshold_mode", 1), ("price_offset", 32), ("tif", 8))  # fmt: skip
RULE_W = sum(w for _, w in RULE_FIELDS)
EV_GARBAGE = {"class": 3, "type": 8, "seq": 64, "sof_ts": 64, "epoch": EPOCH_W, "locate": 16, "shares": 64,
              "price": 32, "side": 8, "stock": 64}  # fmt: skip


def pack_rule(r: Rule) -> int:
    vals = {
        "enable": int(r.enable), "msg_mask": r.msg_mask, "locate": r.locate, "side_match": r.side_match,
        "price_ge": int(r.price_op == PriceOp.GE), "price_threshold": r.price_threshold, "min_shares": r.min_shares,
        "action_side": int(r.action_side), "qty_min": int(r.qty_mode == QtyMode.MIN), "qty": r.qty,
        "price_threshold_mode": int(r.price_mode == PriceMode.THRESHOLD), "price_offset": r.price_offset & 0xFFFFFFFF,
        "tif": r.tif,
    }  # fmt: skip
    out = 0
    for name, w in RULE_FIELDS:
        assert 0 <= vals[name] < 1 << w, name
        out = out << w | vals[name]
    return out


def ev(typ="A", locate=42, side="B", shares=500, price=1_000_000, stock="MSFT    ", extra=b"") -> Event:
    fields = {"A": {"order_ref": 1, "side": side, "shares": shares, "stock": stock, "price": price},
              "F": {"order_ref": 1, "side": side, "shares": shares, "stock": stock, "price": price,
                    "attribution": "MPID"},
              "P": {"order_ref": 1, "side": side, "shares": shares, "stock": stock, "price": price, "match": 9},
              "E": {"order_ref": 1, "executed_shares": shares, "match": 9},
              "U": {"orig_ref": 1, "new_ref": 2, "shares": shares, "price": price},
              "C": {"order_ref": 1, "executed_shares": shares, "match": 9, "printable": "Y", "exec_price": price},
              "Q": {"shares": shares, "stock": stock, "cross_price": price, "match": 9, "cross_type": "O"},
              "S": {"event_code": "O"}}[typ]  # fmt: skip
    msg = encode(typ, locate=locate if typ != "S" else 0, **fields) + extra
    return expect(msg, ItchConfig(), Bitmap())[0]


BASE = Rule(
    enable=True, msg_mask=0b111, locate=42, side_match=0b11, price_op=PriceOp.LE, price_threshold=1_000_000,
    min_shares=500, action_side=ActionSide.SELL, qty_mode=QtyMode.FIXED, qty=100, tif=ord("3"),
)  # fmt: skip


def rule(**kw) -> Rule:
    return Rule(**{**BASE.__dict__, **kw})


@dataclass
class Msg:
    ev: Event
    seq: int = field(default_factory=lambda: random.getrandbits(64))
    sof_ts: int = field(default_factory=lambda: random.getrandbits(64))
    epoch: int = field(default_factory=lambda: random.getrandbits(EPOCH_W))


BINS = [
    *[f"fire_rule_{i}" for i in range(N_RULES)],
    "multi_hit", "fired_rule_matches_again", "reject_with_orders", "accept_with_orders", "accept_without_orders",
    "cmd_after_fire_arm", "cmd_after_fire_disarm", "cmd_after_fire_commit", "fire_with_pkt_same_cycle",
    "action_buy", "action_sell", "action_opposite", "qty_min_shares", "qty_min_cap", "price_threshold_mode",
    "fire_type_A", "fire_type_F", "fire_type_P", "other_type_on_rule_locate", "malformed_a",
    "static_err_locate", "static_err_tif", "static_err_action", "static_err_qty0", "static_err_qty_big",
    "static_err_price", "queue_over_8", "commit_and_arm_same_cycle", "arm_and_disarm_same_cycle",
    "command_blocks_message", "arm_on_fire_cycle", "arm_on_accept_cycle",
]  # fmt: skip


class Engine:
    """Driver, cycle model and monitor."""

    def __init__(self, dut, cov: Coverage | None = None) -> None:
        self.dut, self.cov = dut, cov
        self.model = Strategy()
        self.d = 0  # driver edges
        self.m = 0  # monitor edges
        self.out: deque[tuple[int, OrderReq]] = deque()  # (first output cycle, order)
        self.state: dict[int, tuple[int, int]] = {}  # monitor cycle -> (st_armed, st_error)
        self.got: list[tuple[int, OrderReq]] = []  # (output cycle, order) seen
        self.fired_this_pkt: set[int] = set()
        self.paused = False

    def hit(self, name: str) -> None:
        if self.cov is not None:
            self.cov.hit(name)

    def idle_inputs(self) -> None:
        d = self.dut
        d.s_ev_valid.value = 0
        d.s_pkt_valid.value = 0
        d.cfg_rule_wr.value = 0
        d.cmd_disarm.value = 0
        d.cmd_arm.value = 0
        for k, w in EV_GARBAGE.items():
            getattr(d, f"s_ev_{k}").value = random.getrandbits(w)
        d.s_pkt_status.value = random.getrandbits(4)
        d.cfg_rule_idx.value = random.getrandbits(3)
        d.cfg_rule.value = random.getrandbits(RULE_W)
        d.cmd_disarm_mask.value = random.getrandbits(N_RULES)
        d.cmd_arm_mask.value = random.getrandbits(N_RULES)

    async def step(self, msg: Msg | None = None, pkt: S | None = None, commit: tuple[int, Rule] | None = None,
                   disarm: int | None = None, arm: int | None = None) -> int | None:  # fmt: skip
        """One input cycle; returns the rule that fired on msg, if any. The model's tick() holds the order of the
        cycle and its ties (decision 19)."""
        d, s = self.dut, self.model
        self.idle_inputs()
        accepted = None
        if pkt is not None:
            d.s_pkt_valid.value = 1
            d.s_pkt_status.value = int(pkt)
            accepted = pkt in ACCEPTED
            if s.pending:
                self.hit("accept_with_orders" if accepted else "reject_with_orders")
            elif accepted:
                self.hit("accept_without_orders")
            if accepted and arm is not None and arm & s.disarm_on_accept:
                self.hit("arm_on_accept_cycle")
        block = (disarm or 0) | (1 << commit[0] if commit else 0)
        if msg is not None:
            e = msg.ev
            d.s_ev_valid.value = 1
            d.s_ev_class.value = int(e.cls)
            d.s_ev_type.value = e.type
            d.s_ev_seq.value = msg.seq
            d.s_ev_sof_ts.value = msg.sof_ts
            d.s_ev_epoch.value = msg.epoch
            d.s_ev_locate.value = e.locate
            d.s_ev_shares.value = e.shares
            d.s_ev_price.value = e.price
            d.s_ev_side.value = e.side
            d.s_ev_stock.value = e.stock
            self._cover_message(e, block)
        if commit is not None:
            d.cfg_rule_wr.value = 1
            d.cfg_rule_idx.value = commit[0]
            d.cfg_rule.value = pack_rule(commit[1])
            if 1 << commit[0] & s.disarm_on_accept:
                self.hit("cmd_after_fire_commit")
            self._cover_static(commit[1])
            if arm is not None and arm >> commit[0] & 1:
                self.hit("commit_and_arm_same_cycle")
        if disarm is not None:
            d.cmd_disarm.value = 1
            d.cmd_disarm_mask.value = disarm
            if disarm & s.disarm_on_accept:
                self.hit("cmd_after_fire_disarm")
            if arm is not None and arm & disarm:
                self.hit("arm_and_disarm_same_cycle")
        if arm is not None:
            d.cmd_arm.value = 1
            d.cmd_arm_mask.value = arm
            if arm & s.disarm_on_accept and not accepted:
                self.hit("cmd_after_fire_arm")
        released, fired = s.tick(
            accepted, (msg.ev, msg.seq, msg.sof_ts, msg.epoch) if msg else None, commit, disarm or 0, arm or 0
        )
        for o in released:
            self.out.append((self.d + 2, o))
        if fired is not None:
            self._cover_fire(fired, msg.ev, pkt is not None, arm)
        self.state[self.d + 1] = (s.armed, s.error_bits())
        if len(self.out) + len(s.pending) > 8:
            self.hit("queue_over_8")
        await RisingEdge(d.clk)
        self.d += 1
        return fired

    def _cover_message(self, e: Event, block: int) -> None:
        s = self.model
        live = [i for i, r in enumerate(s.rules)
                if r.enable and s.armed >> i & 1 and not static_error(r) and matches(r, e)]  # fmt: skip
        if any(block >> i & 1 and not s.fired_now >> i & 1 for i in live):
            self.hit("command_blocks_message")
        if len([i for i in live if not s.fired_now >> i & 1]) > 1:
            self.hit("multi_hit")
        if any(s.fired_now >> i & 1 for i in live):
            self.hit("fired_rule_matches_again")
        if e.type in b"AFP" and e.cls.name == "MALFORMED":
            self.hit("malformed_a")
        if e.type not in b"AFP" and any(r.locate == e.locate and r.enable for r in s.rules) and s.armed:
            self.hit("other_type_on_rule_locate")

    def _cover_fire(self, i: int, e: Event, with_pkt: bool, arm: int | None) -> None:
        r = self.model.rules[i]
        if arm is not None and arm >> i & 1:
            self.hit("arm_on_fire_cycle")
        self.hit(f"fire_rule_{i}")
        self.hit(f"fire_type_{chr(e.type)}")
        self.hit({ActionSide.BUY: "action_buy", ActionSide.SELL: "action_sell"}.get(r.action_side, "action_opposite"))
        if r.qty_mode == QtyMode.MIN:
            self.hit("qty_min_shares" if e.shares < r.qty else "qty_min_cap")
        if r.price_mode == PriceMode.THRESHOLD:
            self.hit("price_threshold_mode")
        if with_pkt:
            self.hit("fire_with_pkt_same_cycle")

    def _cover_static(self, r: Rule) -> None:
        if r.locate == 0:
            self.hit("static_err_locate")
        if r.tif not in b"035E":
            self.hit("static_err_tif")
        if r.action_side == 3:
            self.hit("static_err_action")
        if r.qty == 0:
            self.hit("static_err_qty0")
        if r.qty_mode == QtyMode.FIXED and r.qty >= 1_000_000:
            self.hit("static_err_qty_big")
        if r.price_mode == PriceMode.THRESHOLD and not 1 <= r.price_threshold + r.price_offset <= PRICE_LIMIT_MAX:
            self.hit("static_err_price")

    async def idle(self, n: int = 1) -> None:
        for _ in range(n):
            await self.step()

    async def drain(self) -> None:
        while self.out or self.model.pending:
            await self.step()
        await self.idle(3)

    async def monitor(self) -> None:
        d = self.dut
        while True:
            await RisingEdge(d.clk)
            self.m += 1
            await ReadOnly()
            if not self.paused:
                self._check()

    def _check(self) -> None:
        d, m = self.dut, self.m
        if m in self.state:
            want = self.state.pop(m)
            got = (int(d.st_armed.value), int(d.st_error.value))
            assert got == want, f"cycle {m}: (st_armed, st_error) {got}, model {want}"
        valid = bool(d.m_ord_valid.value)
        want_o = self.out[0][1] if self.out and self.out[0][0] <= m else None
        assert valid == (want_o is not None), f"cycle {m}: m_ord_valid {valid}, model {want_o}"
        if not valid:
            return
        self.out.popleft()
        got_o = (int(d.m_ord_rule.value), int(d.m_ord_side.value), int(d.m_ord_qty.value),
                 int(d.m_ord_price.value), int(d.m_ord_locate.value), int(d.m_ord_stock.value),
                 int(d.m_ord_tif.value), int(d.m_ord_seq.value), int(d.m_ord_sof_ts.value),
                 int(d.m_ord_epoch.value))  # fmt: skip
        w = want_o
        want_t = (w.rule, w.side, w.qty, w.price % (1 << 32), w.locate, wire(w.stock), w.tif, w.seq, w.sof_ts,
                  w.epoch)  # fmt: skip
        assert got_o == want_t, f"cycle {m}: order {got_o}, model {want_t}"
        self.got.append((m, w))

    async def reset(self, cycles: int = 3) -> None:
        self.paused = True
        self.dut.rst.value = 1
        for _ in range(cycles):
            await self.step()
        self.dut.rst.value = 0
        self.model = Strategy()
        self.out.clear()
        self.state.clear()
        self.fired_this_pkt.clear()
        await self.step()
        self.paused = False


async def start(dut, cov: Coverage | None = None) -> Engine:
    e = Engine(dut, cov)
    e.idle_inputs()
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    e.idle_inputs()
    await RisingEdge(dut.clk)
    e.d = e.m = 0
    cocotb.start_soon(e.monitor())
    return e


async def program(e: Engine, *rules: Rule, arm: int = ALL) -> None:
    for i, r in enumerate(rules):
        await e.step(commit=(i, r))
    await e.step(arm=arm)


# ---------------------------------------------------------------- directed


@cocotb.test()
async def tc_reset_state(dut):
    """After reset: every rule disarmed and flagged (locate 0), no order."""
    e = await start(dut)
    await e.idle(2)
    assert (int(dut.st_armed.value), int(dut.st_error.value), int(dut.m_ord_valid.value)) == (0, 0xFF, 0)
    await e.step(arm=ALL)
    await e.step(Msg(ev()))
    await e.step(pkt=S.OK)
    await e.drain()
    assert e.got == [] and int(dut.st_armed.value) == 0xFF


@cocotb.test()
async def tc_latency_and_fields(dut):
    """Hand values: the order appears exactly 2 cycles after the accepted packet event."""
    e = await start(dut)
    await program(e, rule(action_side=ActionSide.OPPOSITE, qty_mode=QtyMode.MIN, qty=300, min_shares=0))
    m = Msg(ev(side="S", shares=250, price=999_900, stock="ZVZZT   "), seq=0x0102030405060708,
            sof_ts=0x1112131415161718, epoch=0x5A)  # fmt: skip
    assert await e.step(m) == 0
    await e.idle(3)
    pkt_cycle = e.d + 1  # the edge that samples the packet event
    await e.step(pkt=S.OK)
    await e.drain()
    ((cyc, o),) = e.got
    assert cyc == pkt_cycle + 1  # output after the second edge: 2 cycles
    assert (o.rule, chr(o.side), o.qty, o.price, o.locate, o.stock, chr(o.tif)) == (
        0, "B", 250, 999_900, 42, b"ZVZZT   ", "3",
    )  # fmt: skip
    assert (o.seq, o.sof_ts, o.epoch) == (0x0102030405060708, 0x1112131415161718, 0x5A)


@cocotb.test()
async def tc_order_modes(dut):
    """Hand values for each side, quantity and price mode."""
    e = await start(dut)
    rules = [
        rule(action_side=ActionSide.BUY, qty=7),
        rule(action_side=ActionSide.SELL, qty_mode=QtyMode.MIN, qty=400),
        rule(action_side=ActionSide.OPPOSITE, price_mode=PriceMode.THRESHOLD, price_offset=-2500),
        rule(action_side=ActionSide.OPPOSITE, price_mode=PriceMode.THRESHOLD, price_threshold=PRICE_LIMIT_MAX - 7,
             price_offset=7, price_op=PriceOp.GE),  # fmt: skip
    ]
    await program(e, *rules)
    await e.step(Msg(ev(side="S", shares=500, price=900_000)))  # rule 0
    await e.step(Msg(ev(side="S", shares=500, price=900_000)))  # rule 1: min(500, 400)
    await e.step(Msg(ev(side="S", shares=500, price=900_000)))  # rule 2: threshold - 2500, opposite of S
    await e.step(Msg(ev(side="B", shares=999, price=PRICE_LIMIT_MAX)))  # rule 3: GE, the largest price
    await e.step(pkt=S.HEARTBEAT)
    await e.drain()
    got = [(o.rule, chr(o.side), o.qty, o.price) for _, o in e.got]
    assert got == [(0, "B", 7, 900_000), (1, "S", 400, 900_000), (2, "B", 100, 997_500), (3, "S", 100, PRICE_LIMIT_MAX)]


@cocotb.test()
async def tc_match_conditions(dut):
    """Each condition of a match, broken alone: no fire; then the base message fires."""
    e = await start(dut)
    await program(e, rule())
    misses = [ev(locate=43), ev(shares=499), ev(price=1_000_001), ev(side="X"), ev(typ="E"), ev(typ="U"),
              ev(typ="C"), ev(typ="Q"), ev(extra=b"\x00")]  # fmt: skip
    for m in misses:
        assert await e.step(Msg(m)) is None
    for typ in "AFP":
        await e.step(commit=(0, rule()))
        await e.step(arm=1)
        assert await e.step(Msg(ev(typ=typ))) == 0
        await e.step(pkt=S.OK)
    await e.drain()
    assert len(e.got) == 3
    await e.step(commit=(0, rule(msg_mask=0b110)))
    await e.step(arm=1)
    assert await e.step(Msg(ev(typ="A"))) is None
    await e.step(commit=(0, rule(side_match=0b10)))
    await e.step(arm=1)
    assert await e.step(Msg(ev(side="B"))) is None
    assert await e.step(Msg(ev(side="S"))) == 0
    await e.step(pkt=S.OK)
    await e.drain()


@cocotb.test()
async def tc_priority_and_one_shot(dut):
    """The lowest index fires; a fired rule passes the next message to the next rule; one shot per packet."""
    e = await start(dut)
    await program(e, rule(qty=1), rule(qty=2), rule(qty=3))
    assert [await e.step(Msg(ev())) for _ in range(4)] == [0, 1, 2, None]
    await e.step(pkt=S.OK)
    await e.drain()
    assert [o.qty for _, o in e.got] == [1, 2, 3]
    assert int(dut.st_armed.value) == 0xF8
    assert await e.step(Msg(ev())) is None


@cocotb.test()
async def tc_rejected_packet(dut):
    """Every rejected status drops the orders and keeps the rules armed."""
    e = await start(dut)
    await program(e, rule())
    for st in REJECTED:
        assert await e.step(Msg(ev())) == 0
        await e.step(pkt=st)
    await e.drain()
    assert e.got == [] and int(dut.st_armed.value) & 1


@cocotb.test()
async def tc_commands_after_the_fire(dut):
    """Decisions 14 and 18: the order leaves; a host command after the fire beats the acceptance disarm."""
    e = await start(dut)
    await program(e, rule(qty=1), rule(qty=2), rule(qty=3))
    for _ in range(3):
        await e.step(Msg(ev()))
    await e.step(commit=(0, rule(qty=9)))
    await e.step(arm=0b001)
    await e.step(disarm=0b010)
    await e.step(pkt=S.OK)
    await e.drain()
    assert [o.qty for _, o in e.got] == [1, 2, 3]
    assert int(dut.st_armed.value) & 0b111 == 0b001  # rule 0 re-armed; 1 disarmed; 2 one-shot
    assert await e.step(Msg(ev())) == 0  # the new rule 0
    await e.step(pkt=S.EOS)
    await e.drain()
    assert e.got[-1][1].qty == 9


@cocotb.test()
async def tc_same_cycle(dut):
    """A message in the cycle of a packet event belongs to the next packet. Ties in one cycle go to the
    disarming side (decision 19), with hand-written expectations."""
    e = await start(dut)
    await program(e, rule(qty=1), rule(qty=2))
    assert await e.step(Msg(ev())) == 0
    # Packet 1 is accepted first, which disarms rule 0; the message is packet 2's, so rule 1 takes it.
    assert await e.step(Msg(ev()), pkt=S.OK) == 1
    await e.step(pkt=S.RUNT)  # packet 2 is rejected: rule 1's order is dropped and rule 1 stays armed
    await e.drain()
    assert [o.qty for _, o in e.got] == [1]
    assert int(dut.st_armed.value) & 0b11 == 0b10
    # An arm in the cycle of the fire is not after the fire: the acceptance disarms rule 1.
    assert await e.step(Msg(ev()), arm=0b10) == 1
    await e.step(pkt=S.OK)
    await e.drain()
    assert [o.qty for _, o in e.got] == [1, 2]
    assert int(dut.st_armed.value) & 0b11 == 0b00
    # An arm in the cycle of the acceptance loses to its disarm.
    await e.step(arm=0b01)
    assert await e.step(Msg(ev())) == 0
    await e.step(pkt=S.OK, arm=0b01)
    await e.drain()
    assert int(dut.st_armed.value) & 0b01 == 0
    # Commit and arm, disarm and arm of one rule in one cycle: disarmed.
    await e.step(commit=(0, rule(qty=5)), arm=0b01)
    await e.step(arm=0b10)
    await e.step(disarm=0b10, arm=0b10)
    await e.idle(2)
    assert int(dut.st_armed.value) & 0b11 == 0b00
    # A disarm or a commit in the cycle of a message keeps that rule from firing on it; an arm does not fire it.
    await e.step(arm=0b11)
    assert await e.step(Msg(ev()), disarm=0b01) == 1
    await e.step(pkt=S.OK)
    await e.step(arm=0b11)
    assert await e.step(Msg(ev()), commit=(0, rule(qty=6))) == 1
    await e.step(pkt=S.OK)
    await e.step(disarm=0b11)
    assert await e.step(Msg(ev()), arm=0b01) is None
    assert await e.step(Msg(ev())) == 0
    await e.step(pkt=S.OK)
    await e.drain()
    assert [o.qty for _, o in e.got] == [1, 2, 1, 2, 2, 6]


@cocotb.test()
async def tc_static_errors(dut):
    """Every static error alone: its st_error bit is set and the rule never fires."""
    e = await start(dut)
    bad = [
        rule(locate=0), rule(tif=ord("6")), rule(action_side=3), rule(qty=0),
        rule(qty=1_000_000), rule(price_mode=PriceMode.THRESHOLD, price_threshold=10, price_offset=-10),
        rule(price_mode=PriceMode.THRESHOLD, price_threshold=PRICE_LIMIT_MAX, price_offset=1),
        rule(price_mode=PriceMode.THRESHOLD, price_threshold=0xFFFFFFFF, price_offset=0x7FFFFFFF),
    ]  # fmt: skip
    await program(e, *bad)
    await e.idle(2)
    assert int(dut.st_error.value) == 0xFF
    assert await e.step(Msg(ev(locate=0))) is None
    assert await e.step(Msg(ev())) is None
    await e.step(pkt=S.OK)
    for i, r in enumerate([rule(), rule(qty=999_999), rule(qty_mode=QtyMode.MIN, qty=1_000_000),
                           rule(price_mode=PriceMode.THRESHOLD, price_threshold=10, price_offset=-9)]):  # fmt: skip
        await e.step(commit=(i, r))
    await e.idle(2)
    assert int(dut.st_error.value) == 0xF0
    await e.drain()
    assert e.got == []


@cocotb.test()
async def tc_queue_full_load(dut):
    """Eight rules fire in every packet, packets back to back: 16 orders queued at most, none lost."""
    e = await start(dut)
    await program(e, *[rule(qty=i + 1) for i in range(N_RULES)])
    for p in range(20):
        for i in range(N_RULES):
            await e.step(Msg(ev()), pkt=S.OK if i == 0 and p else None)
        await e.step(arm=ALL)
    await e.step(pkt=S.OK)
    await e.drain()
    assert [o.qty for _, o in e.got] == list(range(1, 9)) * 20


@cocotb.test()
async def tc_reset_in_a_packet(dut):
    """A reset drops the fired orders and the queue; the rules must be written again."""
    e = await start(dut)
    await program(e, rule())
    await e.step(Msg(ev()))
    await e.reset()
    await e.step(pkt=S.OK)
    await e.drain()
    assert e.got == [] and int(dut.st_error.value) == 0xFF and int(dut.st_armed.value) == 0


# ---------------------------------------------------------------- random

LOCATES = (1, 42, 0xFFFF)
PRICES = (1, 999_999, 1_000_000, 1_000_001, PRICE_LIMIT_MAX, 0xFFFFFFFF)


def random_rule() -> Rule:
    r = Rule(
        enable=random.random() < 0.9, msg_mask=random.randrange(8), locate=random.choice(LOCATES),
        side_match=random.randrange(4), price_op=random.choice(list(PriceOp)), price_threshold=random.choice(PRICES),
        min_shares=random.choice((0, 1, 500, 501)), action_side=random.choice((0, 1, 2, 2)),
        qty_mode=random.choice(list(QtyMode)), qty=random.choice((1, 400, 500, 999_999)),
        price_mode=random.choice(list(PriceMode)), price_offset=random.choice((0, -1, 1, -1_000_000, 2500)),
        tif=random.choice(b"035E"),
    )  # fmt: skip
    if random.random() < 0.05:
        r = Rule(**{**r.__dict__, **random.choice([{"locate": 0}, {"tif": ord("6")}, {"action_side": 3},
                                                   {"qty": 0}, {"qty": 1_000_000, "qty_mode": QtyMode.FIXED},
                                                   {"price_mode": PriceMode.THRESHOLD, "price_threshold": 1,
                                                    "price_offset": -1}])})  # fmt: skip
    return r


def random_msg() -> Msg:
    typ = random.choice("AAAFFPPEUCQS")
    e = ev(typ=typ, locate=random.choice(LOCATES), side=random.choice("BBSSX"),
           shares=random.choice((0, 400, 499, 500, 501, 1 << 31)), price=random.choice(PRICES),
           extra=b"\x00" if random.random() < 0.02 else b"")  # fmt: skip
    return Msg(e)


async def random_packet(e: Engine) -> None:
    for _ in range(random.randint(0, 10)):
        kw = {}
        if random.random() < 0.08:
            kw["commit"] = (random.randrange(N_RULES), random_rule())
        if random.random() < 0.08:
            kw["arm"] = random.getrandbits(N_RULES)
        if random.random() < 0.04:
            kw["disarm"] = random.getrandbits(N_RULES)
        await e.step(random_msg() if random.random() < 0.85 else None, **kw)
    status = random.choice([S.OK] * 6 + [S.HEARTBEAT, S.EOS] + REJECTED)
    msg = random_msg() if random.random() < 0.2 else None  # the next packet's first message
    await e.step(msg, pkt=status, arm=random.getrandbits(N_RULES) if random.random() < 0.5 else None)


@cocotb.test()
async def tc_random_golden(dut):
    """N_RANDOM cycles of random messages, rule changes, arms and disarms against the model, cycle by cycle, then
    a fixed sequence that hits every bin by construction, so coverage does not depend on the seed."""
    cov = Coverage("strategy_engine", BINS)
    e = await start(dut, cov)
    for i in range(N_RULES):
        await e.step(commit=(i, random_rule()))
    while e.d < N_RANDOM:
        await random_packet(e)
    await e.drain()
    await every_bin(e)
    await e.drain()
    dut._log.info(f"{len(e.got)} orders")
    cov.check(Path.cwd())


async def every_bin(e: Engine) -> None:
    """Hits every bin of BINS whatever came before (the same per-cycle checks run on it)."""
    await e.step(pkt=S.OK)  # close whatever packet is open
    rules = [rule(action_side=ActionSide.BUY, min_shares=0), rule(qty_mode=QtyMode.MIN, qty=400, min_shares=0),
             rule(action_side=ActionSide.OPPOSITE, qty_mode=QtyMode.MIN, qty=600, min_shares=0),
             rule(price_mode=PriceMode.THRESHOLD, price_offset=-1, min_shares=0),
             *[rule(min_shares=0, qty=10 + i) for i in range(4, N_RULES)]]  # fmt: skip
    await program(e, *rules)
    for typ in "AFPAFPAF":  # rules 0..7 fire, the first ones with several hits
        assert await e.step(Msg(ev(typ=typ))) is not None
    await e.step(Msg(ev()))  # every rule has fired: fired_rule_matches_again
    await e.step(Msg(ev(typ="E")))  # other_type_on_rule_locate
    await e.step(Msg(ev(extra=b"\x00")))  # malformed_a
    await e.step(commit=(7, rules[7]))  # cmd_after_fire_commit
    await e.step(disarm=0b0100_0000)  # cmd_after_fire_disarm
    await e.step(arm=ALL)  # cmd_after_fire_arm: no rule is disarmed by the acceptance
    assert await e.step(Msg(ev()), pkt=S.OK) == 0  # accept_with_orders, fire_with_pkt_same_cycle, queue_over_8
    await e.step(pkt=S.RUNT)  # reject_with_orders
    await e.step(pkt=S.OK)  # accept_without_orders
    await program(e, *rules)
    await e.step(Msg(ev()), arm=0b1)  # arm_on_fire_cycle (rule 0 fires)
    await e.step(Msg(ev()), disarm=0b10)  # command_blocks_message (rule 1 is blocked, rule 2 fires)
    await e.step(pkt=S.OK, arm=0b1)  # arm_on_accept_cycle
    await e.step(commit=(0, rules[0]), arm=0b1)  # commit_and_arm_same_cycle
    await e.step(disarm=0b10, arm=0b10)  # arm_and_disarm_same_cycle
    for bad in ({"locate": 0}, {"tif": ord("6")}, {"action_side": 3}, {"qty": 0}, {"qty": 1_000_000},
                {"price_mode": PriceMode.THRESHOLD, "price_threshold": 1, "price_offset": -1}):  # fmt: skip
        await e.step(commit=(5, rule(**bad)))


# ---------------------------------------------------------------- pytest

LONG_TESTS = ("tc_random_golden",)


def directed_tests() -> list[str]:
    return [name for name in globals() if name.startswith("tc_") and name not in LONG_TESTS]


@pytest.mark.parametrize("group", ["directed", *LONG_TESTS])
def test_strategy_engine(group):
    run_cocotb(
        toplevel="strategy_engine",
        filelists=["common.f", "core.f"],
        test_module="test_strategy_engine",
        testcase=directed_tests() if group == "directed" else group,
        extra_env={"GROUP": group},
    )
