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


MOLD_TB = ["firmware/rtl/core/hft_pkg.sv", "firmware/tb/sva/mold_props.sv", "firmware/tb/sv/tb_mold_props.sv"]
# HFT_VIOLATE number in tb_mold_props.sv -> the assertion's message.
MOLD_VIOLATIONS = {
    "a_in_keep_full": (1, "input beat before tlast without a full tkeep"),
    "a_in_keep_contig": (2, "input tlast beat with a non-contiguous tkeep"),
    "a_in_meta_stable": (3, "s_udp_* changed inside an input packet"),
    "a_msg_len": (4, "message length outside MOLD_MIN_LEN..MOLD_MAX_MSG"),
    "a_msg_zero_tail": (5, "message bytes after the length are not 0"),
    "a_msg_seq_up": (6, "message seq does not go up within a packet"),
    "a_msg_in_packet": (7, "message with no input packet awaiting its event"),
    "a_pkt_after_tlast": (8, "packet event not 3 cycles after an input tlast"),
    "a_tlast_gets_pkt": (9, "input tlast without a packet event 3 cycles later"),
    "a_pkt_msgs_count": (10, "m_pkt_msgs differs from the messages output since the last event"),
    "a_unframed_no_msgs": (11, "messages output for a DISABLED, OTHER_FEED, RUNT or SESSION packet"),
    "a_status_legal": (12, "packet status is not a mold_status_e code"),
}


def test_mold_props_quiet_on_legal_traffic():
    """Legal traffic that hits every property's antecedent fires nothing."""
    run_svtb(top="tb_mold_props", filelists=[], tb_files=MOLD_TB)


@pytest.mark.parametrize("prop", list(MOLD_VIOLATIONS))
def test_mold_props_fires(prop, capsys):
    """Each violation fires its own assertion, and only that one."""
    k, message = MOLD_VIOLATIONS[prop]
    with pytest.raises(AssertionError, match="exited with|expected one"):
        run_svtb(top="tb_mold_props", filelists=[], tb_files=MOLD_TB, defines={"HFT_VIOLATE": k})
    out = capsys.readouterr().out
    fired = [name for name, (_, msg) in MOLD_VIOLATIONS.items() if msg in out]
    assert fired == [prop], f"HFT_VIOLATE={k}: fired {fired}"


ITCH_TB = ["firmware/rtl/core/hft_pkg.sv", "firmware/tb/sva/itch_props.sv", "firmware/tb/sv/tb_itch_props.sv"]
# HFT_VIOLATE number in tb_itch_props.sv -> the assertion's message.
ITCH_VIOLATIONS = {
    "a_in_len": (1, "input message length outside MOLD_MIN_LEN..MOLD_MAX_MSG"),
    "a_msg_gets_res": (2, "input message without a result 2 cycles later"),
    "a_res_after_msg": (3, "result not 2 cycles after an input message"),
    "a_ev_iff_out": (4, "m_ev_valid differs from a result with outcome OUT"),
    "a_class_legal": (5, "result class is not an itch_class_e code"),
    "a_outcome_legal": (6, "result outcome is not an itch_outcome_e code"),
    "a_ev_class_same": (7, "event class differs from the result class"),
    "a_undecoded_zero": (8, "normalized fields not 0 for an ADMIN, AUCTION, UNKNOWN or MALFORMED event"),
    "a_raw_is_input": (9, "m_ev_raw, m_ev_len or m_ev_type is not the input message"),
    "a_short_no_header": (10, "header fields not 0 for a message shorter than the header"),
    "a_pkt_gets_fwd": (11, "input packet event not forwarded 2 cycles later"),
    "a_pkt_after_input": (12, "packet event not 2 cycles after an input packet event"),
    "a_cmd_gets_rsp": (13, "accepted command without a response 1 cycle later"),
    "a_rsp_after_cmd": (14, "command response not 1 cycle after an accepted command"),
    "a_no_cmd_in_sweep": (15, "sym_req_ready during the bitmap sweep"),
    "a_ready_after_sweep": (16, "sym_req_ready low after the bitmap sweep"),
}


def test_itch_props_quiet_on_legal_traffic():
    """Legal traffic that hits every property's antecedent fires nothing."""
    run_svtb(top="tb_itch_props", filelists=[], tb_files=ITCH_TB)


@pytest.mark.parametrize("prop", list(ITCH_VIOLATIONS))
def test_itch_props_fires(prop, capsys):
    """Each violation fires its own assertion, and only that one."""
    k, message = ITCH_VIOLATIONS[prop]
    with pytest.raises(AssertionError, match="exited with|expected one"):
        run_svtb(top="tb_itch_props", filelists=[], tb_files=ITCH_TB, defines={"HFT_VIOLATE": k})
    out = capsys.readouterr().out
    fired = [name for name, (_, msg) in ITCH_VIOLATIONS.items() if msg in out]
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
