"""cdc_async_fifo (portable): order, no loss/duplication, full/empty boundaries, overflow and
underflow, latency, reset/busy protocol and gray pointers, across clock ratios and with the
CDC jitter model."""

import os
import random

import cocotb
import pytest
from cocotb.triggers import Combine, FallingEdge, ReadOnly, RisingEdge

from common.clocks import PROFILES, profile, start_clock
from common.runner import run_cocotb
from common.scoreboard import Scoreboard

RUNTIME_PARAMS = ("ratio",)
WIDTH = 32
SYNC_STAGES = 2


def depth() -> int:
    return 2 ** int(os.environ["DEPTH_LOG2"])


def jitter() -> int:
    return int(os.environ["JITTER"])


class Pulses:
    """Counts overflow / underflow pulses for the whole test."""

    def __init__(self, dut) -> None:
        self.overflow = 0
        self.underflow = 0
        cocotb.start_soon(self._count(dut.wr_clk, dut.overflow, "overflow"))
        cocotb.start_soon(self._count(dut.rd_clk, dut.underflow, "underflow"))

    async def _count(self, clk, sig, name: str) -> None:
        while True:
            await RisingEdge(clk)
            await ReadOnly()
            if sig.value:
                setattr(self, name, getattr(self, name) + 1)


async def start(dut) -> Pulses:
    """Inputs and reset first, then the clocks; power-up reset held long enough (see the module
    header); wait for both busy flags."""
    wr_ps, rd_ps, jit = profile()
    dut.wr_en.value = 0
    dut.rd_en.value = 0
    dut.din.value = 0
    dut.rst.value = 1
    start_clock(dut.wr_clk, wr_ps, jit)
    start_clock(dut.rd_clk, rd_ps, jit)
    for _ in range(2 * SYNC_STAGES + 4):
        await RisingEdge(dut.wr_clk)
        await RisingEdge(dut.rd_clk)
    await FallingEdge(dut.wr_clk)
    dut.rst.value = 0
    await wait_not_busy(dut)
    return Pulses(dut)


async def wait_not_busy(dut, limit: int = 100) -> None:
    for _ in range(limit):
        await RisingEdge(dut.wr_clk)
        await ReadOnly()
        if not dut.wr_rst_busy.value and not dut.rd_rst_busy.value:
            return
    raise AssertionError("reset handshake never finished (busy stuck high)")


async def writer(dut, words: list[int], sb: Scoreboard, p: float = 1.0) -> None:
    """Write when full and wr_rst_busy are low (both registered, stable at the falling edge)."""
    pending = list(words)
    while pending:
        await FallingEdge(dut.wr_clk)
        if not dut.full.value and not dut.wr_rst_busy.value and random.random() < p:
            dut.wr_en.value = 1
            dut.din.value = pending[0]
            sb.push(pending.pop(0))
        else:
            dut.wr_en.value = 0
    await FallingEdge(dut.wr_clk)
    dut.wr_en.value = 0


async def reader(dut, sb: Scoreboard, count: int, p: float = 1.0) -> None:
    """FWFT read: dout is valid while empty is low; rd_en pops it at the next edge. Also checks
    that dout holds still while a valid word is not popped."""
    held = None
    while sb.checked < count:
        await FallingEdge(dut.rd_clk)
        if dut.empty.value:
            dut.rd_en.value = 0
            held = None
            continue
        word = int(dut.dout.value)
        assert held is None or word == held, f"FWFT dout changed without rd_en: {held:#x} -> {word:#x}"
        if random.random() < p:
            dut.rd_en.value = 1
            sb.pop_check(word)
            held = None
        else:
            dut.rd_en.value = 0
            held = word
    await FallingEdge(dut.rd_clk)
    dut.rd_en.value = 0


async def traffic(dut, n: int, p_wr: float, p_rd: float, name: str) -> Scoreboard:
    sb = Scoreboard(name)
    words = [random.getrandbits(WIDTH) for _ in range(n)]
    await Combine(cocotb.start_soon(writer(dut, words, sb, p_wr)), cocotb.start_soon(reader(dut, sb, n, p_rd)))
    sb.check_empty()
    return sb


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_random_traffic(dut):
    """Random enables on both sides: every word arrives once, in order; no overflow/underflow
    when the protocol is respected."""
    pulses = await start(dut)
    await traffic(dut, 2000, 0.7, 0.6, "random")
    await traffic(dut, 500, 1.0, 0.3, "writer-fast")  # mostly full
    await traffic(dut, 500, 0.3, 1.0, "reader-fast")  # mostly empty
    assert pulses.overflow == 0 and pulses.underflow == 0, (pulses.overflow, pulses.underflow)


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_fill_drain(dut):
    """Without reads the FIFO takes exactly 2**DEPTH_LOG2 words, then drains them in order."""
    await start(dut)
    sb = Scoreboard("fill")
    accepted = 0
    for _ in range(4 * depth() + 20):
        await FallingEdge(dut.wr_clk)
        if dut.full.value:
            dut.wr_en.value = 0
            continue
        word = random.getrandbits(WIDTH)
        dut.wr_en.value = 1
        dut.din.value = word
        sb.push(word)
        accepted += 1
    assert accepted == depth(), f"accepted {accepted} words, depth is {depth()}"
    await reader(dut, sb, depth())
    sb.check_empty()
    for _ in range(SYNC_STAGES + 4):
        await RisingEdge(dut.wr_clk)
    await ReadOnly()
    assert dut.empty.value and not dut.full.value, "flags after drain"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_overflow_underflow(dut):
    """Writes while full and reads while empty are rejected, each pulsing overflow/underflow
    once; rejected words never appear."""
    pulses = await start(dut)
    sb = Scoreboard("ovf")
    await writer(dut, [random.getrandbits(WIDTH) for _ in range(depth())], sb)
    for _ in range(5):  # attempt writes while full
        await FallingEdge(dut.wr_clk)
        assert dut.full.value
        dut.wr_en.value = 1
        dut.din.value = random.getrandbits(WIDTH)
    await FallingEdge(dut.wr_clk)
    dut.wr_en.value = 0
    await reader(dut, sb, depth())
    sb.check_empty()
    for _ in range(SYNC_STAGES + 4):
        await FallingEdge(dut.rd_clk)
    for _ in range(4):  # attempt reads while empty
        await FallingEdge(dut.rd_clk)
        assert dut.empty.value
        dut.rd_en.value = 1
    await FallingEdge(dut.rd_clk)
    dut.rd_en.value = 0
    for _ in range(3):
        await RisingEdge(dut.rd_clk)
        await RisingEdge(dut.wr_clk)
    assert pulses.overflow == 5, f"overflow pulses: {pulses.overflow}"
    assert pulses.underflow == 4, f"underflow pulses: {pulses.underflow}"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_latency_bound(dut):
    """A single write into an empty FIFO is visible (empty low) within SYNC_STAGES+3 rd edges."""
    await start(dut)
    for _ in range(20):
        await FallingEdge(dut.wr_clk)
        dut.wr_en.value = 1
        dut.din.value = word = random.getrandbits(WIDTH)
        await RisingEdge(dut.wr_clk)  # accepted here
        dut.wr_en.value = 0
        edges = 0
        while True:
            await RisingEdge(dut.rd_clk)
            edges += 1
            await ReadOnly()
            if not dut.empty.value:
                break
            assert edges < 20, "word never became visible"
        assert int(dut.dout.value) == word
        assert edges <= SYNC_STAGES + 3 + jitter(), f"latency {edges} rd edges"
        await FallingEdge(dut.rd_clk)
        dut.rd_en.value = 1
        await FallingEdge(dut.rd_clk)
        dut.rd_en.value = 0
        for _ in range(SYNC_STAGES + 4):
            await RisingEdge(dut.wr_clk)


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_reset_mid_traffic(dut):
    """1-cycle rst while busy with data: enables are ignored (and pulse overflow/underflow) until
    both busy flags drop; afterwards the FIFO is empty and no pre-reset word ever appears."""
    pulses = await start(dut)
    sb = Scoreboard("pre")
    words = [random.getrandbits(WIDTH) for _ in range(depth())]
    await writer(dut, words, sb)  # leave data inside

    await FallingEdge(dut.wr_clk)
    dut.rst.value = 1
    dut.wr_en.value = 1
    dut.din.value = 0xDEAD
    await FallingEdge(dut.wr_clk)
    dut.rst.value = 0
    rejected_wr = 2  # the edge with rst high, and the next edge (wr_en still high, busy already set)
    while True:  # keep trying to write while wr_rst_busy (registered: its falling-edge value holds)
        await FallingEdge(dut.wr_clk)
        if not dut.wr_rst_busy.value:
            dut.wr_en.value = 0
            break
        dut.din.value = random.getrandbits(WIDTH)
        rejected_wr += 1

    rejected_rd = 0
    while True:  # rd side may still be busy; reads must be ignored too
        await FallingEdge(dut.rd_clk)
        if not dut.rd_rst_busy.value:
            dut.rd_en.value = 0
            break
        dut.rd_en.value = 1
        rejected_rd += 1
    await wait_not_busy(dut)
    for _ in range(SYNC_STAGES + 3):
        await RisingEdge(dut.rd_clk)
    await ReadOnly()
    assert dut.empty.value, "FIFO not empty after reset"
    assert not dut.full.value, "FIFO full after reset"
    assert pulses.overflow == rejected_wr, f"{pulses.overflow} overflow pulses for {rejected_wr} rejected writes"
    assert pulses.underflow == rejected_rd, f"{pulses.underflow} underflow pulses for {rejected_rd} rejected reads"

    await traffic(dut, 300, 0.7, 0.7, "post-reset")  # a stale word would break the order


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_gray_one_bit_step(dut):
    """Gray pointers change by exactly one bit per step, wrap included."""
    await start(dut)
    steps = {"wgray_q": 0, "rgray_q": 0}

    async def watch(clk, name: str):
        sig = getattr(dut.g_portable, name)
        prev = int(sig.value)
        while True:
            await RisingEdge(clk)
            await ReadOnly()
            cur = int(sig.value)
            if cur != prev:
                assert bin(cur ^ prev).count("1") == 1, f"{name}: {prev:#x} -> {cur:#x}"
                steps[name] += 1
            prev = cur

    cocotb.start_soon(watch(dut.wr_clk, "wgray_q"))
    cocotb.start_soon(watch(dut.rd_clk, "rgray_q"))
    await traffic(dut, 4 * depth() + 50, 0.8, 0.8, "gray")
    assert min(steps.values()) > 2 * depth(), f"pointers did not wrap: {steps}"


@pytest.mark.parametrize("ratio", list(PROFILES))
@pytest.mark.parametrize("jitter", [0, 1], ids=["nojit", "jit"])
@pytest.mark.parametrize("depth_log2", [2, 4], ids=["d4", "d16"])
def test_cdc_async_fifo(depth_log2, jitter, ratio):
    run_cocotb(
        toplevel="cdc_async_fifo",
        filelists=["common.f"],
        test_module="test_cdc_async_fifo",
        parameters={"WIDTH": WIDTH, "DEPTH_LOG2": depth_log2, "SYNC_STAGES": SYNC_STAGES},
        defines={"HFT_SIM_CDC_JITTER": 1} if jitter else {},
        extra_env={"DEPTH_LOG2": depth_log2, "JITTER": jitter, "RATIO": ratio},
    )
