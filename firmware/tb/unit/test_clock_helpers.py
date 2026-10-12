"""common.clocks.start_clock: clocks start low, the first rising edge comes half a period later.

Regression (S6, found in CI): cocotb's Clock starts high by default, which gives the DUT a rising
edge at the moment the clock is started. Testbenches that started the clock before driving their
inputs let flops capture random (undriven) inputs at t=0, e.g. a spurious FIFO overflow that
fifo_props.a_overflow_cause flagged.
"""

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge, Timer

try:
    from cocotb.simtime import get_sim_time
except ImportError:  # older cocotb 2.x
    from cocotb.utils import get_sim_time

from common.clocks import start_clock
from common.runner import run_cocotb

PERIOD_PS = 6400


async def first_rise_delay(dut, jitter_pct: int) -> int:
    """Start a clock, check it is low right away, return the delay to its first rising edge."""
    dut.rst_in.value = 0
    t0 = get_sim_time("ps")
    start_clock(dut.clk, PERIOD_PS, jitter_pct, seed=1)
    await ReadOnly()
    assert dut.clk.value == 0, "clock must start low"
    await RisingEdge(dut.clk)
    return get_sim_time("ps") - t0


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_fixed_clock_starts_low(dut):
    """Fixed clock: first rising edge exactly half a period after start."""
    assert await first_rise_delay(dut, 0) == PERIOD_PS // 2


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def tc_jittered_clock_starts_low(dut):
    """Jittered clock: first rising edge within half a period +/- jitter, never at start."""
    await Timer(1, unit="ns")  # leave the previous test's clock state behind
    delay = await first_rise_delay(dut, 20)
    half = PERIOD_PS // 2
    assert half - half // 5 <= delay <= half + half // 5, f"first rising edge after {delay} ps"


def test_clock_helpers():
    run_cocotb(
        toplevel="reset_sync",
        filelists=["common.f"],
        test_module="test_clock_helpers",
        parameters={"STAGES": 2},
    )
