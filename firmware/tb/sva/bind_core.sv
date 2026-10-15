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
