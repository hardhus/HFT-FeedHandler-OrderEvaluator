// event_path_ooc: OOC synthesis harness for event_packer and the COMMIT=1 cdc_async_fifo. Every input goes
// through one register first, as from itch_decoder's output registers, and the FIFO's word goes into a read
// domain register, as into a real consumer, so the packer and the memory -> read-domain paths are inside
// the timing and CDC gates. Every output is a port, so synthesis keeps all of the logic.
// Not used in simulation.
// Clock domain : net_clk, host_clk
module event_path_ooc
  import hft_pkg::*;
  import event_desc_pkg::*;
#(
    parameter int unsigned DEPTH_LOG2 = 8
) (
    input logic net_clk,
    input logic net_rst,
    input logic host_clk,

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

    output logic [63:0] cnt_commit_entries,
    output logic [63:0] cnt_drop_pkts,
    output logic [63:0] cnt_drop_entries,
    output logic        net_overflow,

    input  logic              host_rd_en,
    output logic [DESC_W-1:0] host_dout_q,
    output logic              host_empty,
    output logic              host_underflow,
    output logic              host_rd_rst_busy
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
  logic [CFG_EPOCH_W-1:0] pkt_epoch_q;

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
  end

  logic fifo_wr_en, fifo_commit, fifo_rollback, fifo_full, fifo_wr_busy;
  logic [DESC_W-1:0] fifo_din, fifo_dout;

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
      .fifo_wr_en,
      .fifo_din,
      .fifo_commit,
      .fifo_rollback,
      .fifo_full,
      .fifo_wr_busy,
      .cnt_commit_entries,
      .cnt_drop_pkts,
      .cnt_drop_entries
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
      .rd_clk     (host_clk),
      .rd_en      (host_rd_en),
      .dout       (fifo_dout),
      .empty      (host_empty),
      .underflow  (host_underflow),
      .rd_rst_busy(host_rd_rst_busy)
  );

  always_ff @(posedge host_clk) begin
    if (host_rd_en && !host_empty) begin
      host_dout_q <= fifo_dout;
    end
  end
endmodule
