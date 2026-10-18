"""rx_stats: receive counter bank (audit/10 §2.4).

Every cycle the testbench compares all counters with a Python model, so the latency (an event is
in cnt one cycle after its stat_valid) is checked with exact equality, not eventually. The model
indexes drops by 1 + reason; tc_every_reason checks that order against a hand-written table.
"""

import os
import random

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from common.clocks import start_clock
from common.runner import run_cocotb
from model.net import RxReason

PERIOD_PS = 6400  # net_clk, 156.25 MHz
N_CNT = 11
PKTS, BYTES = 0, 1
# Hand-written counter index of each drop reason (STATS block order: DROP_DISABLED first).
DROP_INDEX = {"DISABLED": 2, "FCS": 3, "MAC": 4, "ETHERTYPE": 5, "IP_HDR": 6, "FRAG": 7, "PROTO": 8, "FILTER": 9, "LEN": 10}
STAT_BYTES_MAX = 0xFFFF


def cnt_w() -> int:
    return int(os.environ["CNT_W"])


class Stats:
    """Drives stat_* and checks cnt against the model on every clock edge."""

    def __init__(self, dut):
        self.dut = dut
        self.mask = (1 << cnt_w()) - 1
        self.model = [0] * N_CNT
        self.cycles = 0
        cocotb.start_soon(self._check())

    def apply(self, reason: RxReason, nbytes: int) -> None:
        self.model[PKTS] = (self.model[PKTS] + 1) & self.mask
        self.model[BYTES] = (self.model[BYTES] + nbytes) & self.mask
        if reason != RxReason.OK:
            self.model[1 + reason] = (self.model[1 + reason] + 1) & self.mask

    def read(self) -> list[int]:
        """cnt now; call it where the value is settled (ReadOnly, or after sample())."""
        raw = int(self.dut.cnt.value)
        return [(raw >> (i * cnt_w())) & self.mask for i in range(N_CNT)]

    async def _check(self) -> None:
        while True:
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            assert self.read() == self.model, f"cycle {self.cycles}: cnt {self.read()} != model {self.model}"
            self.cycles += 1

    async def event(self, reason: RxReason, nbytes: int) -> None:
        """One stat_valid cycle; the model takes it at the edge that samples it."""
        await self.drive(True, reason, nbytes)

    async def sample(self) -> list[int]:
        """cnt in the middle of the cycle (settled after the last edge); drive again afterwards."""
        await FallingEdge(self.dut.clk)
        return self.read()

    async def idle(self, n: int = 1) -> None:
        for _ in range(n):
            await self.drive(False, RxReason.OK, 0)

    async def drive(self, valid: bool, reason: RxReason, nbytes: int) -> None:
        """Present one cycle of stat_* and update the model at the edge that samples it. rst is
        set by the test before the call and changed only after it, so its value here is the one
        that edge saw. stat_valid drops right after the edge: one call is exactly one event."""
        d = self.dut
        d.stat_valid.value = int(valid)
        d.stat_reason.value = int(reason)
        d.stat_bytes.value = nbytes
        await RisingEdge(d.clk)
        d.stat_valid.value = 0
        if d.rst.value:
            self.model = [0] * N_CNT
        elif valid:
            self.apply(reason, nbytes)


async def start(dut) -> Stats:
    """Idle inputs and reset first, then the clock (no edge with undriven inputs)."""
    dut.stat_valid.value = 0
    dut.stat_reason.value = 0
    dut.stat_bytes.value = 0
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    for _ in range(3):
        await RisingEdge(dut.clk)
    st = Stats(dut)
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    return st


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_counts(dut):
    """Random events, back to back and with gaps, every reason and byte count (0, max, random)."""
    st = await start(dut)
    for _ in range(3000):
        if random.random() < 0.3:
            await st.idle(random.randint(1, 3))
        nbytes = random.choice([0, STAT_BYTES_MAX, random.randint(0, STAT_BYTES_MAX)])
        await st.event(random.choice(list(RxReason)), nbytes)
    await st.idle(2)
    assert st.model[PKTS] == 3000 % (st.mask + 1)


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_every_reason(dut):
    """One event per reason: only its own drop counter moves; OK moves no drop counter."""
    st = await start(dut)
    for reason in RxReason:
        before = await st.sample()
        await st.event(reason, 100)
        after = await st.sample()
        moved = {i for i in range(N_CNT) if after[i] != before[i]}
        want = {PKTS, BYTES} | ({DROP_INDEX[reason.name]} if reason != RxReason.OK else set())
        assert moved == want, f"{reason.name}: moved {moved}, want {want}"
    assert set(DROP_INDEX) == {r.name for r in RxReason} - {"OK"}


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_reset(dut):
    """rst clears every counter, also in the middle of traffic."""
    st = await start(dut)
    for _ in range(50):
        await st.event(random.choice(list(RxReason)), random.randint(0, STAT_BYTES_MAX))
    assert any(await st.sample())
    dut.rst.value = 1
    await st.event(RxReason.FCS, 77)  # ignored: reset has priority
    dut.rst.value = 0
    assert await st.sample() == [0] * N_CNT
    await st.event(RxReason.MAC, 5)
    got = await st.sample()
    assert (got[PKTS], got[BYTES], got[DROP_INDEX["MAC"]]) == (1, 5, 1)


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_wrap(dut):
    """At and beyond the counter limit (CNT_W = 8 build only: with 64 bits the limit is unreachable,
    tc_bytes_carry_32 tests the byte carry there). Every counter wraps to 0 after 2**CNT_W - 1;
    RX_BYTES with stat_bytes = 0xFFFF steps by 0xFFFF mod 2**CNT_W (checked on every edge)."""
    assert cnt_w() == 8, "tc_wrap runs in the CNT_W = 8 build"
    st = await start(dut)
    limit = st.mask + 1
    for reason in list(RxReason)[1:]:
        for _ in range(limit + 2):  # every drop counter passes its limit (and RX_PKTS many times)
            await st.event(reason, STAT_BYTES_MAX)
    got = await st.sample()
    assert got[2:] == [2] * 9, "each drop counter wrapped once and counted 2 more"
    assert got[PKTS] == (9 * (limit + 2)) % limit


@cocotb.test(timeout_time=200, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_bytes_carry_32(dut):
    """RX_BYTES crosses 2**32 (CNT_W = 64): 65538 events of 0xFFFF bytes = 2**32 + 0xFFFE.
    The model check on every edge covers the carry into bit 32."""
    assert cnt_w() == 64, "tc_bytes_carry_32 runs in the CNT_W = 64 build"
    st = await start(dut)
    n = 65538
    for _ in range(n):
        await st.event(RxReason.OK, STAT_BYTES_MAX)
    got = await st.sample()
    assert got[BYTES] == n * STAT_BYTES_MAX == (1 << 32) + 0xFFFE
    assert got[PKTS] == n


# The long test runs as its own pytest entry (parallel worker, shared build).
LONG_TESTS = ("tc_bytes_carry_32",)
# Tests that only make sense at one counter width: name -> CNT_W.
WIDTH_TESTS = {"tc_wrap": 8, "tc_bytes_carry_32": 64}


def directed_tests(width: int) -> list[str]:
    """Every tc_* of this module that is not a long test and fits this CNT_W."""
    return [
        name for name in globals()
        if name.startswith("tc_") and name not in LONG_TESTS and WIDTH_TESTS.get(name, width) == width
    ]  # fmt: skip


# CNT_W: 64 (the product value) and 8 (every counter reaches its limit in a short test).
@pytest.mark.parametrize("group, cnt_w", [("directed", 64), ("directed", 8), *((n, WIDTH_TESTS[n]) for n in LONG_TESTS)])
def test_rx_stats(group, cnt_w):
    run_cocotb(
        toplevel="rx_stats",
        filelists=["common.f", "core.f", "csr.f"],
        test_module="test_rx_stats",
        parameters={"CNT_W": cnt_w},
        testcase=directed_tests(cnt_w) if group == "directed" else group,
        extra_env={"GROUP": group, "CNT_W": cnt_w},
    )
