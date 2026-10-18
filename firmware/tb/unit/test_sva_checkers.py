"""Tests of the SVA checkers themselves (they must fire on violations and stay quiet otherwise)."""

import pytest

from common.svtb import run_svtb

TB = ["firmware/tb/sva/fifo_props.sv", "firmware/tb/sv/tb_fifo_props_arming.sv"]


def test_fifo_props_ignores_power_up_busy_glitch():
    """Regression (S6): a stale rd_rst_busy glitch during the power-up rst hold must not be
    taken for the end of reset."""
    run_svtb(top="tb_fifo_props_arming", filelists=[], tb_files=TB)


def test_fifo_props_flags_non_empty_after_reset(capsys):
    """The real end of reset with a non-empty FIFO must fire a_empty_after_reset."""
    with pytest.raises(AssertionError, match="exited with|expected one"):
        run_svtb(top="tb_fifo_props_arming", filelists=[], tb_files=TB, defines={"HFT_VIOLATE": 1})
    assert "FIFO not empty when rd_rst_busy fell" in capsys.readouterr().out, "a_empty_after_reset did not fire"


UDP_TB = ["firmware/tb/sva/udp_rx_props.sv", "firmware/tb/sv/tb_udp_rx_props.sv"]
# HFT_VIOLATE number in tb_udp_rx_props.sv -> the assertion's message.
UDP_RX_VIOLATIONS = {
    "a_in_keep_full": (1, "input beat before tlast without a full tkeep"),
    "a_in_keep_contig": (2, "input tlast beat with a non-contiguous tkeep"),
    "a_out_keep_full": (3, "payload beat before tlast without a full tkeep"),
    "a_out_keep_contig": (4, "payload tlast beat with an empty or non-contiguous tkeep"),
    "a_out_user_last": (5, "abort (tuser) before the payload tlast"),
    "a_out_only_after_hdr": (6, "payload beat for a frame whose headers did not pass"),
    "a_meta_stable": (7, "m_udp_* changed inside a payload packet"),
    "a_sof_clean": (8, "header verdict of the previous frame still set at SOF (K1)"),
    "a_beat_cnt_saturates": (9, "beat counter wrapped (K4)"),
    "a_stat_after_tlast": (10, "stat event not 1 cycle after an input tlast"),
}


def test_udp_rx_props_quiet_on_legal_traffic():
    """Legal traffic that hits every property's antecedent fires nothing."""
    run_svtb(top="tb_udp_rx_props", filelists=[], tb_files=UDP_TB)


@pytest.mark.parametrize("prop", list(UDP_RX_VIOLATIONS))
def test_udp_rx_props_fires(prop, capsys):
    """Each violation fires its own assertion, and only that one."""
    k, message = UDP_RX_VIOLATIONS[prop]
    with pytest.raises(AssertionError, match="exited with|expected one"):
        run_svtb(top="tb_udp_rx_props", filelists=[], tb_files=UDP_TB, defines={"HFT_VIOLATE": k})
    out = capsys.readouterr().out
    fired = [name for name, (_, msg) in UDP_RX_VIOLATIONS.items() if msg in out]
    assert fired == [prop], f"HFT_VIOLATE={k}: fired {fired}"


FIFO_TB = ["firmware/tb/sva/fifo_props.sv", "firmware/tb/sv/tb_fifo_props.sv"]
# HFT_VIOLATE number in tb_fifo_props.sv -> the assertion's message (a_empty_after_reset: above).
FIFO_VIOLATIONS = {
    "a_reject_full": (1, "write while full did not pulse overflow"),
    "a_reject_busy": (2, "write while wr_rst_busy did not pulse overflow"),
    "a_overflow_cause": (3, "overflow without a rejected write"),
    "a_reject_empty": (4, "read while empty did not pulse underflow"),
    "a_underflow_cause": (5, "underflow without a rejected read"),
    "a_fwft_hold": (6, "FWFT word vanished or changed without rd_en"),
}
AXIS_TB = ["firmware/tb/sva/axis_props.sv", "firmware/tb/sv/tb_axis_props.sv"]
AXIS_VIOLATIONS = {
    "a_stall_stable": (1, "payload changed or tvalid dropped while stalled"),
    "a_last_keep": (2, "tlast beat with empty tkeep"),
}

AXIL_TB = ["firmware/tb/sva/axil_props.sv", "firmware/tb/sv/tb_axil_props.sv"]
AXIL_VIOLATIONS = {
    "a_aw_stable": (1, "AW changed or AWVALID dropped before AWREADY"),
    "a_w_stable": (2, "W changed or WVALID dropped before WREADY"),
    "a_ar_stable": (3, "AR changed or ARVALID dropped before ARREADY"),
    "a_b_stable": (4, "BRESP changed or BVALID dropped before BREADY"),
    "a_r_stable": (5, "R changed or RVALID dropped before RREADY"),
    "a_b_count": (6, "BVALID without an accepted write that has no response yet"),
    "a_r_count": (7, "RVALID without an accepted read that has no response yet"),
}
CHECKERS = {
    "fifo": ("tb_fifo_props", FIFO_TB, FIFO_VIOLATIONS),
    "axis": ("tb_axis_props", AXIS_TB, AXIS_VIOLATIONS),
    "axil": ("tb_axil_props", AXIL_TB, AXIL_VIOLATIONS),
}


@pytest.mark.parametrize("top, tb", [(t, tb) for t, tb, _ in CHECKERS.values()], ids=list(CHECKERS))
def test_checker_quiet_on_legal_traffic(top, tb):
    """Legal traffic that hits every property's antecedent fires nothing."""
    run_svtb(top=top, filelists=[], tb_files=tb)


@pytest.mark.parametrize(
    "top, tb, table, prop",
    [(top, tb, table, p) for top, tb, table in CHECKERS.values() for p in table],
    ids=lambda x: x if isinstance(x, str) and x.startswith("a_") else "",
)
def test_checker_fires(top, tb, table, prop, capsys):
    """Each violation fires its own assertion, and only that one."""
    k, _ = table[prop]
    with pytest.raises(AssertionError, match="exited with|expected one"):
        run_svtb(top=top, filelists=[], tb_files=tb, defines={"HFT_VIOLATE": k})
    out = capsys.readouterr().out
    fired = [name for name, (_, msg) in table.items() if msg in out]
    assert fired == [prop], f"{top} HFT_VIOLATE={k}: fired {fired}"
