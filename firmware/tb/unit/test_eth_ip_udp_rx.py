"""eth_ip_udp_rx: Ethernet/IPv4/UDP receive filter (audit/09).

F2 S0: build and wiring smoke test only. Directed, golden-model and coverage tests
arrive with the decision path (S2) and the payload path (S3, S5).
"""

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

from common.clocks import start_clock
from common.runner import run_cocotb

PERIOD_PS = 6400  # net_clk, 156.25 MHz
N_FILTERS = 8


async def start(dut, cycles: int = 4) -> None:
    """Idle inputs and reset first, then the clock (no edge with undriven inputs)."""
    dut.s_axis_rx_tvalid.value = 0
    dut.s_axis_rx_tdata.value = 0
    dut.s_axis_rx_tkeep.value = 0
    dut.s_axis_rx_tlast.value = 0
    dut.s_axis_rx_tuser.value = 0
    dut.cfg_rx_en.value = 0
    dut.cfg_local_mac.value = 0
    dut.cfg_accept_bcast.value = 0
    dut.cfg_filter_ip.value = 0
    dut.cfg_filter_port.value = 0
    dut.cfg_filter_en.value = 0
    dut.ts_now.value = 0
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


@cocotb.test(timeout_time=1, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_smoke(dut):
    """Idle input after reset: the MAC is never stalled and nothing comes out."""
    await start(dut)
    for _ in range(32):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert int(dut.s_axis_rx_tready.value) == 1, "RX must never backpressure the MAC"
        assert int(dut.m_axis_udp_tvalid.value) == 0, "payload out without a frame in"
        assert int(dut.stat_valid.value) == 0, "stat event without a frame in"


def test_eth_ip_udp_rx():
    run_cocotb(
        toplevel="eth_ip_udp_rx",
        filelists=["common.f", "core.f"],
        test_module="test_eth_ip_udp_rx",
        parameters={"N_FILTERS": N_FILTERS},
    )
