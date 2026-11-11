"""order_stats: order path counters (audit/14 §2.8, decision 30).

Every cycle the testbench compares all counters with a Python model, so the latency (an event is in cnt
one cycle after the edge that samples it) is checked with exact equality. Inputs are random each cycle:
a released order, an ORDER_SENT, a RISK_REJECT and a lost reject, independently, so every combination
occurs, the same reason on both reject inputs included. Invalid inputs and out-of-enum sources and
reasons carry random fields and must count nowhere. The model indexes counters by a hand-written table.
"""

import os
import random

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from common.clocks import start_clock
from common.runner import run_cocotb

PERIOD_PS = 6400  # net_clk, 156.25 MHz
# Hand-written counter index (RISK and STRATEGY blocks): RULE_FIRED_<n>, ORDERS_SENT_*, RISK_REJECT_<reason>
# (reason codes 1..10 of risk_reason_e), RING_DROP_ORDERS.
FIRED = list(range(8))
SENT = {1: 8, 2: 9}  # order_src_e: RULE, HOST
REJ = {
    1: 10, 2: 11, 3: 12, 4: 13, 5: 14,  # KILL TX_OFF INVALID LOCATE QTY
    6: 15, 7: 16, 8: 17, 9: 18, 10: 19,  # NOTIONAL BUDGET RATE TX_BUSY RING_FULL
}  # fmt: skip
DROP = 20
N_CNT = 21
U64 = (1 << 64) - 1


def cnt_w() -> int:
    return int(os.environ["CNT_W"])


class Stats:
    """Drives the inputs and checks cnt against the model on every clock edge."""

    def __init__(self, dut):
        self.dut = dut
        self.mask = (1 << cnt_w()) - 1
        self.model = [0] * N_CNT
        # Events per counter since the last reset, unmasked: at CNT_W=8 a counter that moved 256 times
        # reads 0 again, so "did it move" is asked here, not of the model.
        self.events = [0] * N_CNT
        self.cycles = 0
        cocotb.start_soon(self._check())

    def read(self) -> list[int]:
        raw = int(self.dut.cnt.value)
        return [(raw >> (i * cnt_w())) & self.mask for i in range(N_CNT)]

    async def _check(self) -> None:
        while True:
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            got = self.read()
            assert got == self.model, f"cycle {self.cycles}: cnt {got} != model {self.model}"
            self.cycles += 1

    async def cycle(self, fire: int | None = None, sent: int | None = None, rej: int | None = None,
                    lost: int | None = None) -> None:  # fmt: skip
        """One cycle. Each argument is a rule, source or reason code (any value of the field width), or None
        for an invalid input with a random field. The model takes it at the edge that samples it; rst is
        set by the test before the call."""
        d = self.dut
        d.fire_valid.value = int(fire is not None)
        d.fire_rule.value = fire if fire is not None else random.getrandbits(3)
        d.sent_valid.value = int(sent is not None)
        d.sent_source.value = sent if sent is not None else random.getrandbits(2)
        d.rej_valid.value = int(rej is not None)
        d.rej_reason.value = rej if rej is not None else random.getrandbits(4)
        d.lost_valid.value = int(lost is not None)
        d.lost_reason.value = lost if lost is not None else random.getrandbits(4)
        await RisingEdge(d.clk)
        d.fire_valid.value = 0
        d.sent_valid.value = 0
        d.rej_valid.value = 0
        d.lost_valid.value = 0
        if d.rst.value:
            self.model = [0] * N_CNT
            self.events = [0] * N_CNT
            return
        inc = [0] * N_CNT
        if fire is not None:
            inc[FIRED[fire]] += 1
        if sent in SENT:
            inc[SENT[sent]] += 1
        if rej in REJ:
            inc[REJ[rej]] += 1
        if lost is not None:
            inc[DROP] += 1
            if lost in REJ:
                inc[REJ[lost]] += 1
        self.model = [(c + i) & self.mask for c, i in zip(self.model, inc, strict=True)]
        self.events = [e + i for e, i in zip(self.events, inc, strict=True)]

    async def sample(self) -> list[int]:
        await FallingEdge(self.dut.clk)
        return self.read()

    def deposit(self, values: list[int]) -> None:
        """Backdoor write of the counters (cnt_q) and the model, between edges."""
        raw = 0
        for i, v in enumerate(values):
            raw |= (v & self.mask) << (i * cnt_w())
        self.dut.cnt_q.value = raw
        self.model = [v & self.mask for v in values]


async def start(dut) -> Stats:
    """Idle inputs and reset first, then the clock (no edge with undriven inputs)."""
    for s in ("fire_valid", "fire_rule", "sent_valid", "sent_source", "rej_valid", "rej_reason", "lost_valid",
              "lost_reason"):  # fmt: skip
        getattr(dut, s).value = 0
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    for _ in range(3):
        await RisingEdge(dut.clk)
    st = Stats(dut)
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    return st


def opt(p: float, values) -> int | None:
    return random.choice(values) if random.random() < p else None


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_counts(dut):
    """Random traffic: each input valid with probability 0.5, any field value (out-of-enum sources 0
    and 3, reasons 0 and 11..15 included), the same reason on both reject inputs in some cycles."""
    st = await start(dut)
    same = 0
    for _ in range(4000):
        rej = opt(0.5, range(16))
        lost = rej if rej is not None and random.random() < 0.2 else opt(0.5, range(16))
        same += rej is not None and rej == lost and rej in REJ
        await st.cycle(fire=opt(0.5, range(8)), sent=opt(0.5, range(4)), rej=rej, lost=lost)
    await st.sample()
    assert same > 20, f"only {same} cycles with one reason on both reject inputs"
    assert all(e > 0 for e in st.events), f"a counter never moved: events {st.events}"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_every_index(dut):
    """Each rule, source and reason alone moves exactly its own counter (the hand-written table); a lost
    reject moves its reason and RING_DROP_ORDERS; out-of-enum values move nothing (a lost one only
    RING_DROP_ORDERS)."""
    st = await start(dut)
    cases = [({"fire": n}, {FIRED[n]: 1}) for n in range(8)]
    cases += [({"sent": s}, {SENT[s]: 1}) for s in SENT] + [({"sent": s}, {}) for s in (0, 3)]
    cases += [({"rej": r}, {REJ[r]: 1}) for r in REJ] + [({"rej": r}, {}) for r in (0, 11, 15)]
    cases += [({"lost": r}, {REJ[r]: 1, DROP: 1}) for r in REJ] + [({"lost": r}, {DROP: 1}) for r in (0, 12)]
    cases += [({"rej": r, "lost": r}, {REJ[r]: 2, DROP: 1}) for r in (1, 10)]
    for kw, want in cases:
        before = await st.sample()
        await st.cycle(**kw)
        after = await st.sample()
        moved = {i: after[i] - before[i] for i in range(N_CNT) if after[i] != before[i]}
        assert moved == want, f"{kw}: moved {moved}, expected {want}"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_reset(dut):
    """rst clears every counter; the inputs of a reset cycle are ignored."""
    st = await start(dut)
    for _ in range(50):
        await st.cycle(fire=opt(0.7, range(8)), sent=opt(0.7, (1, 2)), rej=opt(0.7, range(1, 11)),
                       lost=opt(0.7, range(1, 11)))  # fmt: skip
    assert any(await st.sample())
    dut.rst.value = 1
    await st.cycle(fire=3, sent=1, rej=4, lost=4)  # ignored
    dut.rst.value = 0
    assert await st.sample() == [0] * N_CNT
    await st.cycle(rej=7)
    assert (await st.sample())[REJ[7]] == 1


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_wrap(dut):
    """At the counter limit (CNT_W = 8 build only): 2**CNT_W + 3 events on every counter wrap each once;
    a reason getting 2 in a cycle crosses the limit by 2 (from limit - 1 to 1)."""
    assert cnt_w() == 8, "tc_wrap runs in the CNT_W = 8 build"
    st = await start(dut)
    limit = st.mask + 1
    for i in range(limit + 3):
        await st.cycle(fire=i % 8, sent=1 + i % 2, rej=1 + i % 10, lost=1 + (i + 5) % 10)
    await st.sample()
    st.deposit([limit - 1] * N_CNT)
    await st.cycle(rej=5, lost=5)
    got = await st.sample()
    assert got[REJ[5]] == 1 and got[DROP] == 0, f"wrap by 2: {got[REJ[5]]}, drop {got[DROP]}"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_carry_64(dut):
    """The 64-bit adders: the carry into bit 32 and the wrap at 2**64. The test deposits every counter at
    2**32 - 1 and then 2**64 - 1; one cycle with every input (one reason on both reject inputs) gives
    2**32 (2**32 + 1 for that reason) and 0 (1)."""
    assert cnt_w() == 64, "tc_carry_64 runs in the CNT_W = 64 build"
    st = await start(dut)
    for base, want, want2 in (((1 << 32) - 1, 1 << 32, (1 << 32) + 1), (U64, 0, 1)):
        await st.sample()
        st.deposit([base] * N_CNT)
        for n in range(8):
            await st.cycle(fire=n, sent=1 + n % 2, rej=None if n else 6, lost=6 if not n else None)
        got = await st.sample()
        assert got[REJ[6]] == want2 and got[FIRED[0]] == want and got[DROP] == want, (base, got)


# Tests that only make sense at one counter width: name -> CNT_W.
WIDTH_TESTS = {"tc_wrap": 8, "tc_carry_64": 64}


def directed_tests(width: int) -> list[str]:
    """Every tc_* of this module that fits this CNT_W."""
    return [name for name in globals() if name.startswith("tc_") and WIDTH_TESTS.get(name, width) == width]


# CNT_W: 64 (the product value) and 8 (every counter reaches its limit in a short test).
@pytest.mark.parametrize("cnt_w", [64, 8])
def test_order_stats(cnt_w):
    run_cocotb(
        toplevel="order_stats",
        filelists=["common.f", "core.f"],
        test_module="test_order_stats",
        parameters={"CNT_W": cnt_w},
        testcase=directed_tests(cnt_w),
        extra_env={"CNT_W": cnt_w},
    )
