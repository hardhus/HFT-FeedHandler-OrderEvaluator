// moldudp64_rx_ooc: OOC synthesis harness for moldudp64_rx. Every input goes through one
// register first, as from eth_ip_udp_rx's output registers and the F5 config registers, so the
// framer, sequence and realigner paths are register to register and inside the timing gate.
// Every output is a port, so synthesis keeps (and the gate checks) all of the logic.
// Not used in simulation.
// Clock domain : clk
module moldudp64_rx_ooc
  import hft_pkg::*;
#(
    parameter  int unsigned N_FILTERS = 8,
    localparam int unsigned FIDX_W    = (N_FILTERS > 1) ? $clog2(N_FILTERS) : 1
) (
    input  logic                              clk,
    input  logic                              rst,
    input  logic         [              63:0] s_axis_udp_tdata,
    input  logic         [               7:0] s_axis_udp_tkeep,
    input  logic                              s_axis_udp_tvalid,
    input  logic                              s_axis_udp_tlast,
    input  logic                              s_axis_udp_tuser,
    input  logic         [              15:0] s_udp_len,
    input  logic         [        FIDX_W-1:0] s_udp_filter_idx,
    input  logic         [              63:0] s_udp_sof_ts,
    input  logic                              cfg_feed_en,
    input  logic                              cfg_session_check,
    input  logic         [              79:0] cfg_session,
    input  logic         [     N_FILTERS-1:0] cfg_filter_mask,
    input  logic                              cfg_resync,
    input  logic                              cfg_resync_learn,
    input  logic         [              63:0] cfg_resync_seq,
    output logic                              m_msg_valid,
    output logic         [8*MOLD_MAX_MSG-1:0] m_msg_data,
    output logic         [MOLD_MSG_LEN_W-1:0] m_msg_len,
    output logic         [              63:0] m_msg_seq,
    output logic         [              63:0] m_msg_sof_ts,
    output logic                              m_pkt_valid,
    output mold_status_e                      m_pkt_status,
    output logic         [              63:0] m_pkt_seq,
    output logic         [              15:0] m_pkt_count,
    output logic         [              63:0] m_pkt_gap,
    output logic         [              15:0] m_pkt_msgs,
    output logic         [              15:0] m_pkt_dup_msgs,
    output logic         [              15:0] m_pkt_oversize,
    output logic                              m_pkt_synced_now,
    output logic         [              63:0] feed_expected_seq,
    output logic                              feed_synced,
    output logic                              feed_eos
);
  logic                 rst_q;
  logic [         63:0] tdata_q;
  logic [          7:0] tkeep_q;
  logic                 tvalid_q;
  logic                 tlast_q;
  logic                 tuser_q;
  logic [         15:0] len_q;
  logic [   FIDX_W-1:0] fidx_q;
  logic [         63:0] sof_ts_q;
  logic                 feed_en_q;
  logic                 session_check_q;
  logic [         79:0] session_q;
  logic [N_FILTERS-1:0] filter_mask_q;
  logic                 resync_q;
  logic                 resync_learn_q;
  logic [         63:0] resync_seq_q;

  always_ff @(posedge clk) begin
    rst_q           <= rst;
    tdata_q         <= s_axis_udp_tdata;
    tkeep_q         <= s_axis_udp_tkeep;
    tvalid_q        <= s_axis_udp_tvalid;
    tlast_q         <= s_axis_udp_tlast;
    tuser_q         <= s_axis_udp_tuser;
    len_q           <= s_udp_len;
    fidx_q          <= s_udp_filter_idx;
    sof_ts_q        <= s_udp_sof_ts;
    feed_en_q       <= cfg_feed_en;
    session_check_q <= cfg_session_check;
    session_q       <= cfg_session;
    filter_mask_q   <= cfg_filter_mask;
    resync_q        <= cfg_resync;
    resync_learn_q  <= cfg_resync_learn;
    resync_seq_q    <= cfg_resync_seq;
  end

  moldudp64_rx #(
      .N_FILTERS(N_FILTERS)
  ) u_rx (
      .clk              (clk),
      .rst              (rst_q),
      .s_axis_udp_tdata (tdata_q),
      .s_axis_udp_tkeep (tkeep_q),
      .s_axis_udp_tvalid(tvalid_q),
      .s_axis_udp_tlast (tlast_q),
      .s_axis_udp_tuser (tuser_q),
      .s_udp_len        (len_q),
      .s_udp_filter_idx (fidx_q),
      .s_udp_sof_ts     (sof_ts_q),
      .cfg_feed_en      (feed_en_q),
      .cfg_session_check(session_check_q),
      .cfg_session      (session_q),
      .cfg_filter_mask  (filter_mask_q),
      .cfg_resync       (resync_q),
      .cfg_resync_learn (resync_learn_q),
      .cfg_resync_seq   (resync_seq_q),
      .m_msg_valid      (m_msg_valid),
      .m_msg_data       (m_msg_data),
      .m_msg_len        (m_msg_len),
      .m_msg_seq        (m_msg_seq),
      .m_msg_sof_ts     (m_msg_sof_ts),
      .m_pkt_valid      (m_pkt_valid),
      .m_pkt_status     (m_pkt_status),
      .m_pkt_seq        (m_pkt_seq),
      .m_pkt_count      (m_pkt_count),
      .m_pkt_gap        (m_pkt_gap),
      .m_pkt_msgs       (m_pkt_msgs),
      .m_pkt_dup_msgs   (m_pkt_dup_msgs),
      .m_pkt_oversize   (m_pkt_oversize),
      .m_pkt_synced_now (m_pkt_synced_now),
      .feed_expected_seq(feed_expected_seq),
      .feed_synced      (feed_synced),
      .feed_eos         (feed_eos)
  );
endmodule
