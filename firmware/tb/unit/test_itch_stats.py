"""itch_stats: ITCH counters for committed messages (audit/12 §2.6).

Every cycle the testbench compares all counters with a Python model, so the latency (a packet's
results are in cnt one cycle after its pkt_valid) is checked with exact equality, not eventually.
The model keeps its own pending counts: a result waits for its packet event, which adds the
pending counts for MOLD_OK, MOLD_HEARTBEAT and MOLD_EOS and drops them otherwise; a result in the
same cycle as a packet event belongs to the next packet. Idle cycles carry random fields. The
model indexes counters by class and outcome; tc_every_class_outcome checks that order against a
hand-written table, and the other directed tests use hand-written values.
"""

import os
import random

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from common.clocks import start_clock
from common.runner import run_cocotb
from model.itch import ItchClass, Outcome
from model.mold import ACCEPTED, MoldStatus

PERIOD_PS = 6400  # net_clk, 156.25 MHz
# Hand-written counter index of each class and outcome (ITCH block order).
INDEX = {
    "ORDER": 0, "TRADE": 1, "ADMIN": 2, "AUCTION": 3, "UNKNOWN": 4, "MALFORMED": 5,
    "OUT": 6, "FILTERED": 7, "MASKED": 8,
}  # fmt: skip
N_CNT = 9
N_CLASS = 6
PEND_MOD = 1 << 16  # pending counts: 16 bits, as pkt_msgs
U64 = (1 << 64) - 1

Res = tuple[ItchClass, Outcome]


def cnt_w() -> int:
    return int(os.environ["CNT_W"])


def rand_res() -> Res:
    return random.choice(list(ItchClass)), random.choice(list(Outcome))


class Stats:
    """Drives res_* and pkt_* and checks cnt against the model on every clock edge."""

    def __init__(self, dut):
        self.dut = dut
        self.mask = (1 << cnt_w()) - 1
        self.model = [0] * N_CNT
        self.pend = [0] * N_CNT
        self.balance = 0  # sum(classes) - sum(outcomes): 0, or what a deposit set
        self.cycles = 0
        cocotb.start_soon(self._check())

    def apply(self, res: Res | None, pkt: MoldStatus | None) -> None:
        inc = [0] * N_CNT
        if res is not None:
            inc[INDEX[res[0].name]] = 1
            inc[INDEX[res[1].name]] = 1
        if pkt is not None:
            if pkt in ACCEPTED:
                self.model = [(c + p) & self.mask for c, p in zip(self.model, self.pend, strict=True)]
            self.pend = inc
        else:
            self.pend = [(p + i) % PEND_MOD for p, i in zip(self.pend, inc, strict=True)]

    def read(self) -> list[int]:
        """cnt now; call it where the value is settled (ReadOnly, or after sample())."""
        raw = int(self.dut.cnt.value)
        return [(raw >> (i * cnt_w())) & self.mask for i in range(N_CNT)]

    async def _check(self) -> None:
        while True:
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            got = self.read()
            assert got == self.model, f"cycle {self.cycles}: cnt {got} != model {self.model}"
            # every result moves one class and one outcome counter
            assert (sum(got[:N_CLASS]) - sum(got[N_CLASS:]) - self.balance) % (self.mask + 1) == 0
            self.cycles += 1

    async def cycle(self, res: Res | None = None, pkt: MoldStatus | None = None) -> None:
        """One cycle: a result, a packet event, both or neither. The model takes it at the edge
        that samples it. rst is set by the test before the call and changed only after it, so its
        value here is the one that edge saw. Invalid inputs carry random fields."""
        d = self.dut
        rc, ro = res if res is not None else (random.getrandbits(3), random.getrandbits(2))
        d.res_valid.value = int(res is not None)
        d.res_class.value = int(rc)
        d.res_outcome.value = int(ro)
        d.pkt_valid.value = int(pkt is not None)
        d.pkt_status.value = int(pkt) if pkt is not None else random.getrandbits(4)
        await RisingEdge(d.clk)
        d.res_valid.value = 0
        d.pkt_valid.value = 0
        if d.rst.value:
            self.model = [0] * N_CNT
            self.pend = [0] * N_CNT
            self.balance = 0
        else:
            self.apply(res, pkt)

    async def packet(self, results: list[Res], status: MoldStatus) -> None:
        """A packet's results on consecutive cycles, then its event in the next cycle."""
        for r in results:
            await self.cycle(res=r)
        await self.cycle(pkt=status)

    async def idle(self, n: int = 1) -> None:
        for _ in range(n):
            await self.cycle()

    async def sample(self) -> list[int]:
        """cnt in the middle of the cycle (settled after the last edge); drive again afterwards."""
        await FallingEdge(self.dut.clk)
        return self.read()

    def deposit(self, values: list[int]) -> None:
        """Backdoor write of the counters (cnt_q; the model too), between edges with no packet
        event. Verilator keeps the top-level cnt port apart from the register: a write to the
        port does not hold."""
        raw = 0
        for i, v in enumerate(values):
            raw |= (v & self.mask) << (i * cnt_w())
        self.dut.cnt_q.value = raw
        self.model = [v & self.mask for v in values]
        self.balance = sum(self.model[:N_CLASS]) - sum(self.model[N_CLASS:])


async def start(dut) -> Stats:
    """Idle inputs and reset first, then the clock (no edge with undriven inputs)."""
    dut.res_valid.value = 0
    dut.res_class.value = 0
    dut.res_outcome.value = 0
    dut.pkt_valid.value = 0
    dut.pkt_status.value = 0
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    for _ in range(3):
        await RisingEdge(dut.clk)
    st = Stats(dut)
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    return st


def moved(before: list[int], after: list[int]) -> dict[str, int]:
    """Counters that changed, by name, with their step."""
    names = {i: n for n, i in INDEX.items()}
    return {names[i]: after[i] - before[i] for i in range(N_CNT) if after[i] != before[i]}


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_counts(dut):
    """Random traffic: packets of 0..20 results with every status, idle gaps, and packet events
    in the same cycle as the next packet's first result (IPG 0)."""
    st = await start(dut)
    for _ in range(600):
        n = random.choice([0, 1, random.randint(2, 20)])
        status = random.choice(list(MoldStatus))
        for _ in range(n):
            if random.random() < 0.2:
                await st.idle(random.randint(1, 3))
            await st.cycle(res=rand_res())
        if random.random() < 0.4:
            await st.cycle(res=rand_res(), pkt=status)  # the result starts the next packet
        else:
            if random.random() < 0.3:
                await st.idle(random.randint(1, 3))
            await st.cycle(pkt=status)
    await st.cycle(pkt=MoldStatus.OK)
    await st.idle(2)
    assert any(st.model), "some packets were committed"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_every_class_outcome(dut):
    """One result per class and outcome in its own OK packet: only that class's and that
    outcome's counters move, by 1 (hand-written indices)."""
    st = await start(dut)
    for cls in ItchClass:
        for out in Outcome:
            before = await st.sample()
            await st.packet([(cls, out)], MoldStatus.OK)
            after = await st.sample()
            assert moved(before, after) == {cls.name: 1, out.name: 1}, f"{cls.name}/{out.name}"
    assert set(INDEX) == {c.name for c in ItchClass} | {o.name for o in Outcome}
    assert len(INDEX) == N_CNT == len(ItchClass) + len(Outcome)


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_every_status(dut):
    """A packet of 2 ORDER/OUT, 3 TRADE/FILTERED and 1 MALFORMED/MASKED results per status:
    OK, HEARTBEAT and EOS add exactly those counts, every other status adds nothing. The next
    OK packet adds only its own result: dropped results do not stay pending."""
    st = await start(dut)
    results = [(ItchClass.ORDER, Outcome.OUT)] * 2 + [(ItchClass.TRADE, Outcome.FILTERED)] * 3
    results += [(ItchClass.MALFORMED, Outcome.MASKED)]
    random.shuffle(results)
    accepted = {"OK", "HEARTBEAT", "EOS"}
    want = {"ORDER": 2, "OUT": 2, "TRADE": 3, "FILTERED": 3, "MALFORMED": 1, "MASKED": 1}
    for status in MoldStatus:
        before = await st.sample()
        await st.packet(results, status)
        mid = await st.sample()
        assert moved(before, mid) == (want if status.name in accepted else {}), status.name
        await st.packet([(ItchClass.AUCTION, Outcome.OUT)], MoldStatus.OK)
        after = await st.sample()
        assert moved(mid, after) == {"AUCTION": 1, "OUT": 1}, f"after {status.name}"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_ipg0_boundary(dut):
    """A result in the same cycle as a packet event belongs to the next packet (§2.6): it is
    neither added by that event nor dropped by it. Hand-written counts."""
    st = await start(dut)
    o, t, a = (ItchClass.ORDER, Outcome.OUT), (ItchClass.TRADE, Outcome.MASKED), (ItchClass.ADMIN, Outcome.FILTERED)
    # accepted event with the next packet's result, then that packet is dropped
    await st.cycle(res=o)
    await st.cycle(res=o)
    await st.cycle(res=t, pkt=MoldStatus.OK)
    await st.cycle(pkt=MoldStatus.DUP)
    got = await st.sample()
    assert moved([0] * N_CNT, got) == {"ORDER": 2, "OUT": 2}
    # dropped event with the next packet's result, then that packet is accepted
    await st.cycle(res=t)
    await st.cycle(res=a, pkt=MoldStatus.DISABLED)
    await st.cycle(pkt=MoldStatus.EOS)
    after = await st.sample()
    assert moved(got, after) == {"ADMIN": 1, "FILTERED": 1}
    # a result with every event, back to back: each event takes the previous cycle's result
    seq = [MoldStatus.OK, MoldStatus.ABORT, MoldStatus.HEARTBEAT, MoldStatus.RUNT, MoldStatus.OK, MoldStatus.OK]
    before = after
    for s in seq:
        await st.cycle(res=o, pkt=s)
    await st.cycle(pkt=MoldStatus.SESSION)  # drops the last one
    after = await st.sample()
    # results of events 1..5 are committed by events 2..6: kept by HEARTBEAT, OK, OK (3)
    assert moved(before, after) == {"ORDER": 3, "OUT": 3}
    # empty packets add nothing
    before = after
    for s in MoldStatus:
        await st.cycle(pkt=s)
    assert await st.sample() == before


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_reset(dut):
    """rst clears the counters and the pending counts, also in the middle of a packet; the
    inputs of a reset cycle are ignored."""
    st = await start(dut)
    for _ in range(30):
        await st.packet([rand_res() for _ in range(random.randint(1, 5))], MoldStatus.OK)
    assert any(await st.sample())
    for _ in range(4):
        await st.cycle(res=(ItchClass.TRADE, Outcome.OUT))  # pending at the reset
    dut.rst.value = 1
    await st.cycle(res=(ItchClass.ORDER, Outcome.OUT), pkt=MoldStatus.OK)  # ignored
    dut.rst.value = 0
    assert await st.sample() == [0] * N_CNT
    await st.cycle(res=(ItchClass.UNKNOWN, Outcome.MASKED))
    await st.cycle(pkt=MoldStatus.OK)
    got = await st.sample()
    assert moved([0] * N_CNT, got) == {"UNKNOWN": 1, "MASKED": 1}


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_wrap(dut):
    """At and beyond the counter limit (CNT_W = 8 build only): 2**CNT_W + 2 results per class in
    packets of up to 300 (a pending count above the counter range), outcomes in turn. Each class
    counter wraps once and counts 2 more; each outcome counter gets 6 * (2**CNT_W + 2) / 3."""
    assert cnt_w() == 8, "tc_wrap runs in the CNT_W = 8 build"
    st = await start(dut)
    limit = st.mask + 1
    results = [(c, Outcome(i % 3)) for c in ItchClass for i in range(limit + 2)]
    random.shuffle(results)
    while results:
        n = random.choice([1, 300, random.randint(2, 40)])
        await st.packet(results[:n], MoldStatus.OK)
        results = results[n:]
    got = await st.sample()
    assert got[:N_CLASS] == [2] * N_CLASS, "each class counter wrapped once and counted 2 more"
    assert got[N_CLASS:] == [(2 * (limit + 2)) % limit] * 3


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_carry_64(dut):
    """The 64-bit adders: the carry into bit 32 and the wrap at 2**64. One result per cycle
    cannot reach 2**32 in a simulation, so the test deposits the counters (and the model) at
    2**32 - 2 (classes) and 2**64 - 3 (outcomes); one packet of 3 results per class, outcomes in
    turn (6 each), gives 2**32 + 1 and 3 (hand-written)."""
    assert cnt_w() == 64, "tc_carry_64 runs in the CNT_W = 64 build"
    st = await start(dut)
    await st.idle(1)
    st.deposit([(1 << 32) - 2] * N_CLASS + [U64 - 2] * 3)
    await st.idle(2)  # the deposit holds without a packet event
    assert await st.sample() == [(1 << 32) - 2] * N_CLASS + [U64 - 2] * 3
    results = [(c, Outcome(i % 3)) for c in ItchClass for i in range(3)]
    await st.packet(results, MoldStatus.HEARTBEAT)
    assert await st.sample() == [(1 << 32) + 1] * N_CLASS + [3] * 3


@cocotb.test(timeout_time=300, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_pend_limit(dut):
    """The 16-bit pending counts at their limit (CNT_W = 64): a packet of 65535 results adds
    65535, also when its event comes with the next packet's first result (the result on the full
    pending count is legal then). More results per packet break the input contract; the
    itch_stats_props assertion fires on them (test_sva_checkers)."""
    assert cnt_w() == 64, "tc_pend_limit runs in the CNT_W = 64 build"
    st = await start(dut)
    await st.packet([(ItchClass.ORDER, Outcome.OUT)] * 0xFFFF, MoldStatus.OK)
    got = await st.sample()
    assert moved([0] * N_CNT, got) == {"ORDER": 0xFFFF, "OUT": 0xFFFF}
    for _ in range(0xFFFF):
        await st.cycle(res=(ItchClass.TRADE, Outcome.MASKED))
    await st.cycle(res=(ItchClass.ADMIN, Outcome.FILTERED), pkt=MoldStatus.EOS)
    await st.cycle(pkt=MoldStatus.HEARTBEAT)
    after = await st.sample()
    assert moved(got, after) == {"TRADE": 0xFFFF, "MASKED": 0xFFFF, "ADMIN": 1, "FILTERED": 1}


# The long test runs as its own pytest entry (parallel worker, shared build).
LONG_TESTS = ("tc_pend_limit",)
# Tests that only make sense at one counter width: name -> CNT_W.
WIDTH_TESTS = {"tc_wrap": 8, "tc_carry_64": 64, "tc_pend_limit": 64}


def directed_tests(width: int) -> list[str]:
    """Every tc_* of this module that is not a long test and fits this CNT_W."""
    return [
        name for name in globals()
        if name.startswith("tc_") and name not in LONG_TESTS and WIDTH_TESTS.get(name, width) == width
    ]  # fmt: skip


# CNT_W: 64 (the product value) and 8 (every counter reaches its limit in a short test).
@pytest.mark.parametrize("group, cnt_w", [("directed", 64), ("directed", 8), *((n, WIDTH_TESTS[n]) for n in LONG_TESTS)])
def test_itch_stats(group, cnt_w):
    run_cocotb(
        toplevel="itch_stats",
        filelists=["common.f", "core.f"],
        test_module="test_itch_stats",
        parameters={"CNT_W": cnt_w},
        testcase=directed_tests(cnt_w) if group == "directed" else group,
        extra_env={"GROUP": group, "CNT_W": cnt_w},
    )
