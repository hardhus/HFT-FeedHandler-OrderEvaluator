"""Tests of the SVA checkers themselves (they must fire on violations and stay quiet otherwise)."""

import pytest

from common.svtb import run_svtb

TB = ["firmware/tb/sva/fifo_props.sv", "firmware/tb/sv/tb_fifo_props_arming.sv"]


def test_fifo_props_ignores_power_up_busy_glitch():
    """Regression (S6): a stale rd_rst_busy glitch during the power-up rst hold must not be
    taken for the end of reset."""
    run_svtb(top="tb_fifo_props_arming", filelists=[], tb_files=TB)


def test_fifo_props_flags_non_empty_after_reset():
    """The real end of reset with a non-empty FIFO must fire a_empty_after_reset."""
    with pytest.raises(AssertionError, match="exited with|expected one"):
        run_svtb(top="tb_fifo_props_arming", filelists=[], tb_files=TB, defines={"HFT_VIOLATE": 1})


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
