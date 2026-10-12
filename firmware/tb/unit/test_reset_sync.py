"""reset_sync: async assert, STAGES-cycle synchronous deassert.

cocotb tests are named tc_* so pytest does not collect them; pytest only sees
test_reset_sync(), which builds and runs the simulation per STAGES value.
"""

import os
import random

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

from common.clocks import start_clock
from common.runner import run_cocotb

PERIOD_NS = 10


def stages() -> int:
    # Passed via env: Verilator does not expose parameters over VPI by default.
    return int(os.environ["STAGES"])


async def start(dut):
    dut.rst_in.value = 1
    start_clock(dut.clk, PERIOD_NS * 1000)
    for _ in range(stages() + 2):
        await RisingEdge(dut.clk)


@cocotb.test()
async def tc_async_assert(dut):
    """Assert between edges -> rst_out rises without waiting for a clock edge."""
    await start(dut)
    dut.rst_in.value = 0
    for _ in range(stages() + 2):
        await RisingEdge(dut.clk)
    assert dut.rst_out.value == 0
    await Timer(PERIOD_NS // 4, unit="ns")  # mid-cycle
    dut.rst_in.value = 1
    await Timer(1, unit="ns")  # no clock edge in between
    assert dut.rst_out.value == 1, "assert must be asynchronous"


@cocotb.test()
async def tc_sync_deassert_latency(dut):
    """Release reset -> rst_out falls exactly STAGES rising edges later, edge-aligned."""
    await start(dut)
    await FallingEdge(dut.clk)
    dut.rst_in.value = 0
    edges = 0
    while True:
        await RisingEdge(dut.clk)
        edges += 1
        await ReadOnly()
        if dut.rst_out.value == 0:
            break
        assert edges <= stages(), "deassert took too long"
    assert edges == stages(), f"expected {stages()} edges, got {edges}"


@cocotb.test()
async def tc_reassert_during_release(dut):
    """Re-asserting while the release ripples restarts the full STAGES count."""
    await start(dut)
    await FallingEdge(dut.clk)
    dut.rst_in.value = 0
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst_in.value = 1
    await Timer(1, unit="ns")
    assert dut.rst_out.value == 1
    await FallingEdge(dut.clk)
    dut.rst_in.value = 0
    for _ in range(stages() - 1):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert dut.rst_out.value == 1
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert dut.rst_out.value == 0


@cocotb.test()
async def tc_short_pulse(dut):
    """A rst_in pulse shorter than a clock period, between two edges, still gives a full
    reset: rst_out rises at once and falls exactly STAGES rising edges after the pulse."""
    await start(dut)
    dut.rst_in.value = 0
    for _ in range(stages() + 2):
        await RisingEdge(dut.clk)
    period_ps = PERIOD_NS * 1000
    for _ in range(20):
        await RisingEdge(dut.clk)
        offset = random.randint(100, period_ps - 300)  # keep the pulse off the clock edges
        width = random.randint(1, period_ps - 200 - offset)
        await Timer(offset, unit="ps")
        dut.rst_in.value = 1
        await Timer(width, unit="ps")
        assert dut.rst_out.value == 1, f"{width} ps pulse: assert must be asynchronous"
        dut.rst_in.value = 0
        edges = 0
        while True:
            await RisingEdge(dut.clk)
            edges += 1
            await ReadOnly()
            if dut.rst_out.value == 0:
                break
            assert edges <= stages(), "deassert took too long"
        assert edges == stages(), f"{width} ps pulse: reset lasted {edges} edges, expected {stages()}"


@cocotb.test()
async def tc_stable_when_idle(dut):
    """With no reset request, rst_out stays low for many cycles."""
    await start(dut)
    dut.rst_in.value = 0
    for _ in range(stages() + 1):
        await RisingEdge(dut.clk)
    for _ in range(100):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert dut.rst_out.value == 0


@pytest.mark.parametrize("n", [2, 3, 4])
def test_reset_sync(n):
    run_cocotb(
        toplevel="reset_sync",
        filelists=["common.f"],
        test_module="test_reset_sync",
        parameters={"STAGES": n},
        extra_env={"STAGES": n},
    )
