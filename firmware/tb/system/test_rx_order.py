"""rx_csr_top: the order path in the system (audit/14 S8): itch_decoder -> strategy_engine -> risk_check (with the
host mailbox) -> order_tx_builder -> the TX port, with ORDER_SENT and RISK_REJECT in the host ring.

Every block was checked cycle by cycle against its model in its unit test. Here the chain is checked end to end,
as software and the exchange see it:
- The exchange (common/exchange.py) receives every frame on m_axis_tx_* (with tready pressure when a test asks):
  headers equal to the template, OUCH fields, UserRefNum exactly +1 each frame. Each frame must be the model's
  order (strategy model: make_order from the trigger message as the ITCH chain model saw it; host: the mailbox
  order) with the model's ClOrdID.
- OrderWatch records, in one coroutine and one cycle count, the trigger frame's beats, the forwarded packet event,
  strategy_engine's released orders, risk_check's handovers and rejects, the TX beats, and event_packer's order
  inputs (ORDER_SENT, RISK_REJECT, lost rejects). The ring's order entries must be exactly those inputs, in the
  same order, encoded by the ring model (model/ring.py order_entry), each between two packets' entries.
- Every snapshot holds the counters at its acceptance edge (common/csr_env.Snap, fed by hft_csr_wrap's order
  inputs) and the ring identity of decision 30 (test_rx_csr.check_itch_identities).
- Latency (P10): from the trigger packet's last beat to the order's first TX beat, hop by hop, exact cycles.

The host follows the software contract after reset (decision 5): limits, LOCATE_ALLOW, budget, rules and arm, TX
template and TX_COMMIT, TX_USERREF_NEXT, tx_en, and kill released last.
"""

import json
import os
import random
from dataclasses import dataclass, field, replace
from pathlib import Path

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
from cocotb.utils import get_sim_time
from test_rx_csr import (
    FEED_ON,
    ITCH_LATENCY,
    NET_FEED,
    PKT_LATENCY,
    SESSION,
    RingHost,
    System,
    bit,
    configure,
    configure_feed,
    feed_frame,
    named,
    order,
    ring_system,
    send_feed,
    snapshot_checked,
    start,
)

from common.clocks import PROFILES
from common.csr_env import (
    COMMANDS,
    LOCATE_ALLOW,
    MBOX_SEND,
    RISK_CTRL,
    SYMBOL,
    TX_CTRL,
    LimCfg,
    MboxOrder,
    RuleCfg,
    TxCfg,
)
from common.exchange import Exchange
from common.itch_real import Retired, fetch, load, packets
from common.runner import run_cocotb
from model import mold
from model.csr import OKAY, addr
from model.desc_gen import FLAG_OVERFLOW_BEFORE, Kind, decode
from model.itch import N_WORDS, Event, ItchClass, encode
from model.mold import ACCEPTED
from model.order import FRAME_LEN, OrderReq, Source, TxConfig, frame, ouch_of, template, valid_order
from model.ring import RingOrder, order_entry, stamp
from model.risk import Reason
from model.strategy import PriceMode, PriceOp, QtyMode, Rule, Strategy, make_order

RUNTIME_PARAMS = ("ratio", "group")
STRAT_LATENCY = 2  # strategy_engine: packet event -> its first released order (module header)
RISK_LATENCY = 3  # risk_check: an order taken at edge k is on m_tx_* after edge k+3 (module header)
TX_BEATS = 12  # order_tx_builder: a 92-byte frame in 64-bit beats
TXC = TxCfg(template(), ord("Y"), ord("P"), ord("N"), ord("N"))
TX_MODEL = TxConfig(TXC.template, TXC.display, TXC.capacity, TXC.ise, TXC.cross_type)
OPEN = LimCfg(max_qty=999_999, max_notional=(1 << 64) - 1, refill=1, period=1, burst=1000)
LOC = 5  # the trigger messages' stock locate (test_rx_csr.order)


def model_rule(c: RuleCfg) -> Rule:
    """The strategy model's rule for what software wrote."""
    offset = c.offset - (1 << 32) if c.offset >> 31 else c.offset
    return Rule(bool(c.enable), c.msg_mask, c.locate, c.side_match, PriceOp(c.price_ge), c.threshold, c.min_shares,
                c.action_side, QtyMode(c.qty_min), c.qty, PriceMode(c.price_mode), offset, c.tif)  # fmt: skip


# Buy 100 at the event price on every Add Order of LOC, Day.
BUY_ON_ADD = RuleCfg(enable=1, msg_mask=0b001, locate=LOC, side_match=0b11, price_ge=0, threshold=0xFFFF_FFFF,
                     min_shares=0, action_side=0, qty_min=0, qty=100, price_mode=0, offset=0, tif=ord("0"))  # fmt: skip


class OrderHost:
    """Software: the order registers through the CSR bus; every command answers once it is in effect."""

    def __init__(self, w) -> None:
        self.w = w

    async def wr(self, a: int, d: int) -> None:
        assert await self.w.drv.write(a, d) == OKAY, f"write {a:#06x}"

    async def rule(self, idx: int, r: RuleCfg) -> None:
        for a, d in [*r.writes(), (addr("STRATEGY.RULE_SEL"), idx)]:
            await self.wr(a, d)
        await self.wr(COMMANDS["RULE_COMMIT"], 1)

    async def arm(self, mask: int) -> None:
        await self.wr(COMMANDS["RULE_ARM"], mask)

    async def disarm(self, mask: int) -> None:
        await self.wr(COMMANDS["RULE_DISARM"], mask)

    async def limits(self, lim: LimCfg) -> None:
        for a, d in lim.writes():
            await self.wr(a, d)
        await self.wr(COMMANDS["RISK_COMMIT"], 1)

    async def budget(self, n: int) -> None:
        await self.wr(COMMANDS["BUDGET_ADD"], n)

    async def allow(self, *locates: int) -> None:
        words: dict[int, int] = {}
        for loc in locates:
            words[loc >> 5] = words.get(loc >> 5, 0) | 1 << (loc & 31)
        for word, bits in words.items():
            await self.wr(LOCATE_ALLOW.addr + 4 * word, bits)

    async def tx_config(self, cfg: TxCfg) -> None:
        for a, d in cfg.writes():
            await self.wr(a, d)
        await self.wr(COMMANDS["TX_COMMIT"], 1)

    async def userref(self, n: int) -> None:
        await self.wr(COMMANDS["USERREF"], n)

    async def tx_en(self, on: bool) -> None:
        await self.wr(TX_CTRL, int(on))

    async def kill(self, on: bool) -> None:
        await self.wr(RISK_CTRL, int(on))

    async def mbox(self, o: MboxOrder) -> None:
        for a, d in o.writes():
            await self.wr(a, d)
        await self.wr(MBOX_SEND, 1)

    async def bring_up(self, rules: dict[int, RuleCfg], lim: LimCfg = OPEN, budget: int = 1000,
                       allow: tuple[int, ...] = (LOC,), userref: int = 1) -> None:  # fmt: skip
        """The software contract after reset (decision 5), kill released last."""
        await self.limits(lim)
        await self.allow(*allow)
        await self.budget(budget)
        for idx, r in rules.items():
            await self.rule(idx, r)
        await self.arm(sum(1 << i for i in rules))
        await self.tx_config(TXC)
        await self.userref(userref)
        await self.tx_en(True)
        await self.kill(False)


def fields_req(d, prefix: str) -> OrderReq:
    """An order as a block port carries it (prefix: u_packer.s_sent_ or s_rej_)."""

    def v(name: str) -> int:
        return int(getattr(d, prefix + name).value)

    source = Source(v("source"))
    clordid = v("clordid").to_bytes(14, "little") if source == Source.HOST else None
    return OrderReq(source, v("side"), v("qty"), v("price"), v("locate"), v("stock").to_bytes(8, "little"), v("tif"),
                    rule=v("rule"), clordid=clordid, seq=v("seq"), sof_ts=v("sof_ts"), epoch=v("epoch"))  # fmt: skip


def ord_req(s) -> OrderReq:
    """strategy_engine's m_ord_* as an order."""
    return OrderReq(Source.RULE, int(s.m_ord_side.value), int(s.m_ord_qty.value), int(s.m_ord_price.value),
                    int(s.m_ord_locate.value), int(s.m_ord_stock.value).to_bytes(8, "little"), int(s.m_ord_tif.value),
                    rule=int(s.m_ord_rule.value), seq=int(s.m_ord_seq.value), sof_ts=int(s.m_ord_sof_ts.value),
                    epoch=int(s.m_ord_epoch.value))  # fmt: skip


@dataclass
class OrderWatch:
    """One coroutine, one cycle count (net edges): what the order path did and when. A value read after edge c is
    'after edge c'; a handshake seen after edge c is taken at edge c + 1."""

    dut: object
    cycle: int = 0
    p_ready: float = 1.0  # tready probability per cycle
    sof: list = field(default_factory=list)  # edges that take a frame's first beat on s_axis_rx
    tlast: list = field(default_factory=list)  # edges that take a frame's last beat on s_axis_rx
    udp_tlast: list = field(default_factory=list)  # edges at which moldudp64_rx takes a packet's last beat
    fwd: list = field(default_factory=list)  # (edge after which, status) of forwarded packet events
    released: list = field(default_factory=list)  # (edge after which, order) strategy_engine m_ord_*
    taken: list = field(default_factory=list)  # orders risk_check takes, in its order (hardware first)
    tx_valid: list = field(default_factory=list)  # edges after which risk_check m_tx_valid rises
    tx_first: list = field(default_factory=list)  # (edge after which the first beat is valid, edge taking it)
    entries: list = field(default_factory=list)  # event_packer order inputs, in its order: (kind, RingOrder)
    lost: list = field(default_factory=list)  # (edge, reason) of lost rejects
    frames: list = field(default_factory=list)  # whole frames taken on m_axis_tx_*
    hold: bool = False  # tready 0 while set
    next_ready: bool = True  # tready for the next edge, chosen after the previous one

    def __post_init__(self) -> None:
        cocotb.start_soon(self._ready())
        cocotb.start_soon(self._run())

    async def _ready(self) -> None:
        """Drives the tready that _run chose for the next edge (so _run knows which beat that edge takes)."""
        d = self.dut
        while True:
            await FallingEdge(d.net_clk)
            d.m_axis_tx_tready.value = int(self.next_ready)

    async def _run(self) -> None:
        d = self.dut
        rx_busy, tx_busy, buf, tv_prev = False, False, bytearray(), False
        first_valid = None
        while True:
            await RisingEdge(d.net_clk)
            await ReadOnly()
            self.cycle += 1
            c = self.cycle
            if d.s_axis_rx_tvalid.value:  # taken at edge c + 1
                if not rx_busy:
                    self.sof.append(c + 1)
                rx_busy = not d.s_axis_rx_tlast.value
                if not rx_busy:
                    self.tlast.append(c + 1)
            if d.m_axis_udp_tvalid.value and d.m_axis_udp_tlast.value:  # moldudp64_rx samples it at edge c + 1
                self.udp_tlast.append(c + 1)
            if d.m_fwd_pkt_valid.value:
                self.fwd.append((c, int(d.m_fwd_pkt_status.value)))
            s, r, p = d.u_strategy, d.u_risk, d.u_packer
            if s.m_ord_valid.value:
                self.released.append((c, ord_req(s)))
            if r.s_hw_valid.value:  # taken at edge c + 1, hardware first
                self.taken.append(ord_req(s))
            elif r.s_host_valid.value and r.s_host_ready.value:
                self.taken.append(OrderReq(
                    Source.HOST, int(r.s_host_side.value), int(r.s_host_qty.value), int(r.s_host_price.value),
                    int(r.s_host_locate.value), int(r.s_host_stock.value).to_bytes(8, "little"),
                    int(r.s_host_tif.value), clordid=int(r.s_host_clordid.value).to_bytes(14, "little"),
                    epoch=int(r.epoch_now.value)))  # fmt: skip
            tv = bool(r.m_tx_valid.value)
            if tv and not tv_prev:
                self.tx_valid.append(c)
            tv_prev = tv and not r.m_tx_ready.value
            # tready for edge c + 1, driven at the falling edge before it (_ready)
            self.next_ready = not self.hold and random.random() < self.p_ready
            if d.m_axis_tx_tvalid.value:
                if not tx_busy and first_valid is None:
                    first_valid = c
                if self.next_ready:  # taken at edge c + 1
                    if not tx_busy:
                        self.tx_first.append((first_valid, c + 1))
                        first_valid = None
                    beat = int(d.m_axis_tx_tdata.value).to_bytes(8, "little")
                    keep = int(d.m_axis_tx_tkeep.value)
                    buf += beat[: bin(keep).count("1")]
                    tx_busy = not d.m_axis_tx_tlast.value
                    if not tx_busy:
                        self.frames.append(bytes(buf))
                        buf.clear()
            # event_packer's order inputs, sampled at edge c + 1 (decision 28: ORDER_SENT first)
            if p.s_sent_valid.value:
                req = fields_req(p, "s_sent_")
                self.entries.append((Kind.ORDER_SENT, RingOrder(req, Reason.NONE, int(p.s_sent_user_ref.value),
                                                                int(p.s_sent_tx_ts.value))))  # fmt: skip
            if p.s_rej_valid.value:
                req = fields_req(p, "s_rej_")
                self.entries.append((Kind.RISK_REJECT, RingOrder(req, Reason(int(p.s_rej_reason.value)),
                                                                 ts=int(p.s_rej_ts.value))))  # fmt: skip
            if r.ev_rej_dropped.value:
                self.lost.append((c, Reason(int(r.ev_rej_dropped_reason.value))))


async def order_system(dut, rules: dict[int, RuleCfg], size_log2: int = 6, **kw):
    """A ring system (every class out, nothing filtered), the order path brought up, the watch and the exchange."""
    w, sysm, host = await ring_system(dut, size_log2)
    watch = OrderWatch(dut)
    oh = OrderHost(w)
    await oh.bring_up(rules, **kw)
    return w, sysm, host, oh, watch


def last_out(sysm: System) -> tuple[list[tuple[int, Event]], int, int]:
    """The last packet's OUT messages (seq, event), its SOF timestamp and cfg_epoch, as the chain model saw them."""
    pkt = sysm.mold_done[-1]
    return pkt.out, pkt.ts, pkt.epoch


def hw_order(idx: int, rule: RuleCfg, sysm: System, k: int = 0) -> OrderReq:
    """The model's order for rule idx fired by the k-th OUT message of the last packet."""
    out, ts, epoch = last_out(sysm)
    seq, ev = out[k]
    return make_order(idx, model_rule(rule), ev, seq, ts, epoch)


def check_frames(watch: OrderWatch, want: list[tuple[OrderReq, int]], user_ref_first: int = 1) -> None:
    """Every frame through the exchange model, and equal to the model's orders (req, UserRefNum)."""
    ex = Exchange(TXC.template, user_ref_first)
    exp = [ouch_of(req, ref, TX_MODEL) for req, ref in want]
    for k, f in enumerate(watch.frames):
        if len(f) != FRAME_LEN:
            model = frame(TX_MODEL, exp[k]) if k < len(exp) else b""
            raise AssertionError(f"frame {k}: {len(f)} B\n got  {f.hex()}\n want {model.hex()}")
    got = [ex.receive(f) for f in watch.frames]
    assert got == exp, f"frames (exchange's view) {got}, model {exp}"


def check_ring_orders(sysm: System, watch: OrderWatch, flagged: frozenset[int] = frozenset()) -> None:
    """The ring's order entries are event_packer's order inputs, in order, as the ring model encodes them. flagged:
    the order entries (k-th) that carry overflow_before."""
    assert len(sysm.ring_orders) == len(watch.entries), (
        f"{len(sysm.ring_orders)} order entries read, event_packer took {len(watch.entries)}")
    for k, ((idx, entry), (_, o)) in enumerate(zip(sysm.ring_orders, watch.entries, strict=True)):
        want = stamp(order_entry(o, FLAG_OVERFLOW_BEFORE if k in flagged else 0), idx, sysm.ring_size_log2)
        assert entry == want, f"ring entry {idx} (order {k}): {decode(entry)}, model {decode(want)}"


async def drain_checked(w, sysm: System, host: RingHost, watch: OrderWatch, orders: int,
                        flagged: frozenset[int] = frozenset()) -> dict[str, int]:  # fmt: skip
    await host.drain(orders=orders)
    check_ring_orders(sysm, watch, flagged)
    v = named(await snapshot_checked(w))
    await host.check_quiet(v)
    return v


# ---------------------------------------------------------------------------------------------


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_order_rule(dut):
    """audit/04 §3.4 scenario 5: a programmed rule, a trigger message, the right OUCH order on the TX port and its
    ORDER_SENT in the ring; the latency hop by hop (P10); one-shot (the next trigger sends nothing); re-armed it
    fires again with the next UserRefNum; a risk reject (QTY) is a RISK_REJECT and no frame."""
    w, sysm, host, oh, watch = await order_system(dut, {0: BUY_ON_ADD})
    await send_feed(sysm, 100, [order(LOC, 1)])
    v = await drain_checked(w, sysm, host, watch, 1)
    first = hw_order(0, BUY_ON_ADD, sysm)
    check_frames(watch, [(first, 1)])
    assert (v["RULE_FIRED_0"], v["ORDERS_SENT_HW"], v["RULE_ARMED"], v["BUDGET"]) == (1, 1, 0, 999)
    (kind, o), = watch.entries
    assert kind == Kind.ORDER_SENT and o.user_ref == 1

    # P10: the trigger packet's last beat -> forwarded packet event -> released order -> m_tx_* -> first TX beat.
    tlast = watch.udp_tlast[-1]
    (fwd, status), = watch.fwd[-1:]
    dut._log.info(f"hops: sof {watch.sof[-1]} rx tlast {watch.tlast[-1]} udp tlast {tlast} fwd {fwd} "
                  f"released {watch.released} tx_valid {watch.tx_valid} first beat {watch.tx_first}")
    assert fwd == tlast + PKT_LATENCY + ITCH_LATENCY - 1, (tlast, fwd)
    (rel, released), = watch.released
    assert (released, rel) == (first, fwd + STRAT_LATENCY), (fwd, rel, released)
    assert watch.tx_valid == [rel + 1 + RISK_LATENCY], (rel, watch.tx_valid)
    (beat_valid, beat_taken), = watch.tx_first
    assert beat_valid == watch.tx_valid[0] + 1, (watch.tx_valid, beat_valid)
    # eth_ip_udp_rx: payload tlast 1 cycle after the input tlast, 2 if the last input beat holds > 2 payload bytes.
    assert tlast - watch.tlast[-1] in (1, 2), (watch.tlast[-1], tlast)
    total = beat_valid - (watch.sof[-1] - 1)
    dut._log.info(f"SOF -> first TX beat: {total} net cycles ({len(watch.frames[0])} B frame)")

    # One-shot: the rule was disarmed by the acceptance.
    await send_feed(sysm, 101, [order(LOC, 2)])
    v = await drain_checked(w, sysm, host, watch, 1)
    assert (v["RULE_FIRED_0"], v["ORDERS_SENT_HW"], len(watch.frames)) == (1, 1, 1)

    # Re-armed: fires again, UserRefNum 2.
    await oh.arm(1)
    await send_feed(sysm, 102, [order(LOC, 3)])
    v = await drain_checked(w, sysm, host, watch, 2)
    second = hw_order(0, BUY_ON_ADD, sysm)
    check_frames(watch, [(first, 1), (second, 2)])
    assert (v["RULE_FIRED_0"], v["ORDERS_SENT_HW"], v["BUDGET"]) == (2, 2, 998)

    # Risk reject: max_qty below the rule's qty.
    await oh.limits(replace(OPEN, max_qty=99))
    await oh.arm(1)
    await send_feed(sysm, 103, [order(LOC, 4)])
    v = await drain_checked(w, sysm, host, watch, 3)
    rejected = hw_order(0, BUY_ON_ADD, sysm)
    kind, o = watch.entries[-1]
    assert (kind, o.reason, o.req) == (Kind.RISK_REJECT, Reason.QTY, rejected)
    assert (v["RULE_FIRED_0"], v["RISK_REJECT_QTY"], v["ORDERS_SENT_HW"], v["BUDGET"]) == (3, 1, 2, 998)
    assert len(watch.frames) == 2
    w.feed.check_done()


async def settle(dut, cycles: int) -> None:
    for _ in range(cycles):
        await RisingEdge(dut.net_clk)


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_order_kill_cancels_the_queue(dut):
    """Scenario 5, kill switch (decisions 20, 21): two orders fire in one packet while the MAC holds tready low. The
    first is handed over (its frame has started), the second waits in the TX queue. Kill cancels the queued one
    (RISK_REJECT KILL, no frame) and the started frame still goes out whole once tready returns; neither gives its
    budget back. While killed, a trigger fires no rule (decision 23)."""
    w, sysm, host, oh, watch = await order_system(dut, {0: BUY_ON_ADD, 1: BUY_ON_ADD})
    watch.hold = True
    await send_feed(sysm, 100, [order(LOC, 1), order(LOC, 2)])
    await sysm.finish()
    await settle(dut, 20)
    a, b = hw_order(0, BUY_ON_ADD, sysm, 0), hw_order(1, BUY_ON_ADD, sysm, 1)
    assert int(dut.u_tx.s_ord_ready.value) == 0 and int(dut.u_risk.m_tx_valid.value) == 1, "b waits behind a"
    await oh.kill(True)
    await settle(dut, 20)
    watch.hold = False
    v = await drain_checked(w, sysm, host, watch, 2)
    check_frames(watch, [(a, 1)])
    assert [(k, o.reason, o.req) for k, o in watch.entries] == [
        (Kind.RISK_REJECT, Reason.KILL, b), (Kind.ORDER_SENT, Reason.NONE, a)]
    assert (v["RULE_FIRED_0"], v["RULE_FIRED_1"], v["ORDERS_SENT_HW"], v["RISK_REJECT_KILL"]) == (1, 1, 1, 1)
    assert (v["BUDGET"], v["RULE_ARMED"]) == (998, 0), "no refund on a cancel (decision 21)"

    await oh.arm(0b11)  # armed while killed: the path is closed, so no rule fires and none is used up
    await send_feed(sysm, 102, [order(LOC, 3)])
    v = await drain_checked(w, sysm, host, watch, 2)
    assert (v["RULE_FIRED_0"], v["RULE_ARMED"], len(watch.frames)) == (1, 0b11, 1)
    w.feed.check_done()


def host_req(m: MboxOrder, epoch: int) -> OrderReq:
    return OrderReq(Source.HOST, m.side, m.qty, m.price, m.locate, m.stock, m.tif, clordid=m.clordid, epoch=epoch)


MBOX = MboxOrder(side=ord("S"), qty=250, price=1_234_500, locate=LOC, stock=b"AAPL    ", tif=ord("3"),
                 clordid=b"HOSTORDER00001")  # fmt: skip


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_order_mailbox(dut):
    """audit/04 §3.4 scenario 6: a host order through the mailbox leaves as OUCH with the host's ClOrdID and its
    ORDER_SENT (source HOST, the cfg_epoch in force); a host order and a hardware order at the same time both leave,
    each with its own UserRefNum in the order they are sent; MBOX_SPACE reads 16 when the mailbox is empty; an
    invalid host order (ClOrdID byte) is a RISK_REJECT INVALID."""
    w, sysm, host, oh, watch = await order_system(dut, {0: BUY_ON_ADD})
    epoch = 1  # cfg_epoch counts FEED commits: ring_system made one
    assert await w.drv.read(addr("TX.MBOX_SPACE")) == (16, OKAY)
    await oh.mbox(MBOX)
    v = await drain_checked(w, sysm, host, watch, 1)
    first = host_req(MBOX, epoch)
    check_frames(watch, [(first, 1)])
    (kind, o), = watch.entries
    assert (kind, o.req, o.user_ref) == (Kind.ORDER_SENT, first, 1)
    assert (v["ORDERS_SENT_HOST"], v["ORDERS_SENT_HW"], v["BUDGET"]) == (1, 0, 999)

    # Hardware and host together: the trigger packet and a mailbox order in flight at once.
    other = replace(MBOX, side=ord("B"), clordid=b"HOSTORDER00002")
    send = cocotb.start_soon(send_feed(sysm, 100, [order(LOC, 1)]))
    await oh.mbox(other)
    await send
    v = await drain_checked(w, sysm, host, watch, 3)
    hw = hw_order(0, BUY_ON_ADD, sysm)
    sent = [(o.req.source, o.user_ref) for k, o in watch.entries[1:] if k == Kind.ORDER_SENT]
    by_source = {Source.RULE: hw, Source.HOST: host_req(other, epoch)}
    check_frames(watch, [(first, 1)] + [(by_source[src], ref) for src, ref in sent])
    assert sorted(src for src, _ in sent) == [Source.RULE, Source.HOST]
    assert (v["ORDERS_SENT_HOST"], v["ORDERS_SENT_HW"]) == (2, 1)
    assert await w.drv.read(addr("TX.MBOX_SPACE")) == (16, OKAY)

    bad = replace(MBOX, clordid=b"HOST-ORDER-003")  # '-' is not alphanumeric (decision 13)
    await oh.mbox(bad)
    v = await drain_checked(w, sysm, host, watch, 4)
    kind, o = watch.entries[-1]
    assert (kind, o.reason, o.req) == (Kind.RISK_REJECT, Reason.INVALID, host_req(bad, epoch))
    assert (v["RISK_REJECT_INVALID"], len(watch.frames)) == (1, 3)
    w.feed.check_done()


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_order_bad_packets(dut):
    """audit/04 §3.4 scenario 7 and the feed's other refusals: a trigger in a frame with a bad FCS, in a duplicate
    packet (already seen) and in another session's packet sends no order and writes no order entry, and the rule
    stays armed (decision 1); the next good trigger fires it."""
    w, sysm, host, oh, watch = await order_system(dut, {0: BUY_ON_ADD})
    await send_feed(sysm, 100, [order(70, 1, "MSFT")])  # in sync at 101, no trigger
    await send_feed(sysm, 101, [order(LOC, 2)], tuser=True)  # bad FCS: dropped by the receiver
    await send_feed(sysm, 100, [order(LOC, 3)])  # duplicate: seq 100 was seen
    await sysm.send(feed_frame(mold.pack(b"OTHERSESS1", 101, [order(LOC, 4)]), 0), gap=2)  # another session
    v = await drain_checked(w, sysm, host, watch, 0)
    assert (len(watch.frames), v["RULE_FIRED_0"], v["RULE_ARMED"]) == (0, 0, 1)
    assert v["DROP_FCS"] == 1 and v["MOLD_DUP"] >= 1, v
    await send_feed(sysm, 101, [order(LOC, 5)])
    v = await drain_checked(w, sysm, host, watch, 1)
    check_frames(watch, [(hw_order(0, BUY_ON_ADD, sysm), 1)])
    assert (v["RULE_FIRED_0"], v["RULE_ARMED"]) == (1, 0)
    w.feed.check_done()


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_order_feed_filter_gates_rules(dut):
    """Decision 39: the rule sees only what itch_decoder outputs. ORDER class off (MASKED): no fire; ORDER class
    filtered and the locate not in SYMBOL (FILTERED): no fire; the locate subscribed: it fires."""
    all_en = (1 << len(ItchClass)) - 1
    w, sysm, host, oh, watch = await order_system(dut, {0: BUY_ON_ADD})
    await configure_feed(w, replace(FEED_ON, class_en=all_en & ~bit(ItchClass.ORDER)))
    await send_feed(sysm, 100, [order(LOC, 1)])
    await configure_feed(w, replace(FEED_ON, class_en=all_en, class_filter=bit(ItchClass.ORDER)))
    await send_feed(sysm, 101, [order(LOC, 2)])
    v = await drain_checked(w, sysm, host, watch, 0)
    assert (v["ITCH_MASKED"], v["ITCH_FILTERED"], len(watch.frames), v["RULE_ARMED"]) == (1, 1, 0, 1)
    assert await w.drv.write(SYMBOL.addr, 1 << LOC) == OKAY
    await send_feed(sysm, 102, [order(LOC, 3)])
    v = await drain_checked(w, sysm, host, watch, 1)
    check_frames(watch, [(hw_order(0, BUY_ON_ADD, sysm), 1)])
    assert (v["RULE_FIRED_0"], v["ITCH_OUT"]) == (1, 1)
    w.feed.check_done()


@cocotb.test(timeout_time=100, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_order_ring_pressure(dut):
    """Order entries under ring pressure (decisions 25, 29, 30, 31). The host stops reading: the commit FIFO fills
    with packets and one packet is dropped. Then 20 host orders arrive while killed (KILL rejects): the first 16
    take the 16 ring credits and wait in event_packer's order queue, the last 4 find no credit and are lost
    (RING_DROP_ORDERS, still RISK_REJECT_KILL). The identity holds in snapshots taken while entries wait. The host
    reads again: the first order entry carries the dropped packet's overflow_before (decision 31), and the lost
    rejects' flag goes on the next entry written, the next packet's first (decision 29)."""
    w, sysm, host, oh, watch = await order_system(dut, {}, size_log2=4)
    await oh.kill(True)
    host.hold = True
    seq, n = 100, 40
    while sysm.ring_drop_pkts == 0:  # large packets until one does not fit
        await send_feed(sysm, seq, [order(70, k, "MSFT") for k in range(n)])
        await sysm.finish()
        seq += n
    while sysm.ring_drop_pkts == 1:  # then one-message packets until one does not fit: the FIFO is full
        await send_feed(sysm, seq, [order(70, seq, "MSFT")])
        await sysm.finish()
        seq += 1
    mbox = [replace(MBOX, clordid=f"HOSTORDER{k:05d}".encode()) for k in range(20)]
    for m in mbox:
        await oh.mbox(m)
    await settle(dut, 50)
    v = named(await snapshot_checked(w))  # the identity with 16 entries waiting
    got = {k: v[k] for k in ("RING_ORDER_QUEUED", "RING_DROP_ORDERS", "RISK_REJECT_KILL", "RING_DROP_PKTS",
                             "RING_COMMIT_ENTRIES")}  # fmt: skip
    assert (got["RING_ORDER_QUEUED"], got["RING_DROP_ORDERS"], got["RISK_REJECT_KILL"]) == (16, 4, 20), (
        f"{got}, lost {watch.lost}, entries {len(watch.entries)}")
    assert [r for _, r in watch.lost] == [Reason.KILL] * 4
    host.hold = False
    await host.drain(orders=16)
    await send_feed(sysm, seq, [order(70, n, "MSFT")])  # its first entry carries the lost rejects' flag
    v = await drain_checked(w, sysm, host, watch, 16, flagged=frozenset({0}))
    assert [(k, o.reason, o.req) for k, o in watch.entries] == [
        (Kind.RISK_REJECT, Reason.KILL, host_req(m, 1)) for m in mbox[:16]]
    assert (v["RING_ORDER_QUEUED"], v["RING_DROP_ORDERS"], v["RISK_REJECT_KILL"]) == (0, 4, 20)
    assert sysm.ring_flags[-1] == sysm.ring_read - 1 + 16, "the last packet's entry carries the flag"
    w.feed.check_done()


KILL_EXTRA_BOUND = 24 + 8 + 24  # net cycles over the no-hardware case (core README, Kill switch latency)


@cocotb.test(timeout_time=300, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_order_kill_latency_under_burst(dut):
    """Decision 38, handed to S8: a kill write behind a command fenced by 16 MBOX_SENDs, with the real blocks.
    Base: no hardware order. Burst: all 8 rules armed and a packet of 8 trigger messages whose orders reach
    risk_check while the fence waits (the packet's start swept over 48 net cycles, so the overlap does not depend on
    the seed); the MAC holds tready low, so the TX queue fills and later orders are TX_BUSY rejects. Measured from
    the kill write's W handshake to ctl_kill at risk_check; the hardware orders risk_check took in that time are
    counted. The burst may add at most KILL_EXTRA_BOUND net cycles to the slowest base case (the README analysis).
    Every frame, order entry and snapshot is checked as in the other tests."""
    net_ps = PROFILES[os.environ["RATIO"]][0]
    w, sysm, host, oh, watch = await order_system(dut, {i: BUY_ON_ADD for i in range(8)}, budget=1_000_000)
    await oh.mbox(MBOX)  # the shadow every MBOX_SEND below sends again
    await host.drain(orders=1)
    risk = dut.u_risk
    hw_times: list[int] = []

    async def hw_monitor() -> None:
        while True:
            await RisingEdge(dut.net_clk)
            await ReadOnly()
            if risk.s_hw_valid.value:
                hw_times.append(get_sim_time())

    cocotb.start_soon(hw_monitor())
    results: dict[str, list[tuple[int, int]]] = {"base": [], "burst": []}
    seq = 100
    for case, delay in [("base", d) for d in range(0, 48, 8)] + [("burst", d) for d in range(0, 48, 2)]:
        while risk.ctl_kill.value or not risk.ctl_tx_en.value:  # the release of the last round is in effect
            await RisingEdge(dut.net_clk)
            await ReadOnly()
        await RisingEdge(dut.net_clk)
        watch.hold = True
        feed = None
        if case == "burst":
            feed = cocotb.start_soon(send_feed(sysm, seq, [order(LOC, k) for k in range(8)]))
            seq += 8
        await settle(dut, delay)
        t0 = len(w.drv.hs_time["w"])
        sends = [w.drv.write_nowait(MBOX_SEND, 1) for _ in range(16)]
        q_cmd = w.drv.write_nowait(COMMANDS["BUDGET_ADD"], 1)
        q_kill = w.drv.write_nowait(RISK_CTRL, 1)
        while not risk.ctl_kill.value:
            await RisingEdge(dut.net_clk)
            await ReadOnly()
        t_kill = get_sim_time()
        await RisingEdge(dut.host_clk)
        for q in [*sends, q_cmd, q_kill]:
            await q.wait()
        t_w = w.drv.hs_time["w"][t0 + 17]
        assert t_kill > t_w, f"ctl_kill rose at {t_kill} ps, before the kill write's W handshake at {t_w} ps"
        n_hw = sum(t_w <= t <= t_kill for t in hw_times)
        results[case].append((t_kill - t_w, n_hw))
        if feed is not None:
            await feed
        await settle(dut, 300)
        watch.hold = False
        v = await drain_checked(w, sysm, host, watch, len(watch.entries))
        sent = [o for k, o in watch.entries if k == Kind.ORDER_SENT]
        check_frames(watch, [(o.req, o.user_ref) for o in sent])
        assert v["RISK_REJECT_KILL"] + v["ORDERS_SENT_HOST"] + v["ORDERS_SENT_HW"] + v["RISK_REJECT_TX_BUSY"] > 0
        await oh.arm(0xFF)  # the kill rise disarmed them (decision 23)
        await oh.kill(False)
    base = max(t for t, _ in results["base"])
    burst = max(t for t, _ in results["burst"])
    overlap = [n for _, n in results["burst"]]
    dut._log.info(f"kill (W handshake) -> ctl_kill at risk_check: base {[t for t, _ in results['base']]} ps; "
                  f"burst {results['burst']} (ps, hardware orders taken meanwhile); net period {net_ps} ps")  # fmt: skip
    assert max(overlap) > 0, "no hardware order reached risk_check while the fenced command waited"
    assert burst <= base + KILL_EXTRA_BOUND * net_ps, (
        f"burst {burst} ps > slowest base {base} ps + {KILL_EXTRA_BOUND} net cycles")
    w.feed.check_done()


# ---- the random hybrid test


LOCS = (5, 6, 7)  # trigger locates: word 0 of LOCATE_ALLOW
N_BAGS = 4  # each bag runs every round type once, shuffled; do not lower
ROUNDS = ("open", "killed", "tx_off", "locate", "qty", "notional", "budget", "busy")
RANDOM_BINS = ["sent_hw", "sent_host", "hw_and_host_in_a_round"] + [f"reason_{r}" for r in
               ("KILL", "TX_OFF", "INVALID", "LOCATE", "QTY", "NOTIONAL", "BUDGET", "TX_BUSY")]  # fmt: skip


def random_rule(rng: random.Random) -> RuleCfg:
    return RuleCfg(enable=int(rng.random() < 0.9), msg_mask=rng.randint(1, 7),
                   locate=0 if rng.random() < 0.05 else rng.choice(LOCS), side_match=rng.randint(1, 3),
                   price_ge=rng.getrandbits(1), threshold=rng.randint(900_000, 1_100_000),
                   min_shares=rng.choice([0, 50, 200]), action_side=rng.randint(0, 2), qty_min=rng.getrandbits(1),
                   qty=rng.choice([1, 100, 500, 999_999]), price_mode=int(rng.random() < 0.2),
                   offset=rng.randint(-500, 500) & 0xFFFF_FFFF, tif=rng.choice(b"035E"))  # fmt: skip


def random_msg(rng: random.Random, k: int) -> bytes:
    typ = rng.choice("AAFPX")  # X (Order Cancel) never triggers
    common = {"locate": rng.choice((*LOCS, 70)), "tracking": k & 0xFFFF, "timestamp": 34_200_000_000_000 + k}
    if typ == "X":
        return encode("X", **common, order_ref=k, cancelled_shares=10)
    kw = {"order_ref": k, "side": rng.choice("BS"), "shares": rng.choice([1, 100, 300, 2_000_000]),
          "stock": "AAPL    ", "price": rng.randint(800_000, 1_200_000)}  # fmt: skip
    if typ == "F":
        kw["attribution"] = "MPID"
    if typ == "P":
        kw["match"] = k
    return encode(typ, **common, **kw)


def random_host(rng: random.Random, k: int, **kw) -> MboxOrder:
    """A host order with a unique ClOrdID (sometimes invalid: a '-' in it, side T, qty 0)."""
    o = MboxOrder(side=ord(rng.choice("BS")), qty=rng.choice([1, 100, 200, 500]), price=rng.randint(900_000, 1_100_000),
                  locate=rng.choice(LOCS), stock=b"AAPL    ", tif=rng.choice(b"035E"),
                  clordid=f"R{k:013d}".encode())  # fmt: skip
    bad = rng.random()
    if bad < 0.05:
        o = replace(o, clordid=f"R-{k:012d}".encode())
    elif bad < 0.08:
        o = replace(o, side=ord("T"))
    elif bad < 0.10:
        o = replace(o, qty=0)
    return replace(o, **kw)


@dataclass
class Phase:
    """What risk_check checks against during a round (changed only while the path is quiet)."""

    kill: bool = False
    tx_en: bool = True
    allow: tuple[int, ...] = LOCS
    lim: LimCfg = OPEN

    def reason(self, o: OrderReq, budget: int) -> Reason:
        """The first check that holds, KILL .. BUDGET (P6 order); RATE cannot hold (the bucket refills one token
        a cycle); TX_BUSY and RING_FULL depend on timing, the caller allows them when this is NONE."""
        checks = ((Reason.KILL, self.kill), (Reason.TX_OFF, not self.tx_en), (Reason.INVALID, not valid_order(o)),
                  (Reason.LOCATE, o.locate not in self.allow), (Reason.QTY, o.qty > self.lim.max_qty),
                  (Reason.NOTIONAL, o.qty * o.price > self.lim.max_notional), (Reason.BUDGET, budget == 0))  # fmt: skip
        return next((r for r, hit in checks if hit), Reason.NONE)


def check_round(phase: Phase, budget: int, watch: OrderWatch, n_taken: int, n_entries: int, n_lost: int,
                hit: set[str] | None = None) -> int:  # fmt: skip
    """Every order risk_check took since n_taken has exactly one outcome (ORDER_SENT, RISK_REJECT or a lost reject),
    with the reason of the first check that holds (Phase.reason, the budget run in risk_check's order); TX_BUSY and
    RING_FULL only where the static checks pass. Returns the budget left; hit collects the coverage bins."""
    hit = set() if hit is None else hit
    outcomes: dict[OrderReq, RingOrder] = {o.req: o for _, o in watch.entries[n_entries:]}
    assert len(outcomes) == len(watch.entries) - n_entries, "two entries for one order"
    lost = [r for _, r in watch.lost[n_lost:]]
    for o in watch.taken[n_taken:]:
        want = phase.reason(o, budget)
        got = outcomes.pop(o, None)
        if got is None:  # lost: no credit for its reject
            assert lost and want in (lost[0], Reason.NONE), f"lost {lost}, order {o} ({want.name})"
            hit.add(f"reason_{lost.pop(0).name}")
            continue
        if got.reason == Reason.NONE:
            assert want == Reason.NONE, f"{o} sent, model {want.name}"
            budget -= 1
            hit.add("sent_hw" if o.source == Source.RULE else "sent_host")
        else:
            assert got.reason == want or (want == Reason.NONE and got.reason in (Reason.TX_BUSY, Reason.RING_FULL)), (
                f"{o} rejected {got.reason.name}, model {want.name}")
            hit.add(f"reason_{got.reason.name}")
    assert not outcomes and not lost, f"outcomes without an order {outcomes}, lost {lost}"
    return budget


@cocotb.test(timeout_time=3000, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_order_random(dut):
    """Random hybrid traffic (audit/14 §2.10): N_BAGS bags of every round type, shuffled. Between rounds, with the
    path quiet, software changes rules (commit, arm, disarm), limits, LOCATE_ALLOW, budget, tx_en and kill as the
    round type asks. In a round, random ITCH packets (A, F, P, X on trigger and other locates) and random host orders
    (some invalid) run at once; a 'busy' round holds tready while 12 host orders arrive. After each round:
    - strategy_engine released exactly the strategy model's orders (model fed with the OUT messages of each packet
      as the ITCH chain model saw them, and the packet's acceptance);
    - every order risk_check took has exactly one outcome (ORDER_SENT, RISK_REJECT or a lost reject), and its reason
      is the first check that holds (Phase.reason, with the budget model run in risk_check's order); TX_BUSY and
      RING_FULL only where the static checks pass;
    - every frame is the order of its ORDER_SENT (UserRefNum +1 each), the ring's order entries are event_packer's
      inputs, BUDGET and the counters match, and the identities hold.
    Coverage: every reason but RATE and RING_FULL (their timing is the unit tests'), orders sent from both sources,
    and both in one round; the bag makes each bin independent of the seed."""
    rng = random.Random(random.getrandbits(32))
    w, sysm, host, oh, watch = await order_system(dut, {}, budget=0, allow=LOCS)
    strat = Strategy()
    budget, phase, epoch, seq, k_msg, k_host = 0, Phase(), 1, 100, 0, 0
    hit: set[str] = set()
    for rnd, kind in enumerate([t for _ in range(N_BAGS) for t in rng.sample(ROUNDS, len(ROUNDS))]):
        # ---- software, path quiet
        new = Phase(kill=kind == "killed", tx_en=kind != "tx_off", allow=LOCS[:1] if kind == "locate" else LOCS,
                    lim=replace(OPEN, max_qty=150) if kind == "qty" else
                    replace(OPEN, max_notional=150 * 1_000_000) if kind == "notional" else OPEN)  # fmt: skip
        if new.kill and not phase.kill:
            await oh.kill(True)
            strat.disarm((1 << 8) - 1)  # the kill rise disarms every rule (decision 23)
        for idx in rng.sample(range(8), rng.randint(0, 3)):
            r = random_rule(rng)
            await oh.rule(idx, r)
            strat.commit(idx, model_rule(r))
        if kind == "open":  # a rule that the round's first message surely fires
            await oh.rule(0, BUY_ON_ADD)
            strat.commit(0, model_rule(BUY_ON_ADD))
        if mask := rng.getrandbits(8) | (1 if kind == "open" else 0):
            await oh.arm(mask)
            strat.arm(mask)
        if rng.random() < 0.3 and (mask := rng.getrandbits(8) & ~1):
            await oh.disarm(mask)
            strat.disarm(mask)
        if new.lim != phase.lim:
            await oh.limits(new.lim)
        if new.allow != phase.allow:
            await oh.wr(LOCATE_ALLOW.addr, sum(1 << loc for loc in new.allow))
        add = 0 if kind == "budget" else rng.randint(10, 20)
        if add:
            await oh.budget(add)
            budget = min(budget + add, 0xFFFF_FFFF)
        if new.tx_en != phase.tx_en:
            await oh.tx_en(new.tx_en)
        if not new.kill and phase.kill:
            await oh.kill(False)
        phase = new
        await settle(dut, 10)  # kill and tx_en through their synchronizers

        # ---- traffic
        n_taken, n_entries, n_lost, n_rel, n_pkt = (len(watch.taken), len(watch.entries), len(watch.lost),
                                                    len(watch.released), len(sysm.mold_done))  # fmt: skip
        hosts: list[MboxOrder] = []
        def valid(k: int) -> MboxOrder:
            return random_host(rng, k, qty=100, side=ord("B"), clordid=f"R{k:013d}".encode())

        if kind == "busy":  # 1 frame started + 8 queued, the rest TX_BUSY
            hosts = [valid(k_host + i) for i in range(12)]
        elif kind == "budget":  # 3 more than the budget left
            hosts = [valid(k_host + i) for i in range(budget + 3)]
        else:
            hosts = [random_host(rng, k_host + i) for i in range(rng.randint(1, 4))]
            forced = {"locate": {"locate": LOCS[1], "qty": 100}, "qty": {"qty": 200},
                      "notional": {"qty": 140, "price": 1_100_000}}  # fmt: skip
            if kind in forced:
                hosts[0] = random_host(rng, k_host, side=ord("B"), clordid=f"R{k_host:013d}".encode(), **forced[kind])
            if kind == "open":
                hosts.append(random_host(rng, k_host + len(hosts), clordid=f"R-{k_host + len(hosts):012d}".encode()))
        k_host += len(hosts)
        watch.hold = kind == "busy"

        async def feed() -> None:
            nonlocal seq, k_msg
            for p in range(rng.randint(1, 4)):
                msgs = [random_msg(rng, k_msg + i) for i in range(rng.randint(1, 6))]
                if kind == "open" and p == 0:
                    msgs[0] = order(LOC, k_msg)  # Add, side B, LOC: BUY_ON_ADD fires
                k_msg += len(msgs)
                await send_feed(sysm, seq, msgs, gap=rng.randint(0, 20))
                seq += len(msgs)

        task = cocotb.start_soon(feed())
        for m in hosts:
            await oh.mbox(m)
        await task
        await sysm.finish()
        await settle(dut, 60)
        watch.hold = False
        await settle(dut, 300)

        # ---- strategy: the model fed packet by packet
        want_rel: list[OrderReq] = []
        for pkt in sysm.mold_done[n_pkt:]:
            if not phase.kill and phase.tx_en:
                for s, ev in pkt.out:
                    strat.message(ev, s, pkt.ts, pkt.epoch)
            want_rel += strat.packet(pkt.want.status in ACCEPTED)
        got_rel = [o for _, o in watch.released[n_rel:]]
        assert got_rel == want_rel, f"round {rnd} ({kind}): released {got_rel}, model {want_rel}"

        # ---- risk: one outcome per order taken, the reason of the first check that holds
        dut._log.info(f"round {rnd} ({kind}): {len(watch.taken) - n_taken} orders taken")
        budget = check_round(phase, budget, watch, n_taken, n_entries, n_lost, hit)
        sources = {o.source for o in watch.taken[n_taken:]}
        if len(sources) == 2 and "sent_hw" in hit:
            hit.add("hw_and_host_in_a_round")

        v = await drain_checked(w, sysm, host, watch, len(watch.entries))
        sent = [o for kd, o in watch.entries if kd == Kind.ORDER_SENT]
        check_frames(watch, [(o.req, o.user_ref) for o in sent])
        assert v["BUDGET"] == budget, f"round {rnd}: BUDGET {v['BUDGET']}, model {budget}"
        assert v["RULE_ARMED"] == strat.armed, f"round {rnd}: RULE_ARMED {v['RULE_ARMED']:#x}, model {strat.armed:#x}"
        assert [v[f"RULE_FIRED_{i}"] for i in range(8)] == strat.fired
    hit.discard("reason_NONE")
    missing = [b for b in RANDOM_BINS if b not in hit]
    assert not missing, f"bins not hit: {missing}"
    dut._log.info(f"{N_BAGS * len(ROUNDS)} rounds, {len(watch.taken)} orders taken, {len(watch.frames)} frames")
    w.feed.check_done()


@cocotb.test(timeout_time=600, timeout_unit="ms")
async def tc_real_order(dut):
    """Real Nasdaq data (the pytest entry downloads the range, ITCH_REAL; windows as tc_real_itch): FEED with every
    class out and every class but ADMIN filtered, locates 0 mod 4 subscribed (decision 39: a rule's locate must pass
    them). Before sending, the windows are scanned for the 8 subscribed locates with the most A/F/P messages; one rule
    each (any side, any price, the opposite side, 100 at the event price), all armed, re-armed between windows (the
    path quiet). strategy_engine must release exactly the strategy model's orders (fed with every packet as the ITCH
    chain model saw it); every order leaves (open limits, ample budget), as the exchange checks it, with its
    ORDER_SENT; every snapshot holds the identities."""
    path, windows = Path(os.environ["ITCH_REAL"]), json.loads(os.environ["ITCH_REAL_WINDOWS"])
    batches = [(i, b) for i, b in enumerate(packets(path)) if i < windows[-1][0] + windows[-1][1]]
    counts: dict[int, int] = {}
    for i, batch in batches:
        if any(a <= i < a + n for a, n in windows):
            for m in batch:
                if m[:1] in (b"A", b"F", b"P") and (loc := int.from_bytes(m[1:3], "big")) % 4 == 0 and loc:
                    counts[loc] = counts.get(loc, 0) + 1
    locs = sorted(counts, key=lambda loc: -counts[loc])[:8]
    assert len(locs) == 8, f"only {len(locs)} subscribed locates with A/F/P messages"
    rules = {i: RuleCfg(enable=1, msg_mask=0b111, locate=loc, side_match=0b11, price_ge=0, threshold=0xFFFF_FFFF,
                        min_shares=0, action_side=2, qty=100, tif=ord("0")) for i, loc in enumerate(locs)}  # fmt: skip

    w, sysm = await start(dut)  # keep stays on: the strategy model reads sysm.mold_done
    all_en = (1 << len(ItchClass)) - 1
    await configure(w, NET_FEED)
    await configure_feed(w, replace(FEED_ON, class_en=all_en, class_filter=all_en & ~bit(ItchClass.ADMIN)))
    host = RingHost(dut, w, sysm, 12)
    await host.enable()
    for word in range(N_WORDS):
        assert await w.drv.write(SYMBOL.addr + 4 * word, 0x11111111) == OKAY  # locates 0 mod 4
    watch, oh = OrderWatch(dut), OrderHost(w)
    await oh.bring_up(rules, budget=1_000_000, allow=tuple(locs))
    strat = Strategy()
    for i, r in rules.items():
        strat.commit(i, model_rule(r))
    strat.arm(0xFF)
    phase, budget = Phase(allow=tuple(locs)), 1_000_000
    seq, sent_pkts, window = 1, 0, 0
    want_rel: list[OrderReq] = []
    n_pkt = len(sysm.mold_done)

    def model_packets() -> None:
        """The packets the chain model has seen since the last call, through the strategy model."""
        nonlocal n_pkt
        assert sysm.keep, "with keep off System records no packets: the strategy model would see none"
        for pkt in sysm.mold_done[n_pkt:]:
            for s, ev in pkt.out:
                strat.message(ev, s, pkt.ts, pkt.epoch)
            want_rel.extend(strat.packet(pkt.want.status in ACCEPTED))
        n_pkt = len(sysm.mold_done)

    for i, batch in batches:
        w_now = next((k for k, (a, n) in enumerate(windows) if a <= i < a + n), None)
        if w_now is not None:
            if w_now != window:  # between windows, the path quiet: the model catches up, then both re-arm
                window = w_now
                await sysm.finish()
                await settle(dut, 300)
                model_packets()
                await oh.arm(0xFF)
                strat.arm(0xFF)
            await sysm.send(feed_frame(mold.pack(SESSION, seq, batch), 0), name=f"packet {i}")
            sent_pkts += 1
        seq += len(batch)
    await sysm.finish()
    await settle(dut, 300)
    model_packets()
    got_rel = [o for _, o in watch.released]
    assert got_rel == want_rel, f"released {len(got_rel)} orders, model {len(want_rel)}: {got_rel} / {want_rel}"
    budget = check_round(phase, budget, watch, 0, 0, 0)
    v = await drain_checked(w, sysm, host, watch, len(watch.entries))
    sent = [o for kd, o in watch.entries if kd == Kind.ORDER_SENT]
    check_frames(watch, [(o.req, o.user_ref) for o in sent])
    assert len(sent) == len(want_rel) and v["ORDERS_SENT_HW"] == len(want_rel) and v["BUDGET"] == budget
    dut._log.info(f"{path.name}: {sent_pkts} packets, rules on locates {locs}, {len(want_rel)} orders sent")
    w.feed.check_done()


# ---------------------------------------------------------------------------------------------

RANDOM_TESTS = ("tc_order_random",)
REAL_TESTS = ("tc_real_order",)  # marker real: needs the downloaded data


def directed_tests() -> list[str]:
    return [name for name in globals() if name.startswith("tc_") and name not in RANDOM_TESTS + REAL_TESTS]


@pytest.mark.parametrize(
    "group, jit, ratio",
    [("directed", 0, "net2host"), ("directed", 0, "host2net"),
     ("tc_order_random", 0, "net2host"), ("tc_order_random", 1, "jitter")],
)  # fmt: skip
def test_rx_order(group, jit, ratio):
    assert ratio in PROFILES
    run_cocotb(
        toplevel="rx_csr_top",
        filelists=["common.f", "core.f", "csr.f", "system.f"],
        test_module="test_rx_order",
        defines={"HFT_SIM_CDC_JITTER": 1} if jit else {},
        testcase=directed_tests() if group == "directed" else group,
        extra_env={"GROUP": group, "JITTER": jit, "RATIO": ratio},
    )


@pytest.mark.real
@pytest.mark.parametrize("name", load())
def test_rx_order_real(name):
    src = load()[name]
    try:
        path = fetch(src)
    except Retired as e:
        pytest.skip(str(e))
    run_cocotb(
        toplevel="rx_csr_top",
        filelists=["common.f", "core.f", "csr.f", "system.f"],
        test_module="test_rx_order",
        testcase="tc_real_order",
        extra_env={"GROUP": "tc_real_order", "JITTER": 0, "RATIO": "net2host", "ITCH_REAL": path,
                   "ITCH_REAL_WINDOWS": json.dumps(src.system)},  # fmt: skip
    )
