"""Simulator-free tests of the strategy model (tb/model/strategy.py); audit/14 §2.3, §2.4, P4,
decisions 1, 2, 6, 8, 11, 14, 16.

Events come from real ITCH messages through itch.encode/expect. The expected orders are written
field by field here.
"""

import pytest

from model.itch import Bitmap, ItchConfig, encode, expect
from model.order import PRICE_LIMIT_MAX, Source
from model.strategy import (
    N_RULES,
    ActionSide,
    PriceMode,
    PriceOp,
    QtyMode,
    Rule,
    Strategy,
    matches,
    order_side,
    static_error,
)

STOCK = "MSFT    "
LOC = 42


def ev(typ="A", locate=LOC, side="B", shares=500, price=1_000_000, stock=STOCK, **kw):
    """An OUT event of a message of type typ."""
    fields = {"A": {"order_ref": 1, "side": side, "shares": shares, "stock": stock, "price": price},
              "F": {"order_ref": 1, "side": side, "shares": shares, "stock": stock, "price": price,
                    "attribution": "MPID"},
              "P": {"order_ref": 1, "side": side, "shares": shares, "stock": stock, "price": price, "match": 9},
              "E": {"order_ref": 1, "executed_shares": shares, "match": 9},
              "U": {"orig_ref": 1, "new_ref": 2, "shares": shares, "price": price},
              "C": {"order_ref": 1, "executed_shares": shares, "match": 9, "printable": "Y", "exec_price": price},
              "Q": {"shares": shares, "stock": stock, "cross_price": price, "match": 9,
                    "cross_type": "O"}}[typ]  # fmt: skip
    e, _ = expect(encode(typ, locate=locate, **fields, **kw), ItchConfig(), Bitmap())
    return e


# Matches ev() with its defaults: buy 500 @ 100.0000 on locate 42.
BASE = Rule(
    enable=True, msg_mask=0b111, locate=LOC, side_match=0b11, price_op=PriceOp.LE, price_threshold=1_000_000,
    min_shares=500, action_side=ActionSide.SELL, qty_mode=QtyMode.FIXED, qty=100, tif=ord("3"),
)  # fmt: skip


def rule(**kw):
    return Rule(**{**BASE.__dict__, **kw})


def armed(*rules):
    s = Strategy()
    for i, r in enumerate(rules):
        s.commit(i, r)
    s.arm((1 << N_RULES) - 1)
    return s


# ---- Static errors (decisions 6, 11, 16)

STATIC = [
    ("valid", {}, False),
    ("locate_0", {"locate": 0}, True),
    ("tif_gtt", {"tif": ord("6")}, True),
    ("tif_1", {"tif": ord("1")}, True),
    *[(f"tif_{c}", {"tif": ord(c)}, False) for c in "05E"],
    ("action_side_3", {"action_side": 3}, True),
    ("action_side_opposite", {"action_side": ActionSide.OPPOSITE}, False),
    ("qty_0_fixed", {"qty": 0}, True),
    ("qty_0_min", {"qty": 0, "qty_mode": QtyMode.MIN}, True),
    ("qty_1000000_fixed", {"qty": 1_000_000}, True),
    ("qty_999999_fixed", {"qty": 999_999}, False),
    ("qty_1000000_min", {"qty": 1_000_000, "qty_mode": QtyMode.MIN}, False),
    ("threshold_offset_max", {"price_mode": PriceMode.THRESHOLD, "price_threshold": PRICE_LIMIT_MAX - 5,
                              "price_offset": 5}, False),  # fmt: skip
    ("threshold_offset_over", {"price_mode": PriceMode.THRESHOLD, "price_threshold": PRICE_LIMIT_MAX - 5,
                               "price_offset": 6}, True),  # fmt: skip
    ("threshold_offset_1", {"price_mode": PriceMode.THRESHOLD, "price_threshold": 10, "price_offset": -9}, False),
    ("threshold_offset_0", {"price_mode": PriceMode.THRESHOLD, "price_threshold": 10, "price_offset": -10}, True),
    ("threshold_offset_wraps", {"price_mode": PriceMode.THRESHOLD, "price_threshold": 0xFFFFFFFF,
                                "price_offset": 0x7FFFFFFF}, True),  # fmt: skip
    ("event_price_ignores_offset", {"price_threshold": 10, "price_offset": -10}, False),
]


@pytest.mark.parametrize(("name", "change", "want"), STATIC, ids=[c[0] for c in STATIC])
def test_static_error(name, change, want):
    assert static_error(rule(**change)) is want


def test_reset_rules_are_flagged_and_disarmed():
    """A reset rule has locate 0 (decision 6), so every RULE_STATUS error bit is set until the host
    commits a valid rule."""
    s = Strategy()
    assert (s.error_bits(), s.armed) == (0xFF, 0)
    s.commit(3, BASE)
    assert s.error_bits() == 0xF7


def test_a_rule_with_a_static_error_never_fires():
    s = armed(rule(tif=ord("6")), BASE)
    assert s.error_bits() & 0b11 == 0b01
    assert s.message(ev(), 1, 0, 0) == 1


# ---- Matching

MATCH = [
    ("base", {}, {}, True),
    *[(f"type_{t}", {}, {"typ": t}, True) for t in "AFP"],
    *[(f"type_{t}", {}, {"typ": t}, False) for t in "EUCQ"],
    ("mask_without_A", {"msg_mask": 0b110}, {}, False),
    ("mask_F_only_F", {"msg_mask": 0b010}, {"typ": "F"}, True),
    ("mask_P_only_A", {"msg_mask": 0b100}, {}, False),
    ("locate_other", {}, {"locate": LOC + 1}, False),
    ("side_buy_only", {"side_match": 0b01}, {}, True),
    ("side_sell_only", {"side_match": 0b10}, {}, False),
    ("side_sell_on_sell", {"side_match": 0b10}, {"side": "S"}, True),
    ("side_none", {"side_match": 0}, {}, False),
    ("le_equal", {"price_op": PriceOp.LE}, {"price": 1_000_000}, True),
    ("le_above", {"price_op": PriceOp.LE}, {"price": 1_000_001}, False),
    ("ge_equal", {"price_op": PriceOp.GE}, {"price": 1_000_000}, True),
    ("ge_below", {"price_op": PriceOp.GE}, {"price": 999_999}, False),
    ("shares_below", {}, {"shares": 499}, False),
    ("shares_above", {}, {"shares": 501}, True),
]


@pytest.mark.parametrize(("name", "rchange", "echange", "want"), MATCH, ids=[c[0] for c in MATCH])
def test_matches(name, rchange, echange, want):
    assert matches(rule(**rchange), ev(**echange)) is want


def test_disabled_or_disarmed_rules_do_not_fire():
    s = armed(rule(enable=False))
    assert s.message(ev(), 1, 0, 0) is None
    s = Strategy()
    s.commit(0, BASE)
    assert s.message(ev(), 1, 0, 0) is None


# ---- The order


def fire(r, e=None):
    s = armed(r)
    assert s.message(e or ev(), 77, 1234, 9) == 0
    (o,) = s.packet(accepted=True)
    return o


def test_the_order_fields():
    o = fire(BASE)
    assert (o.source, o.rule, o.side, o.qty, o.price, o.locate, o.stock, o.tif) == (
        Source.RULE, 0, ord("S"), 100, 1_000_000, LOC, b"MSFT    ", ord("3"),
    )  # fmt: skip
    assert (o.seq, o.sof_ts, o.epoch, o.clordid) == (77, 1234, 9, None)


@pytest.mark.parametrize(
    ("action", "ev_side", "want"),
    [(ActionSide.BUY, "S", "B"), (ActionSide.SELL, "B", "S"), (ActionSide.OPPOSITE, "B", "S"),
     (ActionSide.OPPOSITE, "S", "B")],
)  # fmt: skip
def test_order_side(action, ev_side, want):
    assert fire(rule(action_side=action), ev(side=ev_side)).side == ord(want)


def test_opposite_of_a_side_that_is_not_b_or_s_is_0():
    assert fire(rule(action_side=ActionSide.OPPOSITE, side_match=0b11), ev(side="B")).side == ord("S")
    s = armed(rule(action_side=ActionSide.OPPOSITE))
    assert s.message(ev(side="X"), 1, 0, 0) is None  # side_match has no bit for X
    assert order_side(rule(action_side=ActionSide.OPPOSITE), ev(side="X")) == 0


@pytest.mark.parametrize(("qty", "shares", "want"), [(100, 500, 100), (600, 500, 500), (500, 500, 500)])
def test_qty_min_mode(qty, shares, want):
    assert fire(rule(qty_mode=QtyMode.MIN, qty=qty), ev(shares=shares)).qty == want


def test_qty_fixed_mode_ignores_shares():
    assert fire(rule(qty=7), ev(shares=900)).qty == 7


@pytest.mark.parametrize(("offset", "want"), [(0, 1_000_000), (-100, 999_900), (2500, 1_002_500)])
def test_price_threshold_mode(offset, want):
    r = rule(price_mode=PriceMode.THRESHOLD, price_offset=offset)
    assert fire(r, ev(price=900_000)).price == want


def test_price_event_mode():
    assert fire(rule(price_offset=-500), ev(price=900_000)).price == 900_000


# ---- Packets, priority, one-shot


def test_lowest_index_fires():
    s = armed(rule(qty=1), rule(qty=2), rule(qty=3))
    assert s.message(ev(), 1, 0, 0) == 0
    assert [o.qty for o in s.packet(True)] == [1]


def test_a_rule_fires_once_per_packet_and_the_next_one_takes_the_message():
    s = armed(rule(qty=1), rule(qty=2))
    assert [s.message(ev(), n, 0, 0) for n in (1, 2, 3)] == [0, 1, None]
    out = s.packet(True)
    assert [(o.rule, o.seq) for o in out] == [(0, 1), (1, 2)]
    assert s.armed == 0b1111_1100 and s.fired[:3] == [1, 1, 0]


def test_accepted_packet_disarms_only_the_fired_rules():
    s = armed(rule(), rule(locate=LOC + 1))
    s.message(ev(), 1, 0, 0)
    s.packet(True)
    assert s.armed & 0b11 == 0b10
    assert s.message(ev(), 2, 0, 0) is None  # one-shot: rule 0 is disarmed


def test_rejected_packet_drops_the_orders_and_keeps_the_rules_armed():
    s = armed(rule())
    assert s.message(ev(), 1, 0, 0) == 0
    assert s.packet(accepted=False) == []
    assert s.armed & 1 and s.fired[0] == 0
    assert s.message(ev(), 2, 0, 0) == 0  # fires again in the next packet
    assert [o.seq for o in s.packet(True)] == [2]


def test_disarm_or_commit_after_the_fire_does_not_cancel_the_order():
    """Decision 14."""
    s = armed(rule(qty=1), rule(qty=2))
    s.message(ev(), 1, 0, 0)
    s.message(ev(), 2, 0, 0)
    s.disarm(0b01)
    s.commit(1, rule(qty=9))
    assert [o.qty for o in s.packet(True)] == [1, 2]
    assert s.fired[:2] == [1, 1]


def test_commit_and_arm_after_the_fire_survive_the_acceptance():
    """Decision 18: the host's commands after the fire win over the one-shot disarm."""
    s = armed(rule(qty=1))
    s.message(ev(), 1, 0, 0)
    s.commit(0, rule(qty=2))
    s.arm(1)
    assert [o.qty for o in s.packet(True)] == [1]  # the old rule's order still goes (decision 14)
    assert s.armed & 1
    assert s.message(ev(), 2, 0, 0) == 0  # the new rule fires in the next packet
    assert [o.qty for o in s.packet(True)] == [2]
    assert s.armed & 1 == 0  # no command since this fire: one-shot


@pytest.mark.parametrize("command", ["arm", "disarm", "commit"])
def test_any_command_after_the_fire_takes_the_rule_out_of_the_acceptance_disarm(command):
    s = armed(rule(), rule())
    s.message(ev(), 1, 0, 0)
    s.message(ev(), 2, 0, 0)
    {"arm": lambda: s.arm(0b01), "disarm": lambda: s.disarm(0b01), "commit": lambda: s.commit(0, BASE)}[command]()
    assert len(s.packet(True)) == 2
    assert s.armed & 0b11 == (0b01 if command == "arm" else 0b00)  # rule 1: one-shot as usual


def test_a_rearmed_rule_still_fires_once_per_packet():
    s = armed(rule())
    assert s.message(ev(), 1, 0, 0) == 0
    s.arm(1)
    assert s.message(ev(), 2, 0, 0) is None
    assert len(s.packet(True)) == 1 and s.armed & 1


def test_a_rejected_packet_clears_the_acceptance_disarm():
    s = armed(rule())
    s.message(ev(), 1, 0, 0)
    s.packet(accepted=False)
    s.message(ev(), 2, 0, 0)
    s.arm(1)
    s.packet(True)
    assert s.armed & 1


def test_commit_disarms():
    """Decision 8."""
    s = armed(rule())
    s.commit(0, rule(qty=5))
    assert s.armed & 1 == 0
    assert s.message(ev(), 1, 0, 0) is None
    s.arm(1)
    assert s.message(ev(), 2, 0, 0) == 0


def test_arm_ignores_bits_above_the_rules():
    s = Strategy()
    s.arm(0x1FF)
    assert s.armed == 0xFF
