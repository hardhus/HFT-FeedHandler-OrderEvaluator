"""Simulator-free tests of the risk model (tb/model/risk.py); audit/14 §2.5, P6, P7, decisions 5, 7,
9, 12, 15. Expected reasons, token counts and cycles are written by hand here.
"""

import pytest

from model.order import USER_REF_MAX, OrderReq, Source, TxConfig, TxPort, template
from model.risk import BUDGET_MAX, Limits, Reason, Risk

LOC = 42
CFG = TxConfig(template())
OPEN = Limits(max_qty=1000, max_notional=1000 * 2_000_000, rate_refill=1, rate_period=10, rate_burst=4)


def req(**kw):
    base = {
        "source": Source.HOST, "side": ord("B"), "qty": 100, "price": 1_000_000, "locate": LOC,
        "stock": b"MSFT    ", "tif": ord("0"), "clordid": b"C1            ",
    }  # fmt: skip
    return OrderReq(**{**base, **kw})


def open_risk(limits=OPEN, budget=100, cycle=0):
    """Every check passes for req()."""
    r = Risk()
    r.commit(limits, cycle)
    r.allow.set(LOC)
    r.budget_add(budget)
    r.tx_en = True
    r.kill = False
    return r


def test_reset_state_rejects_with_kill():
    r, tx = Risk(), TxPort(CFG)
    assert (r.kill, r.tx_en, r.budget, r.tokens, r.limits) == (True, False, 0, 0, Limits())
    assert r.decide(req(), 0, tx) == Reason.KILL


# Each case breaks exactly one check of an otherwise open path. The setups are looked up by name:
# a function in the parameters would put its address in the test id, which differs between xdist
# workers (the loadgroup id carries the parameters' repr).
SETUPS = {
    "kill": lambda r, tx: setattr(r, "kill", True),
    "tx_off": lambda r, tx: setattr(r, "tx_en", False),
    "refs_exhausted": lambda r, tx: tx.write_user_ref_next(USER_REF_MAX + 1),
    "budget": lambda r, tx: setattr(r, "budget", 0),
    "rate": lambda r, tx: setattr(r, "tokens", 0),
    "tx_busy": lambda r, tx: [tx.accept(req()) for _ in range(8)],
}
SINGLE = [
    ("kill", {}, Reason.KILL),
    ("tx_off", {}, Reason.TX_OFF),
    ("invalid_price", {"price": 0}, Reason.INVALID),
    ("invalid_side", {"side": ord("T")}, Reason.INVALID),
    ("invalid_clordid", {"clordid": b"C-1           "}, Reason.INVALID),
    ("refs_exhausted", {}, Reason.INVALID),
    ("locate", {"locate": LOC + 1}, Reason.LOCATE),
    ("qty", {"qty": 1001}, Reason.QTY),
    ("notional", {"qty": 1000, "price": 2_000_001}, Reason.NOTIONAL),
    ("budget", {}, Reason.BUDGET),
    ("rate", {}, Reason.RATE),
    ("tx_busy", {}, Reason.TX_BUSY),
]


@pytest.mark.parametrize(("name", "change", "want"), SINGLE, ids=[c[0] for c in SINGLE])
def test_each_reason_alone(name, change, want):
    r, tx = open_risk(), TxPort(CFG)
    if name in SETUPS:
        SETUPS[name](r, tx)
    assert r.decide(req(**change), 0, tx) == want
    assert r.rejects[want] == 1 and r.accepted == 0


def test_priority_order():
    """All checks broken at once, then fixed one by one from the top: the reason walks down the
    order of decision 9."""
    r, tx = open_risk(), TxPort(CFG)
    r.kill, r.tx_en, r.budget, r.tokens = True, False, 0, 0
    r.allow.set(LOC, False)
    for _ in range(8):
        tx.queue.append(req())
    o = req(price=0, qty=2000)
    fixes = [
        (Reason.KILL, lambda: setattr(r, "kill", False)),
        (Reason.TX_OFF, lambda: setattr(r, "tx_en", True)),
        (Reason.INVALID, None),
        (Reason.LOCATE, lambda: r.allow.set(LOC)),
        (Reason.QTY, None),
        (Reason.NOTIONAL, None),
        (Reason.BUDGET, lambda: r.budget_add(1)),
        (Reason.RATE, lambda: setattr(r, "tokens", 1)),
        (Reason.TX_BUSY, lambda: tx.queue.pop()),
    ]
    stages = {Reason.INVALID: {"price": 3_000_000}, Reason.QTY: {"qty": 1000}, Reason.NOTIONAL: {"price": 1}}
    for want, fix in fixes:
        assert r.reason(o, 0, tx) == want
        if fix:
            fix()
        o = req(**{**o.__dict__, **stages.get(want, {})})
    assert r.decide(o, 0, tx) == Reason.NONE


@pytest.mark.parametrize(("price", "want"), [(1_999_999, Reason.NONE), (2_000_000, Reason.NONE),
                                             (2_000_001, Reason.NOTIONAL)])  # fmt: skip
def test_notional_boundary(price, want):
    r = open_risk()
    assert r.decide(req(qty=1000, price=price), 0, TxPort(CFG)) == want


def test_notional_is_64_bit():
    lim = Limits(max_qty=999_999, max_notional=999_999 * 0x7735939C, rate_period=0, rate_burst=1)
    r = open_risk(lim)
    assert r.decide(req(qty=999_999, price=0x7735939C), 0, TxPort(CFG)) == Reason.NONE
    r = open_risk(Limits(max_qty=999_999, max_notional=999_999 * 0x7735939C - 1, rate_burst=1))
    assert r.decide(req(qty=999_999, price=0x7735939C), 0, TxPort(CFG)) == Reason.NOTIONAL


def test_accepted_order_spends_budget_and_a_token_rejected_spends_nothing():
    r, tx = open_risk(budget=2), TxPort(CFG)
    assert r.decide(req(), 0, tx) == Reason.NONE
    assert (r.budget, r.tokens, len(tx.queue)) == (1, 3, 1)
    assert r.decide(req(qty=5000), 0, tx) == Reason.QTY
    assert (r.budget, r.tokens, len(tx.queue)) == (1, 3, 1)
    assert r.decide(req(), 0, tx) == Reason.NONE
    assert r.decide(req(), 0, tx) == Reason.BUDGET
    assert (r.budget, r.tokens, r.accepted) == (0, 2, 2)


def test_budget_add_saturates():
    r = Risk()
    r.budget_add(BUDGET_MAX - 1)
    r.budget_add(5)
    assert r.budget == BUDGET_MAX


# ---- Token bucket (decision 15): burst 4, +1 every 10 cycles from the commit at cycle 100


def drain(r, tx, cycle, n):
    return [r.decide(req(), cycle, tx) for _ in range(n)]


def test_commit_fills_the_bucket():
    r, tx = open_risk(cycle=100), TxPort(CFG)
    assert drain(r, tx, 100, 5) == [Reason.NONE] * 4 + [Reason.RATE]


def test_refill_ticks_on_exact_cycles():
    r, tx = open_risk(cycle=100), TxPort(CFG)
    drain(r, tx, 100, 4)
    tx.queue.clear()
    assert r.decide(req(), 109, tx) == Reason.RATE
    assert r.decide(req(), 110, tx) == Reason.NONE
    assert r.decide(req(), 119, tx) == Reason.RATE
    assert r.decide(req(), 120, tx) == Reason.NONE


def test_refill_saturates_at_burst():
    r, tx = open_risk(cycle=100), TxPort(CFG)
    drain(r, tx, 100, 1)
    assert r.tokens_at(1000) == 4
    assert drain(r, tx, 1000, 5) == [Reason.NONE] * 4 + [Reason.RATE]


def test_refill_adds_rate_refill_per_tick():
    r = open_risk(Limits(max_qty=1000, max_notional=1 << 63, rate_refill=3, rate_period=7, rate_burst=10))
    tx = TxPort(CFG)
    for _ in range(10):
        r.decide(req(), 0, tx)
        tx.queue.clear()
    assert r.tokens_at(6) == 0
    assert r.tokens_at(7) == 3
    assert r.tokens_at(20) == 6
    assert r.tokens_at(21) == 9
    assert r.tokens_at(28) == 10


def test_period_zero_never_refills():
    r = open_risk(Limits(max_qty=1000, max_notional=1 << 63, rate_refill=5, rate_period=0, rate_burst=2))
    tx = TxPort(CFG)
    assert drain(r, tx, 0, 3) == [Reason.NONE, Reason.NONE, Reason.RATE]
    assert r.tokens_at(10**9) == 0


def test_a_new_commit_refills_and_restarts_the_period():
    r, tx = open_risk(cycle=100), TxPort(CFG)
    drain(r, tx, 105, 4)
    tx.queue.clear()
    r.commit(OPEN, 115)
    assert r.tokens == 4
    drain(r, tx, 115, 4)
    tx.queue.clear()
    assert r.tokens_at(124) == 0  # the old period would have ticked at 120
    assert r.tokens_at(125) == 1


# ---- tx_en (decision 7)


def test_tx_en_fall_cancels_the_queue_as_tx_off():
    r, tx = open_risk(), TxPort(CFG)
    reqs = [req(qty=q) for q in (1, 2, 3)]
    for o in reqs:
        r.decide(o, 0, tx)
    assert r.set_tx_en(False, tx) == reqs
    assert r.rejects[Reason.TX_OFF] == 3 and not tx.queue
    assert r.decide(req(), 0, tx) == Reason.TX_OFF
    assert r.set_tx_en(True, tx) == []
    assert r.decide(req(), 0, tx) == Reason.NONE
