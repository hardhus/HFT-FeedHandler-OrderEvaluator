// event_path_ooc: OOC synthesis harness for event_packer (its order queue included), the COMMIT=1 cdc_async_fifo and event_ring_writer. Every
// input goes through one register first, as from itch_decoder's output registers on the net side and a register
// slice on the host side, so all paths are register to register and inside the timing and CDC gates. Every output is
// a port, so synthesis keeps all of the logic.
// Not used in simulation.
// Clock domain : net_clk, host_clk
module event_path_ooc
  import hft_pkg::*;
  import event_desc_pkg::*;
#(
    parameter int unsigned DEPTH_LOG2 = 8,
    parameter int unsigned DATA_W     = 512
) (
    input logic net_clk,
    input logic net_rst,
    input logic host_clk,
    input logic host_rst,

    input logic                             s_ev_valid,
    input itch_class_e                      s_ev_class,
    input logic        [               7:0] s_ev_type,
    input logic        [MOLD_MSG_LEN_W-1:0] s_ev_len,
    input logic        [              63:0] s_ev_seq,
    input logic        [              63:0] s_ev_sof_ts,
    input logic        [   CFG_EPOCH_W-1:0] s_ev_epoch,
    input logic        [              15:0] s_ev_locate,
    input logic        [              15:0] s_ev_tracking,
    input logic        [              47:0] s_ev_ts,
    input logic        [              63:0] s_ev_order_ref,
    input logic        [              63:0] s_ev_new_ref,
    input logic        [              63:0] s_ev_shares,
    input logic        [              31:0] s_ev_price,
    input logic        [              63:0] s_ev_match,
    input logic        [               7:0] s_ev_side,
    input logic        [              63:0] s_ev_stock,
    input logic        [              31:0] s_ev_attribution,
    input logic        [               7:0] s_ev_printable,
    input logic        [               7:0] s_ev_cross_type,
    input logic        [8*MOLD_MAX_MSG-1:0] s_ev_raw,

    input logic                           s_pkt_valid,
    input mold_status_e                   s_pkt_status,
    input logic         [           63:0] s_pkt_seq,
    input logic         [           63:0] s_pkt_gap,
    input logic         [           63:0] s_pkt_sof_ts,
    input logic         [CFG_EPOCH_W-1:0] s_pkt_epoch,

    input  logic                            s_sent_valid,
    input  logic         [            31:0] s_sent_user_ref,
    input  order_src_e                      s_sent_source,
    input  logic         [STRAT_RULE_W-1:0] s_sent_rule,
    input  logic         [             7:0] s_sent_side,
    input  logic         [            31:0] s_sent_qty,
    input  logic         [            31:0] s_sent_price,
    input  logic         [            15:0] s_sent_locate,
    input  logic         [            63:0] s_sent_stock,
    input  logic         [             7:0] s_sent_tif,
    input  logic         [           111:0] s_sent_clordid,
    input  logic         [            63:0] s_sent_seq,
    input  logic         [            63:0] s_sent_sof_ts,
    input  logic         [ CFG_EPOCH_W-1:0] s_sent_epoch,
    input  logic         [            63:0] s_sent_tx_ts,
    input  logic                            s_rej_valid,
    input  risk_reason_e                    s_rej_reason,
    input  order_src_e                      s_rej_source,
    input  logic         [STRAT_RULE_W-1:0] s_rej_rule,
    input  logic         [             7:0] s_rej_side,
    input  logic         [            31:0] s_rej_qty,
    input  logic         [            31:0] s_rej_price,
    input  logic         [            15:0] s_rej_locate,
    input  logic         [            63:0] s_rej_stock,
    input  logic         [             7:0] s_rej_tif,
    input  logic         [           111:0] s_rej_clordid,
    input  logic         [            63:0] s_rej_seq,
    input  logic         [            63:0] s_rej_sof_ts,
    input  logic         [ CFG_EPOCH_W-1:0] s_rej_epoch,
    input  logic         [            63:0] s_rej_ts,
    input  logic                            s_rej_lost,
    output logic                            ring_credit_ret,

    output logic [                 63:0] cnt_commit_entries,
    output logic [                 63:0] cnt_drop_pkts,
    output logic [                 63:0] cnt_drop_entries,
    output logic [RING_ORD_QUEUED_W-1:0] cnt_ord_queued,
    output logic                         net_overflow,

    output logic host_underflow,

    input  logic        host_cfg_enable,
    input  logic [63:0] host_cfg_base,
    input  logic [ 4:0] host_cfg_size_log2,
    input  logic [31:0] host_cfg_cons,
    output logic [31:0] host_prod_idx,
    output logic        host_status_bresp_err,
    output logic        host_status_enable_err,
    output logic [63:0] host_cnt_written,
    output logic [63:0] host_cnt_bresp_err,

    output logic [        63:0] m_axi_hmw_awaddr,
    output logic [         7:0] m_axi_hmw_awlen,
    output logic [         2:0] m_axi_hmw_awsize,
    output logic [         1:0] m_axi_hmw_awburst,
    output logic                m_axi_hmw_awvalid,
    input  logic                m_axi_hmw_awready,
    output logic [  DATA_W-1:0] m_axi_hmw_wdata,
    output logic [DATA_W/8-1:0] m_axi_hmw_wstrb,
    output logic                m_axi_hmw_wlast,
    output logic                m_axi_hmw_wvalid,
    input  logic                m_axi_hmw_wready,
    input  logic [         1:0] m_axi_hmw_bresp,
    input  logic                m_axi_hmw_bvalid,
    output logic                m_axi_hmw_bready
);
  logic                             rst_q;
  logic                             ev_valid_q;
  itch_class_e                      ev_class_q;
  logic        [               7:0] ev_type_q;
  logic        [MOLD_MSG_LEN_W-1:0] ev_len_q;
  logic [63:0] ev_seq_q, ev_sof_ts_q;
  logic [CFG_EPOCH_W-1:0] ev_epoch_q;
  logic [15:0] ev_locate_q, ev_tracking_q;
  logic [47:0] ev_ts_q;
  logic [63:0] ev_order_ref_q, ev_new_ref_q, ev_shares_q;
  logic [31:0] ev_price_q;
  logic [63:0] ev_match_q;
  logic [ 7:0] ev_side_q;
  logic [63:0] ev_stock_q;
  logic [31:0] ev_attribution_q;
  logic [7:0] ev_printable_q, ev_cross_type_q;
  logic         [8*MOLD_MAX_MSG-1:0] ev_raw_q;
  logic                              pkt_valid_q;
  mold_status_e                      pkt_status_q;
  logic [63:0] pkt_seq_q, pkt_gap_q, pkt_sof_ts_q;
  logic         [ CFG_EPOCH_W-1:0] pkt_epoch_q;
  logic                            sent_valid_q;
  logic         [            31:0] sent_user_ref_q;
  order_src_e                      sent_source_q;
  logic         [STRAT_RULE_W-1:0] sent_rule_q;
  logic         [             7:0] sent_side_q;
  logic         [            31:0] sent_qty_q;
  logic         [            31:0] sent_price_q;
  logic         [            15:0] sent_locate_q;
  logic         [            63:0] sent_stock_q;
  logic         [             7:0] sent_tif_q;
  logic         [           111:0] sent_clordid_q;
  logic         [            63:0] sent_seq_q;
  logic         [            63:0] sent_sof_ts_q;
  logic         [ CFG_EPOCH_W-1:0] sent_epoch_q;
  logic         [            63:0] sent_tx_ts_q;
  logic                            rej_valid_q;
  risk_reason_e                    rej_reason_q;
  order_src_e                      rej_source_q;
  logic         [STRAT_RULE_W-1:0] rej_rule_q;
  logic         [             7:0] rej_side_q;
  logic         [            31:0] rej_qty_q;
  logic         [            31:0] rej_price_q;
  logic         [            15:0] rej_locate_q;
  logic         [            63:0] rej_stock_q;
  logic         [             7:0] rej_tif_q;
  logic         [           111:0] rej_clordid_q;
  logic         [            63:0] rej_seq_q;
  logic         [            63:0] rej_sof_ts_q;
  logic         [ CFG_EPOCH_W-1:0] rej_epoch_q;
  logic         [            63:0] rej_ts_q;
  logic                            rej_lost_q;

  always_ff @(posedge net_clk) begin
    rst_q            <= net_rst;
    ev_valid_q       <= s_ev_valid;
    ev_class_q       <= s_ev_class;
    ev_type_q        <= s_ev_type;
    ev_len_q         <= s_ev_len;
    ev_seq_q         <= s_ev_seq;
    ev_sof_ts_q      <= s_ev_sof_ts;
    ev_epoch_q       <= s_ev_epoch;
    ev_locate_q      <= s_ev_locate;
    ev_tracking_q    <= s_ev_tracking;
    ev_ts_q          <= s_ev_ts;
    ev_order_ref_q   <= s_ev_order_ref;
    ev_new_ref_q     <= s_ev_new_ref;
    ev_shares_q      <= s_ev_shares;
    ev_price_q       <= s_ev_price;
    ev_match_q       <= s_ev_match;
    ev_side_q        <= s_ev_side;
    ev_stock_q       <= s_ev_stock;
    ev_attribution_q <= s_ev_attribution;
    ev_printable_q   <= s_ev_printable;
    ev_cross_type_q  <= s_ev_cross_type;
    ev_raw_q         <= s_ev_raw;
    pkt_valid_q      <= s_pkt_valid;
    pkt_status_q     <= s_pkt_status;
    pkt_seq_q        <= s_pkt_seq;
    pkt_gap_q        <= s_pkt_gap;
    pkt_sof_ts_q     <= s_pkt_sof_ts;
    pkt_epoch_q      <= s_pkt_epoch;
    sent_valid_q     <= s_sent_valid;
    sent_user_ref_q  <= s_sent_user_ref;
    sent_source_q    <= s_sent_source;
    sent_rule_q      <= s_sent_rule;
    sent_side_q      <= s_sent_side;
    sent_qty_q       <= s_sent_qty;
    sent_price_q     <= s_sent_price;
    sent_locate_q    <= s_sent_locate;
    sent_stock_q     <= s_sent_stock;
    sent_tif_q       <= s_sent_tif;
    sent_clordid_q   <= s_sent_clordid;
    sent_seq_q       <= s_sent_seq;
    sent_sof_ts_q    <= s_sent_sof_ts;
    sent_epoch_q     <= s_sent_epoch;
    sent_tx_ts_q     <= s_sent_tx_ts;
    rej_valid_q      <= s_rej_valid;
    rej_reason_q     <= s_rej_reason;
    rej_source_q     <= s_rej_source;
    rej_rule_q       <= s_rej_rule;
    rej_side_q       <= s_rej_side;
    rej_qty_q        <= s_rej_qty;
    rej_price_q      <= s_rej_price;
    rej_locate_q     <= s_rej_locate;
    rej_stock_q      <= s_rej_stock;
    rej_tif_q        <= s_rej_tif;
    rej_clordid_q    <= s_rej_clordid;
    rej_seq_q        <= s_rej_seq;
    rej_sof_ts_q     <= s_rej_sof_ts;
    rej_epoch_q      <= s_rej_epoch;
    rej_ts_q         <= s_rej_ts;
    rej_lost_q       <= s_rej_lost;
  end

  logic fifo_wr_en, fifo_commit, fifo_rollback, fifo_full, fifo_wr_busy;
  logic [DEPTH_LOG2:0] fifo_wr_count_unused;  // the descriptor FIFO needs no fill level (WR_COUNT=0)
  logic [DESC_W-1:0] fifo_din, fifo_dout;
  logic fifo_rd_en, fifo_empty, fifo_rd_rst_busy;

  event_packer u_packer (
      .clk             (net_clk),
      .rst             (rst_q),
      .s_ev_valid      (ev_valid_q),
      .s_ev_class      (ev_class_q),
      .s_ev_type       (ev_type_q),
      .s_ev_len        (ev_len_q),
      .s_ev_seq        (ev_seq_q),
      .s_ev_sof_ts     (ev_sof_ts_q),
      .s_ev_epoch      (ev_epoch_q),
      .s_ev_locate     (ev_locate_q),
      .s_ev_tracking   (ev_tracking_q),
      .s_ev_ts         (ev_ts_q),
      .s_ev_order_ref  (ev_order_ref_q),
      .s_ev_new_ref    (ev_new_ref_q),
      .s_ev_shares     (ev_shares_q),
      .s_ev_price      (ev_price_q),
      .s_ev_match      (ev_match_q),
      .s_ev_side       (ev_side_q),
      .s_ev_stock      (ev_stock_q),
      .s_ev_attribution(ev_attribution_q),
      .s_ev_printable  (ev_printable_q),
      .s_ev_cross_type (ev_cross_type_q),
      .s_ev_raw        (ev_raw_q),
      .s_pkt_valid     (pkt_valid_q),
      .s_pkt_status    (pkt_status_q),
      .s_pkt_seq       (pkt_seq_q),
      .s_pkt_gap       (pkt_gap_q),
      .s_pkt_sof_ts    (pkt_sof_ts_q),
      .s_pkt_epoch     (pkt_epoch_q),
      .s_sent_valid    (sent_valid_q),
      .s_sent_user_ref (sent_user_ref_q),
      .s_sent_source   (sent_source_q),
      .s_sent_rule     (sent_rule_q),
      .s_sent_side     (sent_side_q),
      .s_sent_qty      (sent_qty_q),
      .s_sent_price    (sent_price_q),
      .s_sent_locate   (sent_locate_q),
      .s_sent_stock    (sent_stock_q),
      .s_sent_tif      (sent_tif_q),
      .s_sent_clordid  (sent_clordid_q),
      .s_sent_seq      (sent_seq_q),
      .s_sent_sof_ts   (sent_sof_ts_q),
      .s_sent_epoch    (sent_epoch_q),
      .s_sent_tx_ts    (sent_tx_ts_q),
      .s_rej_valid     (rej_valid_q),
      .s_rej_reason    (rej_reason_q),
      .s_rej_source    (rej_source_q),
      .s_rej_rule      (rej_rule_q),
      .s_rej_side      (rej_side_q),
      .s_rej_qty       (rej_qty_q),
      .s_rej_price     (rej_price_q),
      .s_rej_locate    (rej_locate_q),
      .s_rej_stock     (rej_stock_q),
      .s_rej_tif       (rej_tif_q),
      .s_rej_clordid   (rej_clordid_q),
      .s_rej_seq       (rej_seq_q),
      .s_rej_sof_ts    (rej_sof_ts_q),
      .s_rej_epoch     (rej_epoch_q),
      .s_rej_ts        (rej_ts_q),
      .s_rej_lost      (rej_lost_q),
      .ring_credit_ret,
      .fifo_wr_en,
      .fifo_din,
      .fifo_commit,
      .fifo_rollback,
      .fifo_full,
      .fifo_wr_busy,
      .cnt_commit_entries,
      .cnt_drop_pkts,
      .cnt_drop_entries,
      .cnt_ord_queued
  );

  cdc_async_fifo #(
      .WIDTH     (DESC_W),
      .DEPTH_LOG2(DEPTH_LOG2),
      .COMMIT    (1'b1)
  ) u_fifo (
      .rst        (rst_q),
      .wr_clk     (net_clk),
      .wr_en      (fifo_wr_en),
      .din        (fifo_din),
      .wr_commit  (fifo_commit),
      .wr_rollback(fifo_rollback),
      .full       (fifo_full),
      .overflow   (net_overflow),
      .wr_rst_busy(fifo_wr_busy),
      .wr_count   (fifo_wr_count_unused),
      .rd_clk     (host_clk),
      .rd_en      (fifo_rd_en),
      .dout       (fifo_dout),
      .empty      (fifo_empty),
      .underflow  (host_underflow),
      .rd_rst_busy(fifo_rd_rst_busy)
  );

  logic host_rst_q, cfg_enable_q;
  logic [63:0] cfg_base_q;
  logic [ 4:0] cfg_size_log2_q;
  logic [31:0] cfg_cons_q;
  logic awready_q, wready_q, bvalid_q;
  logic [1:0] bresp_q;

  always_ff @(posedge host_clk) begin
    host_rst_q      <= host_rst;
    cfg_enable_q    <= host_cfg_enable;
    cfg_base_q      <= host_cfg_base;
    cfg_size_log2_q <= host_cfg_size_log2;
    cfg_cons_q      <= host_cfg_cons;
    awready_q       <= m_axi_hmw_awready;
    wready_q        <= m_axi_hmw_wready;
    bvalid_q        <= m_axi_hmw_bvalid;
    bresp_q         <= m_axi_hmw_bresp;
  end

  event_ring_writer #(
      .DATA_W(DATA_W)
  ) u_writer (
      .clk              (host_clk),
      .rst              (host_rst_q),
      .fifo_rd_en,
      .fifo_dout,
      .fifo_empty,
      .fifo_rd_rst_busy,
      .cfg_enable       (cfg_enable_q),
      .cfg_base         (cfg_base_q),
      .cfg_size_log2    (cfg_size_log2_q),
      .cfg_cons         (cfg_cons_q),
      .prod_idx         (host_prod_idx),
      .status_bresp_err (host_status_bresp_err),
      .status_enable_err(host_status_enable_err),
      .m_axi_hmw_awaddr,
      .m_axi_hmw_awlen,
      .m_axi_hmw_awsize,
      .m_axi_hmw_awburst,
      .m_axi_hmw_awvalid,
      .m_axi_hmw_awready(awready_q),
      .m_axi_hmw_wdata,
      .m_axi_hmw_wstrb,
      .m_axi_hmw_wlast,
      .m_axi_hmw_wvalid,
      .m_axi_hmw_wready (wready_q),
      .m_axi_hmw_bresp  (bresp_q),
      .m_axi_hmw_bvalid (bvalid_q),
      .m_axi_hmw_bready,
      .cnt_written      (host_cnt_written),
      .cnt_bresp_err    (host_cnt_bresp_err)
  );
endmodule
