// Binds the protocol checks into the core blocks (simulation only, listed in sva_core.f).

// Payload output: generic AXI-Stream checks. There is no m_axis_udp_tready (line rate), so
// the stall property never applies; a_last_keep does.
bind eth_ip_udp_rx axis_props #(
    .DATA_W(64),
    .USER_W(1)
) u_m_axis_udp_props (
    .clk   (clk),
    .rst   (rst),
    .tvalid(m_axis_udp_tvalid),
    .tready(1'b1),
    .tlast (m_axis_udp_tlast),
    .tdata (m_axis_udp_tdata),
    .tkeep (m_axis_udp_tkeep),
    .tuser (m_axis_udp_tuser)
);

bind eth_ip_udp_rx udp_rx_props #(
    .BEAT_W(BEAT_W),
    .META_W(64 + 32 + 32 + 16 + 16 + FIDX_W)
) u_udp_rx_props (
    .clk(clk),
    .rst(rst),
    .s_tvalid(s_axis_rx_tvalid),
    .s_tlast(s_axis_rx_tlast),
    .s_tkeep(s_axis_rx_tkeep),
    .m_tvalid(m_axis_udp_tvalid),
    .m_tlast(m_axis_udp_tlast),
    .m_tkeep(m_axis_udp_tkeep),
    .m_tuser(m_axis_udp_tuser),
    .meta({m_udp_sof_ts, m_udp_src_ip, m_udp_dst_ip, m_udp_dst_port, m_udp_len, m_udp_filter_idx}),
    .stat_valid(stat_valid),
    .in_frame_q(in_frame_q),
    .beat_cnt_q(beat_cnt_q),
    .hdr_done_q(hdr_done_q),
    .stage_b_q(stage_b_q),
    .pay_q(pay_q),
    .pay(pay),
    .tail_q(tail_q)
);

bind moldudp64_rx mold_props #(
    .META_W(16 + FIDX_W + 64)
) u_mold_props (
    .clk         (clk),
    .rst         (rst),
    .s_tvalid    (s_axis_udp_tvalid),
    .s_tlast     (s_axis_udp_tlast),
    .s_tkeep     (s_axis_udp_tkeep),
    .s_meta      ({s_udp_len, s_udp_filter_idx, s_udp_sof_ts}),
    .m_msg_valid (m_msg_valid),
    .m_msg_len   (m_msg_len),
    .m_msg_data  (m_msg_data),
    .m_msg_seq   (m_msg_seq),
    .m_pkt_valid (m_pkt_valid),
    .m_pkt_status(m_pkt_status),
    .m_pkt_msgs  (m_pkt_msgs)
);

bind itch_decoder itch_props u_itch_props (
    .clk(clk),
    .rst(rst),
    .s_msg_valid(s_msg_valid),
    .s_msg_data(s_msg_data),
    .s_msg_len(s_msg_len),
    .s_pkt_valid(s_pkt_valid),
    .m_ev_valid(m_ev_valid),
    .m_ev_class(m_ev_class),
    .m_ev_type(m_ev_type),
    .m_ev_len(m_ev_len),
    .m_ev_raw(m_ev_raw),
    .m_ev_header({m_ev_locate, m_ev_tracking, m_ev_ts}),
    .m_ev_norm({
      m_ev_order_ref,
      m_ev_new_ref,
      m_ev_shares,
      m_ev_match,
      m_ev_stock,
      m_ev_price,
      m_ev_attribution,
      m_ev_side,
      m_ev_printable,
      m_ev_cross_type
    }),
    .m_res_valid(m_res_valid),
    .m_res_class(m_res_class),
    .m_res_outcome(m_res_outcome),
    .m_pkt_valid(m_pkt_valid),
    .sym_req_valid(sym_req_valid),
    .sym_req_ready(sym_req_ready),
    .sym_rsp_valid(sym_rsp_valid)
);

bind event_packer packer_props u_packer_props (
    .clk          (clk),
    .rst          (rst),
    .s_ev_valid   (s_ev_valid),
    .s_pkt_valid  (s_pkt_valid),
    .pkt_accepted (pkt_accepted),
    .s1_valid     (s1_valid),
    .fifo_wr_en   (fifo_wr_en),
    .fifo_commit  (fifo_commit),
    .fifo_rollback(fifo_rollback),
    .fifo_full    (fifo_full),
    .fifo_wr_busy (fifo_wr_busy)
);

bind itch_stats itch_stats_props #(
    .PEND_W(PEND_W)
) u_itch_stats_props (
    .clk      (clk),
    .rst      (rst),
    .pkt_valid(pkt_valid),
    .inc      (inc),
    .pend     (pend)
);

bind event_ring_writer axi_wr_props #(
    .DATA_W(DATA_W)
) u_axi_wr_props (
    .clk    (clk),
    .rst    (rst),
    .awaddr (m_axi_hmw_awaddr),
    .awlen  (m_axi_hmw_awlen),
    .awsize (m_axi_hmw_awsize),
    .awburst(m_axi_hmw_awburst),
    .awvalid(m_axi_hmw_awvalid),
    .awready(m_axi_hmw_awready),
    .wdata  (m_axi_hmw_wdata),
    .wstrb  (m_axi_hmw_wstrb),
    .wlast  (m_axi_hmw_wlast),
    .wvalid (m_axi_hmw_wvalid),
    .wready (m_axi_hmw_wready),
    .bvalid (m_axi_hmw_bvalid),
    .bready (m_axi_hmw_bready)
);

bind event_ring_writer ring_props #(
    .MAX_OUTSTANDING(MAX_OUTSTANDING),
    .OUT_W          (OUT_W)
) u_ring_props (
    .clk          (clk),
    .rst          (rst),
    .enable_q     (running_q),
    .start        (start),
    .cfg_cons     (cfg_cons),
    .cfg_size_log2(cfg_size_log2),
    .take         (take),
    .fifo_rd_en   (fifo_rd_en),
    .mask_q       (mask_q),
    .base_q       (base_q),
    .prod_q       (prod_q),
    .cons_q       (cons_q),
    .addr_q       (addr_q),
    .phase_q      (phase_q),
    .out_q        (out_q)
);

bind strategy_engine strategy_props #(
    .N    (N),
    .DEPTH(DEPTH),
    .PTR_W(PTR_W)
) u_strategy_props (
    .clk        (clk),
    .rst        (rst),
    .fire       (fire),
    .armed_p    (armed_p & ~block),
    .fired_p    (fired_p),
    .err        (err_q),
    .path_open  (!ctl_kill && ctl_tx_en),
    .fire_q     (fire_q),
    .pkt_q      (pkt_q),
    .pkt_acc_q  (pkt_acc_q),
    .fill       (base - rd_ptr_q),
    .m_ord_valid(m_ord_valid)
);

bind risk_check risk_props #(
    .QD     (QD),
    .QP     (QP),
    .CREDITS(hft_pkg::RISK_RING_CREDITS),
    .CW     ($clog2(hft_pkg::RISK_RING_CREDITS + 1))
) u_risk_props (
    .clk(clk),
    .rst(rst),
    .accept(accept),
    .ctl_kill(ctl_kill),
    .ctl_tx_en(ctl_tx_en),
    .budget(budget_q),
    .tokens(tokens_eff),
    .handover(handover),
    .cancelled(cancelled),
    .held(held_q),
    .live(live_q),
    .m_tx_valid(m_tx_valid),
    .ref_next(ref_next_q),
    .credits(credits_q),
    .flags({
      burst_nz_q, refill_nz_q, tick_q, tok_nz_q, budget_zero_q, refs_zero_q, qty_gt_q, notional_gt_q
    }),
    .defs({
      rate_burst_q != '0,
      rate_refill_q != '0,
      rate_period_q != '0 && pc_q == '0,
      tokens_q != '0,
      budget_q == '0,
      (33'h1_0000_0000 - ref_next_q - 33'(live_q)) == '0,
      o3_q.qty > max_qty_q,
      1'b0  // notional: risk_props compares prod2_q one cycle later
    }),
    .lim_wr(cfg_lim_wr || rst),
    .prod2(prod2_q),
    .max_notional(max_notional_q)
);

// TX output: generic AXI-Stream checks (stall stability, tkeep on tlast). No tuser.
bind order_tx_builder axis_props #(
    .DATA_W(64),
    .USER_W(1)
) u_m_axis_tx_props (
    .clk   (clk),
    .rst   (rst),
    .tvalid(m_axis_tx_tvalid),
    .tready(m_axis_tx_tready),
    .tlast (m_axis_tx_tlast),
    .tdata (m_axis_tx_tdata),
    .tkeep (m_axis_tx_tkeep),
    .tuser (1'b0)
);
