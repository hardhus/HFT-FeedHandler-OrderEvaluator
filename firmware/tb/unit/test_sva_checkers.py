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


ITCH_STATS_TB = ["firmware/rtl/core/hft_pkg.sv", "firmware/tb/sva/itch_stats_props.sv", "firmware/tb/sv/tb_itch_stats_props.sv"]
ITCH_STATS_VIOLATIONS = {
    "a_pend_limit": (1, "pending count past 16 bits (a packet of more than 65535 results)"),
}


def test_itch_stats_props_quiet_on_legal_traffic():
    """A result on a full pending count with a packet event (legal) fires nothing."""
    run_svtb(top="tb_itch_stats_props", filelists=[], tb_files=ITCH_STATS_TB)


@pytest.mark.parametrize("prop", list(ITCH_STATS_VIOLATIONS))
def test_itch_stats_props_fires(prop, capsys):
    """The violation fires its own assertion."""
    k, message = ITCH_STATS_VIOLATIONS[prop]
    with pytest.raises(AssertionError, match="exited with|expected one"):
        run_svtb(top="tb_itch_stats_props", filelists=[], tb_files=ITCH_STATS_TB, defines={"HFT_VIOLATE": k})
    out = capsys.readouterr().out
    fired = [name for name, (_, msg) in ITCH_STATS_VIOLATIONS.items() if msg in out]
    assert fired == [prop], f"HFT_VIOLATE={k}: fired {fired}"


PACKER_TB = ["firmware/rtl/core/hft_pkg.sv", "firmware/tb/sva/packer_props.sv", "firmware/tb/sv/tb_packer_props.sv"]
PACKER_VIOLATIONS = {
    "a_no_ev_with_pkt": (1, "a message in the cycle of a packet event"),
    "a_ev_after_pkt": (2, "a message within 2 cycles after a packet event"),
    "a_accepted_spacing": (3, "an accepted packet event within 2 cycles after another"),
    "a_capacity": (4, "a packet of more than RING_MAX_PKT_ENTRIES entries"),
    "a_write_when_ready": (5, "a write to a full or busy FIFO"),
    "a_end_xor": (6, "fifo_commit and fifo_rollback in one cycle"),
    "a_end_has_event": (7, "a commit or rollback without a packet event"),
    "a_accepted_gets_end": (8, "an accepted packet event without a commit or rollback"),
}

STRATEGY_TB = ["firmware/tb/sva/strategy_props.sv", "firmware/tb/sv/tb_strategy_props.sv"]
STRATEGY_VIOLATIONS = {
    "a_fire_onehot": (1, "more than one rule fired on a message"),
    "a_fire_armed": (2, "a rule fired while disarmed or with a static error"),
    "a_fire_once": (3, "a rule fired twice in one packet"),
    "a_order_committed": (4, "an order out that no accepted packet committed"),
    "a_drain": (5, "an accepted order waited"),
    "a_room": (6, "an order written to a full queue"),
    "a_fire_closed": (7, "a rule fired while the order path was closed"),
}

RISK_TB = ["firmware/tb/sva/risk_props.sv", "firmware/tb/sv/tb_risk_props.sv"]
RISK_VIOLATIONS = {
    "a_kill_no_accept": (1, "an order accepted while killed"),
    "a_tx_off_no_accept": (2, "an order accepted while tx_en was 0"),
    "a_budget_no_accept": (3, "an order accepted with budget 0"),
    "a_rate_no_accept": (4, "an order accepted with no token"),
    "a_one_gate": (5, "an order went to TX without passing the checks"),
    "a_held_live": (6, "held and live orders above the queue size"),
    "a_no_tx_while_drain": (7, "an order offered to TX while cancelled ones are written out"),
    "a_ref_left": (8, "an order handed over with no UserRefNum left"),
    "a_accept_has_credit": (9, "an order accepted with no ring credit"),
    "a_credits_bounded": (10, "more ring credits than the packer's order queue"),
}

AXI_WR_TB = ["firmware/tb/sva/axi_wr_props.sv", "firmware/tb/sv/tb_axi_wr_props.sv"]
AXI_WR_VIOLATIONS = {
    "a_aw_stable": (1, "AW changed or dropped before AWREADY"),
    "a_w_stable": (2, "W changed or dropped before WREADY"),
    "a_4k": (3, "a burst crosses 4 KiB"),
    "a_incr": (4, "a burst that is not INCR of full-width beats"),
    "a_wstrb": (5, "a W beat without all its strobes"),
    "a_w_beats": (6, "W beats differ from AWLEN+1"),
    "a_b_after_aw": (7, "more B responses than AW"),
    "a_reset_valid": (8, "VALID high after reset"),
}

RING_TB = ["firmware/tb/sva/ring_props.sv", "firmware/tb/sv/tb_ring_props.sv"]
RING_VIOLATIONS = {
    "a_no_take_when_full": (1, "a burst started on a full ring"),
    "a_no_pop_when_full": (2, "the FIFO read on a full ring"),
    "a_prod_step": (3, "prod moved other than by one per burst"),
    "a_prod_start": (4, "prod not 0 after enable"),
    "a_phase": (5, "phase differs from its formula"),
    "a_addr": (6, "address differs from its formula"),
    "a_outstanding": (7, "more bursts unanswered than MAX_OUTSTANDING"),
    "a_enable_refused": (8, "an enable with a stale cons was not refused"),
    "a_enable_bad_size": (9, "an enable with a size out of range was not refused"),
}

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
FIFO_COMMIT_TB = ["firmware/tb/sva/fifo_props.sv", "firmware/tb/sv/tb_fifo_commit_props.sv"]
FIFO_COMMIT_VIOLATIONS = {
    "a_commit_xor_rollback": (1, "wr_commit and wr_rollback in one cycle"),
    "a_read_committed": (2, "read a word that was not committed"),
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
ARM_TB = ["firmware/tb/sva/assert_arm.sv", "firmware/tb/sv/tb_assert_arm.sv"]
ARM_VIOLATIONS = {"a_after_arm": (1, "assertion fired while bad is high")}
CHECKERS = {
    "arm": ("tb_assert_arm", ARM_TB, ARM_VIOLATIONS),
    "fifo": ("tb_fifo_props", FIFO_TB, FIFO_VIOLATIONS),
    "fifo_commit": ("tb_fifo_commit_props", FIFO_COMMIT_TB, FIFO_COMMIT_VIOLATIONS),
    "packer": ("tb_packer_props", PACKER_TB, PACKER_VIOLATIONS),
    "strategy": ("tb_strategy_props", STRATEGY_TB, STRATEGY_VIOLATIONS),
    "risk": ("tb_risk_props", RISK_TB, RISK_VIOLATIONS),
    "axi_wr": ("tb_axi_wr_props", AXI_WR_TB, AXI_WR_VIOLATIONS),
    "ring": ("tb_ring_props", RING_TB, RING_VIOLATIONS),
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
