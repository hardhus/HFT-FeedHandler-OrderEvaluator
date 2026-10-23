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
