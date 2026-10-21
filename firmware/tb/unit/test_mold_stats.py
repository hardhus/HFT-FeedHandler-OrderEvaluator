"""mold_stats: MoldUDP64 counter bank (audit/11 §2.6).

Every cycle the testbench compares all counters with a Python model, so the latency (an event is
in cnt one cycle after its pkt_valid) is checked with exact equality, not eventually. The event
fields are driven with arbitrary values (moldudp64_rx zeroes them where they do not apply, but
mold_stats must add them as they come); only MOLD_MSGS depends on the status. The model indexes
counters by status; tc_every_status checks that order against a hand-written table.
"""

import os
import random

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from common.clocks import start_clock
from common.runner import run_cocotb
from model.mold import ACCEPTED, MoldStatus

PERIOD_PS = 6400  # net_clk, 156.25 MHz
# Hand-written counter index of each status and of the message/gap counters (FEED block order).
INDEX = {
    "OK": 0, "HEARTBEAT": 1, "EOS": 2, "DUP": 3, "DISABLED": 4, "OTHER_FEED": 5, "ABORT": 6, "RUNT": 7,
    "SESSION": 8, "UNSUPPORTED": 9, "MALFORMED": 10,
    "MSGS": 11, "DUP_MSGS": 12, "OVERSIZE_MSGS": 13, "GAP_EVENTS": 14, "GAP_MSGS": 15,
}  # fmt: skip
N_CNT = 16
MSGS, DUP_MSGS, OVERSIZE_MSGS, GAP_EVENTS, GAP_MSGS = 11, 12, 13, 14, 15
U16, U64 = 0xFFFF, (1 << 64) - 1


def cnt_w() -> int:
    return int(os.environ["CNT_W"])


class Stats:
    """Drives pkt_* and checks cnt against the model on every clock edge."""

    def __init__(self, dut):
        self.dut = dut
        self.mask = (1 << cnt_w()) - 1
        self.model = [0] * N_CNT
        self.cycles = 0
        cocotb.start_soon(self._check())

    def apply(self, status: MoldStatus, gap: int, msgs: int, dups: int, over: int) -> None:
        def add(i: int, n: int) -> None:
            self.model[i] = (self.model[i] + n) & self.mask

        add(status, 1)
        if status in ACCEPTED:
            add(MSGS, msgs)
        add(DUP_MSGS, dups)
        add(OVERSIZE_MSGS, over)
        add(GAP_EVENTS, int(gap != 0))
        add(GAP_MSGS, gap)

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

    async def event(self, status: MoldStatus, gap: int = 0, msgs: int = 0, dups: int = 0, over: int = 0) -> None:
        """One pkt_valid cycle; the model takes it at the edge that samples it."""
        await self.drive(True, status, gap, msgs, dups, over)

    async def sample(self) -> list[int]:
        """cnt in the middle of the cycle (settled after the last edge); drive again afterwards."""
        await FallingEdge(self.dut.clk)
        return self.read()

    async def idle(self, n: int = 1) -> None:
        for _ in range(n):
            await self.drive(False, random.choice(list(MoldStatus)), *(random.getrandbits(w) for w in (64, 16, 16, 16)))

    async def drive(self, valid: bool, status: MoldStatus, gap: int, msgs: int, dups: int, over: int) -> None:
        """Present one cycle of pkt_* and update the model at the edge that samples it. rst is
        set by the test before the call and changed only after it, so its value here is the one
        that edge saw. pkt_valid drops right after the edge: one call is exactly one event.
        Idle cycles carry random fields (they must be ignored)."""
        d = self.dut
        d.pkt_valid.value = int(valid)
        d.pkt_status.value = int(status)
        d.pkt_gap.value = gap
        d.pkt_msgs.value = msgs
        d.pkt_dup_msgs.value = dups
        d.pkt_oversize.value = over
        await RisingEdge(d.clk)
        d.pkt_valid.value = 0
        if d.rst.value:
            self.model = [0] * N_CNT
        elif valid:
            self.apply(status, gap, msgs, dups, over)


async def start(dut) -> Stats:
    """Idle inputs and reset first, then the clock (no edge with undriven inputs)."""
    dut.pkt_valid.value = 0
    dut.pkt_status.value = 0
    dut.pkt_gap.value = 0
    dut.pkt_msgs.value = 0
    dut.pkt_dup_msgs.value = 0
    dut.pkt_oversize.value = 0
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    for _ in range(3):
        await RisingEdge(dut.clk)
    st = Stats(dut)
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    return st


def rand_field(bits: int) -> int:
    return random.choice([0, (1 << bits) - 1, random.getrandbits(bits)])


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_counts(dut):
    """Random events, back to back and with gaps: every status, fields 0, max or random."""
    st = await start(dut)
    for _ in range(3000):
        if random.random() < 0.3:
            await st.idle(random.randint(1, 3))
        await st.event(random.choice(list(MoldStatus)), rand_field(64), rand_field(16), rand_field(16), rand_field(16))
    await st.idle(2)
    # one status counter per event: their sum is the event count (mod the counter range)
    assert sum(st.model[: INDEX["MALFORMED"] + 1]) % (st.mask + 1) == 3000 % (st.mask + 1)


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_every_status(dut):
    """One event per status with 5 messages: only its own counter moves, and MOLD_MSGS for OK,
    HEARTBEAT and EOS. Then each field alone moves only its counter (and the status's)."""
    st = await start(dut)
    accepted = {"OK", "HEARTBEAT", "EOS"}
    for status in MoldStatus:
        before = await st.sample()
        await st.event(status, msgs=5)
        after = await st.sample()
        moved = {i for i in range(N_CNT) if after[i] != before[i]}
        want = {INDEX[status.name]} | ({INDEX["MSGS"]} if status.name in accepted else set())
        assert moved == want, f"{status.name}: moved {moved}, want {want}"
    for field, kwargs, want in (
        ("gap", {"gap": 7}, {"GAP_EVENTS", "GAP_MSGS"}),
        ("dup", {"dups": 3}, {"DUP_MSGS"}),
        ("oversize", {"over": 2}, {"OVERSIZE_MSGS"}),
    ):
        before = await st.sample()
        await st.event(MoldStatus.MALFORMED, **kwargs)  # not accepted: MOLD_MSGS stays
        after = await st.sample()
        moved = {i for i in range(N_CNT) if after[i] != before[i]}
        assert moved == {INDEX[n] for n in want} | {INDEX["MALFORMED"]}, f"{field}: moved {moved}"
    assert set(INDEX) == {s.name for s in MoldStatus} | {"MSGS", "DUP_MSGS", "OVERSIZE_MSGS", "GAP_EVENTS", "GAP_MSGS"}


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_reset(dut):
    """rst clears every counter, also in the middle of traffic."""
    st = await start(dut)
    for _ in range(50):
        await st.event(random.choice(list(MoldStatus)), rand_field(64), rand_field(16), rand_field(16), rand_field(16))
    assert any(await st.sample())
    dut.rst.value = 1
    await st.event(MoldStatus.OK, gap=9, msgs=4)  # ignored: reset has priority
    dut.rst.value = 0
    assert await st.sample() == [0] * N_CNT
    await st.event(MoldStatus.EOS, gap=2, msgs=1)
    got = await st.sample()
    assert (got[INDEX["EOS"]], got[MSGS], got[GAP_EVENTS], got[GAP_MSGS]) == (1, 1, 1, 2)


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_wrap(dut):
    """At and beyond the counter limit (CNT_W = 8 build only). Every status counter wraps to 0
    after 2**CNT_W - 1; the field counters with their largest steps (0xFFFF, 2**64 - 1) step by
    those values mod 2**CNT_W (checked on every edge)."""
    assert cnt_w() == 8, "tc_wrap runs in the CNT_W = 8 build"
    st = await start(dut)
    limit = st.mask + 1
    for status in MoldStatus:
        for _ in range(limit + 2):
            await st.event(status, U64, U16, U16, U16)
    got = await st.sample()
    assert got[: INDEX["MALFORMED"] + 1] == [2] * 11, "each status counter wrapped once and counted 2 more"
    assert got[GAP_EVENTS] == (11 * (limit + 2)) % limit


@cocotb.test(timeout_time=20, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_gap_wrap_64(dut):
    """MOLD_GAP_MSGS at 64 bits: gaps of 2**63 - 1 reach 2**64 - 2, the next gap of 3 wraps to 1
    (the 64-bit adder's carry out is dropped); the carry into bit 32 along the way."""
    assert cnt_w() == 64, "tc_gap_wrap_64 runs in the CNT_W = 64 build"
    st = await start(dut)
    await st.event(MoldStatus.OK, gap=(1 << 32) - 1)
    await st.event(MoldStatus.OK, gap=1)
    assert (await st.sample())[GAP_MSGS] == 1 << 32
    await st.event(MoldStatus.OK, gap=(1 << 63) - 1)
    await st.event(MoldStatus.OK, gap=(1 << 63) - 1 - (1 << 32))
    assert (await st.sample())[GAP_MSGS] == U64 - 1
    await st.event(MoldStatus.HEARTBEAT, gap=3)
    got = await st.sample()
    assert (got[GAP_MSGS], got[GAP_EVENTS]) == (1, 5)


@cocotb.test(timeout_time=200, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_msgs_carry_32(dut):
    """MOLD_MSGS crosses 2**32 (CNT_W = 64): 65538 accepted events of 0xFFFF messages = 2**32 +
    0xFFFE. The model check on every edge covers the carry into bit 32."""
    assert cnt_w() == 64, "tc_msgs_carry_32 runs in the CNT_W = 64 build"
    st = await start(dut)
    n = 65538
    for i in range(n):
        await st.event((MoldStatus.OK, MoldStatus.HEARTBEAT, MoldStatus.EOS)[i % 3], msgs=U16)
    got = await st.sample()
    assert got[MSGS] == n * U16 == (1 << 32) + 0xFFFE


# The long test runs as its own pytest entry (parallel worker, shared build).
LONG_TESTS = ("tc_msgs_carry_32",)
# Tests that only make sense at one counter width: name -> CNT_W.
WIDTH_TESTS = {"tc_wrap": 8, "tc_gap_wrap_64": 64, "tc_msgs_carry_32": 64}


def directed_tests(width: int) -> list[str]:
    """Every tc_* of this module that is not a long test and fits this CNT_W."""
    return [
        name for name in globals()
        if name.startswith("tc_") and name not in LONG_TESTS and WIDTH_TESTS.get(name, width) == width
    ]  # fmt: skip


# CNT_W: 64 (the product value) and 8 (every counter reaches its limit in a short test).
@pytest.mark.parametrize("group, cnt_w", [("directed", 64), ("directed", 8), *((n, WIDTH_TESTS[n]) for n in LONG_TESTS)])
def test_mold_stats(group, cnt_w):
    run_cocotb(
        toplevel="mold_stats",
        filelists=["common.f", "core.f"],
        test_module="test_mold_stats",
        parameters={"CNT_W": cnt_w},
        testcase=directed_tests(cnt_w) if group == "directed" else group,
        extra_env={"GROUP": group, "CNT_W": cnt_w},
    )
