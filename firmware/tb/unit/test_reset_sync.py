"""reset_sync: async assert, STAGES-cycle synchronous deassert.

cocotb tests are named tc_* so pytest does not collect them; pytest only sees
test_reset_sync(), which builds and runs the simulation per STAGES value.
"""

import os

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
    start_clock(dut.clk, PERIOD_NS * 1000)
    dut.rst_in.value = 1
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
