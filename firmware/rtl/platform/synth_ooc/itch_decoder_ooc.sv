// itch_decoder_ooc: OOC synthesis harness for itch_decoder. Every input goes through one register
// first, as from moldudp64_rx's output registers, so the classifier, field and bitmap paths are
// register to register and inside the timing gate. Every output is a port, so synthesis keeps (and the
// gate checks) all of the logic.
// Not used in simulation.
// Clock domain : clk
module itch_decoder_ooc
  import hft_pkg::*;
(
    input  logic                               clk,
    input  logic                               rst,
    input  logic                               s_msg_valid,
    input  logic          [8*MOLD_MAX_MSG-1:0] s_msg_data,
    input  logic          [MOLD_MSG_LEN_W-1:0] s_msg_len,
    input  logic          [              63:0] s_msg_seq,
    input  logic          [              63:0] s_msg_sof_ts,
    input  logic          [  ITCH_N_CLASS-1:0] s_msg_class_en,
    input  logic          [  ITCH_N_CLASS-1:0] s_msg_class_filter,
    input  logic                               s_pkt_valid,
    input  mold_status_e                       s_pkt_status,
    input  logic          [              63:0] s_pkt_seq,
    input  logic          [              15:0] s_pkt_count,
    input  logic          [              63:0] s_pkt_gap,
    input  logic          [              15:0] s_pkt_msgs,
    input  logic          [              15:0] s_pkt_dup_msgs,
    input  logic          [              15:0] s_pkt_oversize,
    input  logic                               s_pkt_synced_now,
    input  logic                               sym_req_valid,
    output logic                               sym_req_ready,
    input  logic                               sym_req_wr,
    input  logic          [              10:0] sym_req_addr,
    input  logic          [              31:0] sym_req_wdata,
    input  logic          [               3:0] sym_req_wstrb,
    output logic                               sym_rsp_valid,
    output logic          [              31:0] sym_rsp_rdata,
    output logic                               m_ev_valid,
    output itch_class_e                        m_ev_class,
    output logic          [               7:0] m_ev_type,
    output logic          [MOLD_MSG_LEN_W-1:0] m_ev_len,
    output logic          [              63:0] m_ev_seq,
    output logic          [              63:0] m_ev_sof_ts,
    output logic          [              15:0] m_ev_locate,
    output logic          [              15:0] m_ev_tracking,
    output logic          [              47:0] m_ev_ts,
    output logic          [              63:0] m_ev_order_ref,
    output logic          [              63:0] m_ev_new_ref,
    output logic          [              63:0] m_ev_shares,
    output logic          [              31:0] m_ev_price,
    output logic          [              63:0] m_ev_match,
    output logic          [               7:0] m_ev_side,
    output logic          [              63:0] m_ev_stock,
    output logic          [              31:0] m_ev_attribution,
    output logic          [               7:0] m_ev_printable,
    output logic          [               7:0] m_ev_cross_type,
    output logic          [8*MOLD_MAX_MSG-1:0] m_ev_raw,
    output logic                               m_res_valid,
    output itch_class_e                        m_res_class,
    output itch_outcome_e                      m_res_outcome,
    output logic                               m_pkt_valid,
    output mold_status_e                       m_pkt_status,
    output logic          [              63:0] m_pkt_seq,
    output logic          [              15:0] m_pkt_count,
    output logic          [              63:0] m_pkt_gap,
    output logic          [              15:0] m_pkt_msgs,
    output logic          [              15:0] m_pkt_dup_msgs,
    output logic          [              15:0] m_pkt_oversize,
    output logic                               m_pkt_synced_now
);
  logic                              rst_q;
  logic                              msg_valid_q;
  logic         [8*MOLD_MAX_MSG-1:0] msg_data_q;
  logic         [MOLD_MSG_LEN_W-1:0] msg_len_q;
  logic         [              63:0] msg_seq_q;
  logic         [              63:0] msg_sof_ts_q;
  logic         [  ITCH_N_CLASS-1:0] msg_class_en_q;
  logic                              pkt_valid_q;
  mold_status_e                      pkt_status_q;
  logic         [              63:0] pkt_seq_q;
  logic         [              15:0] pkt_count_q;
  logic         [              63:0] pkt_gap_q;
  logic         [              15:0] pkt_msgs_q;
  logic         [              15:0] pkt_dup_msgs_q;
  logic         [              15:0] pkt_oversize_q;
  logic                              pkt_synced_now_q;
  logic         [  ITCH_N_CLASS-1:0] msg_class_filter_q;
  logic sym_req_valid_q, sym_req_wr_q;
  logic [10:0] sym_req_addr_q;
  logic [31:0] sym_req_wdata_q;
  logic [ 3:0] sym_req_wstrb_q;

  always_ff @(posedge clk) begin
    rst_q              <= rst;
    msg_valid_q        <= s_msg_valid;
    msg_data_q         <= s_msg_data;
    msg_len_q          <= s_msg_len;
    msg_seq_q          <= s_msg_seq;
    msg_sof_ts_q       <= s_msg_sof_ts;
    msg_class_en_q     <= s_msg_class_en;
    pkt_valid_q        <= s_pkt_valid;
    pkt_status_q       <= s_pkt_status;
    pkt_seq_q          <= s_pkt_seq;
    pkt_count_q        <= s_pkt_count;
    pkt_gap_q          <= s_pkt_gap;
    pkt_msgs_q         <= s_pkt_msgs;
    pkt_dup_msgs_q     <= s_pkt_dup_msgs;
    pkt_oversize_q     <= s_pkt_oversize;
    pkt_synced_now_q   <= s_pkt_synced_now;
    msg_class_filter_q <= s_msg_class_filter;
    sym_req_valid_q    <= sym_req_valid;
    sym_req_wr_q       <= sym_req_wr;
    sym_req_addr_q     <= sym_req_addr;
    sym_req_wdata_q    <= sym_req_wdata;
    sym_req_wstrb_q    <= sym_req_wstrb;
  end

  itch_decoder u_dec (
      .clk               (clk),
      .rst               (rst_q),
      .s_msg_valid       (msg_valid_q),
      .s_msg_data        (msg_data_q),
      .s_msg_len         (msg_len_q),
      .s_msg_seq         (msg_seq_q),
      .s_msg_sof_ts      (msg_sof_ts_q),
      .s_msg_class_en    (msg_class_en_q),
      .s_msg_class_filter(msg_class_filter_q),
      .s_pkt_valid       (pkt_valid_q),
      .s_pkt_status      (pkt_status_q),
      .s_pkt_seq         (pkt_seq_q),
      .s_pkt_count       (pkt_count_q),
      .s_pkt_gap         (pkt_gap_q),
      .s_pkt_msgs        (pkt_msgs_q),
      .s_pkt_dup_msgs    (pkt_dup_msgs_q),
      .s_pkt_oversize    (pkt_oversize_q),
      .s_pkt_synced_now  (pkt_synced_now_q),
      .sym_req_valid     (sym_req_valid_q),
      .sym_req_ready     (sym_req_ready),
      .sym_req_wr        (sym_req_wr_q),
      .sym_req_addr      (sym_req_addr_q),
      .sym_req_wdata     (sym_req_wdata_q),
      .sym_req_wstrb     (sym_req_wstrb_q),
      .sym_rsp_valid     (sym_rsp_valid),
      .sym_rsp_rdata     (sym_rsp_rdata),
      .m_ev_valid        (m_ev_valid),
      .m_ev_class        (m_ev_class),
      .m_ev_type         (m_ev_type),
      .m_ev_len          (m_ev_len),
      .m_ev_seq          (m_ev_seq),
      .m_ev_sof_ts       (m_ev_sof_ts),
      .m_ev_locate       (m_ev_locate),
      .m_ev_tracking     (m_ev_tracking),
      .m_ev_ts           (m_ev_ts),
      .m_ev_order_ref    (m_ev_order_ref),
      .m_ev_new_ref      (m_ev_new_ref),
      .m_ev_shares       (m_ev_shares),
      .m_ev_price        (m_ev_price),
      .m_ev_match        (m_ev_match),
      .m_ev_side         (m_ev_side),
      .m_ev_stock        (m_ev_stock),
      .m_ev_attribution  (m_ev_attribution),
      .m_ev_printable    (m_ev_printable),
      .m_ev_cross_type   (m_ev_cross_type),
      .m_ev_raw          (m_ev_raw),
      .m_res_valid       (m_res_valid),
      .m_res_class       (m_res_class),
      .m_res_outcome     (m_res_outcome),
      .m_pkt_valid       (m_pkt_valid),
      .m_pkt_status      (m_pkt_status),
      .m_pkt_seq         (m_pkt_seq),
      .m_pkt_count       (m_pkt_count),
      .m_pkt_gap         (m_pkt_gap),
      .m_pkt_msgs        (m_pkt_msgs),
      .m_pkt_dup_msgs    (m_pkt_dup_msgs),
      .m_pkt_oversize    (m_pkt_oversize),
      .m_pkt_synced_now  (m_pkt_synced_now)
  );
endmodule
