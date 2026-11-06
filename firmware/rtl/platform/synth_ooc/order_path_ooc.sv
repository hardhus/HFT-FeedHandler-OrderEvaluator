// order_path_ooc: OOC synthesis harness for the order path: strategy_engine -> risk_check -> order_tx_builder, wired
// as in the system (the effective tx_en = ctl_tx_en && st_cfg_valid, decision 26). Every input goes through one
// register first, as from itch_decoder's output registers and hft_csr_wrap's, so all paths are register to
// register and inside the timing gate. Every output is a port, so synthesis keeps all of the logic.
// Not used in simulation.
// Clock domain : clk (net_clk)
module order_path_ooc
  import hft_pkg::*;
(
    input logic clk,
    input logic rst,

    input logic [           63:0] ts_now,
    input logic [CFG_EPOCH_W-1:0] cfg_epoch_now,

    // itch_decoder m_ev_*, m_pkt_*
    input logic                           s_ev_valid,
    input itch_class_e                    s_ev_class,
    input logic         [            7:0] s_ev_type,
    input logic         [           63:0] s_ev_seq,
    input logic         [           63:0] s_ev_sof_ts,
    input logic         [CFG_EPOCH_W-1:0] s_ev_epoch,
    input logic         [           15:0] s_ev_locate,
    input logic         [           63:0] s_ev_shares,
    input logic         [           31:0] s_ev_price,
    input logic         [            7:0] s_ev_side,
    input logic         [           63:0] s_ev_stock,
    input logic                           s_pkt_valid,
    input mold_status_e                   s_pkt_status,

    // controls, configuration and commands (hft_csr_wrap, net_clk)
    input logic                            ctl_kill,
    input logic                            ctl_tx_en,
    input logic                            cfg_rule_wr,
    input logic        [ STRAT_RULE_W-1:0] cfg_rule_idx,
    input strat_rule_t                     cfg_rule,
    input logic                            cmd_disarm,
    input logic        [STRAT_N_RULES-1:0] cmd_disarm_mask,
    input logic                            cmd_arm,
    input logic        [STRAT_N_RULES-1:0] cmd_arm_mask,
    input logic                            cfg_lim_wr,
    input logic        [             31:0] cfg_max_qty,
    input logic        [             63:0] cfg_max_notional,
    input logic        [             31:0] cfg_rate_refill,
    input logic        [             31:0] cfg_rate_period,
    input logic        [             31:0] cfg_rate_burst,
    input logic                            cmd_budget_add,
    input logic        [             31:0] cmd_budget_amount,
    input logic                            cmd_userref_wr,
    input logic        [             31:0] cmd_userref_value,
    input logic                            cfg_tx_wr,
    input logic        [         8*42-1:0] cfg_tx_template,
    input logic        [              7:0] cfg_tx_display,
    input logic        [              7:0] cfg_tx_capacity,
    input logic        [              7:0] cfg_tx_ise,
    input logic        [              7:0] cfg_tx_cross_type,

    // allow bitmap commands
    input  logic        sym_req_valid,
    output logic        sym_req_ready,
    input  logic        sym_req_wr,
    input  logic [10:0] sym_req_addr,
    input  logic [31:0] sym_req_wdata,
    input  logic [ 3:0] sym_req_wstrb,
    output logic        sym_rsp_valid,
    output logic [31:0] sym_rsp_rdata,

    // host orders (mailbox FIFO read side)
    input  logic         s_host_valid,
    output logic         s_host_ready,
    input  logic [  7:0] s_host_side,
    input  logic [ 31:0] s_host_qty,
    input  logic [ 31:0] s_host_price,
    input  logic [ 15:0] s_host_locate,
    input  logic [ 63:0] s_host_stock,
    input  logic [  7:0] s_host_tif,
    input  logic [111:0] s_host_clordid,

    // ring credits (event_packer)
    input logic ring_credit_ret,

    // TX AXI-Stream
    output logic [63:0] m_axis_tx_tdata,
    output logic [ 7:0] m_axis_tx_tkeep,
    output logic        m_axis_tx_tlast,
    output logic        m_axis_tx_tvalid,
    input  logic        m_axis_tx_tready,

    // RISK_REJECT and ORDER_SENT entries (to event_packer)
    output logic                            m_rej_valid,
    output risk_reason_e                    m_rej_reason,
    output order_src_e                      m_rej_source,
    output logic         [STRAT_RULE_W-1:0] m_rej_rule,
    output logic         [             7:0] m_rej_side,
    output logic         [            31:0] m_rej_qty,
    output logic         [            31:0] m_rej_price,
    output logic         [            15:0] m_rej_locate,
    output logic         [            63:0] m_rej_stock,
    output logic         [             7:0] m_rej_tif,
    output logic         [           111:0] m_rej_clordid,
    output logic         [            63:0] m_rej_seq,
    output logic         [            63:0] m_rej_sof_ts,
    output logic         [ CFG_EPOCH_W-1:0] m_rej_epoch,
    output logic         [            63:0] m_rej_ts,
    output logic                            m_sent_valid,
    output logic         [            31:0] m_sent_user_ref,
    output order_src_e                      m_sent_source,
    output logic         [STRAT_RULE_W-1:0] m_sent_rule,
    output logic         [             7:0] m_sent_side,
    output logic         [            31:0] m_sent_qty,
    output logic         [            31:0] m_sent_price,
    output logic         [            15:0] m_sent_locate,
    output logic         [            63:0] m_sent_stock,
    output logic         [             7:0] m_sent_tif,
    output logic         [           111:0] m_sent_clordid,
    output logic         [            63:0] m_sent_seq,
    output logic         [            63:0] m_sent_sof_ts,
    output logic         [ CFG_EPOCH_W-1:0] m_sent_epoch,
    output logic         [            63:0] m_sent_tx_ts,

    // status
    output logic [STRAT_N_RULES-1:0] st_armed,
    output logic [STRAT_N_RULES-1:0] st_error,
    output logic [             31:0] st_budget,
    output logic                     st_cfg_valid,
    output logic                     ev_userref_ignored,
    output logic                     ev_rej_dropped,
    output logic                     ev_cfg_refused
);
  // ---- input registers ----
  logic                   rst_q;
  logic [           63:0] ts_q;
  logic [CFG_EPOCH_W-1:0] epoch_now_q;
  logic ev_valid_q, pkt_valid_q;
  itch_class_e ev_class_q;
  logic [7:0] ev_type_q, ev_side_q;
  logic [63:0] ev_seq_q, ev_sof_ts_q, ev_shares_q, ev_stock_q;
  logic         [CFG_EPOCH_W-1:0] ev_epoch_q;
  logic         [           15:0] ev_locate_q;
  logic         [           31:0] ev_price_q;
  mold_status_e                   pkt_status_q;
  logic kill_q, tx_en_q, rule_wr_q, disarm_q, arm_q, lim_wr_q, budget_add_q, userref_wr_q, tx_wr_q;
  logic        [STRAT_RULE_W-1:0] rule_idx_q;
  strat_rule_t                    rule_q;
  logic [STRAT_N_RULES-1:0] disarm_mask_q, arm_mask_q;
  logic [31:0] max_qty_q, refill_q, period_q, burst_q, budget_amount_q, userref_value_q;
  logic [    63:0] max_notional_q;
  logic [8*42-1:0] tmpl_q;
  logic [7:0] display_q, capacity_q, ise_q, cross_q;
  logic sym_valid_q, sym_wr_q;
  logic [10:0] sym_addr_q;
  logic [31:0] sym_wdata_q;
  logic [ 3:0] sym_wstrb_q;
  logic        host_valid_q;
  logic [7:0] host_side_q, host_tif_q;
  logic [31:0] host_qty_q, host_price_q;
  logic [ 15:0] host_locate_q;
  logic [ 63:0] host_stock_q;
  logic [111:0] host_clordid_q;
  logic credit_ret_q, tready_q;

  always_ff @(posedge clk) begin
    rst_q           <= rst;
    ts_q            <= ts_now;
    epoch_now_q     <= cfg_epoch_now;
    ev_valid_q      <= s_ev_valid;
    ev_class_q      <= s_ev_class;
    ev_type_q       <= s_ev_type;
    ev_seq_q        <= s_ev_seq;
    ev_sof_ts_q     <= s_ev_sof_ts;
    ev_epoch_q      <= s_ev_epoch;
    ev_locate_q     <= s_ev_locate;
    ev_shares_q     <= s_ev_shares;
    ev_price_q      <= s_ev_price;
    ev_side_q       <= s_ev_side;
    ev_stock_q      <= s_ev_stock;
    pkt_valid_q     <= s_pkt_valid;
    pkt_status_q    <= s_pkt_status;
    kill_q          <= ctl_kill;
    tx_en_q         <= ctl_tx_en;
    rule_wr_q       <= cfg_rule_wr;
    rule_idx_q      <= cfg_rule_idx;
    rule_q          <= cfg_rule;
    disarm_q        <= cmd_disarm;
    disarm_mask_q   <= cmd_disarm_mask;
    arm_q           <= cmd_arm;
    arm_mask_q      <= cmd_arm_mask;
    lim_wr_q        <= cfg_lim_wr;
    max_qty_q       <= cfg_max_qty;
    max_notional_q  <= cfg_max_notional;
    refill_q        <= cfg_rate_refill;
    period_q        <= cfg_rate_period;
    burst_q         <= cfg_rate_burst;
    budget_add_q    <= cmd_budget_add;
    budget_amount_q <= cmd_budget_amount;
    userref_wr_q    <= cmd_userref_wr;
    userref_value_q <= cmd_userref_value;
    tx_wr_q         <= cfg_tx_wr;
    tmpl_q          <= cfg_tx_template;
    display_q       <= cfg_tx_display;
    capacity_q      <= cfg_tx_capacity;
    ise_q           <= cfg_tx_ise;
    cross_q         <= cfg_tx_cross_type;
    sym_valid_q     <= sym_req_valid;
    sym_wr_q        <= sym_req_wr;
    sym_addr_q      <= sym_req_addr;
    sym_wdata_q     <= sym_req_wdata;
    sym_wstrb_q     <= sym_req_wstrb;
    host_valid_q    <= s_host_valid;
    host_side_q     <= s_host_side;
    host_qty_q      <= s_host_qty;
    host_price_q    <= s_host_price;
    host_locate_q   <= s_host_locate;
    host_stock_q    <= s_host_stock;
    host_tif_q      <= s_host_tif;
    host_clordid_q  <= s_host_clordid;
    credit_ret_q    <= ring_credit_ret;
    tready_q        <= m_axis_tx_tready;
  end

  logic tx_en_eff;
  assign tx_en_eff = tx_en_q && st_cfg_valid;

  // ---- strategy_engine ----
  logic                    o_valid;
  logic [STRAT_RULE_W-1:0] o_rule;
  logic [7:0] o_side, o_tif;
  logic [31:0] o_qty, o_price;
  logic [15:0] o_locate;
  logic [63:0] o_stock, o_seq, o_sof_ts;
  logic [CFG_EPOCH_W-1:0] o_epoch;

  strategy_engine u_strategy (
      .clk            (clk),
      .rst            (rst_q),
      .s_ev_valid     (ev_valid_q),
      .s_ev_class     (ev_class_q),
      .s_ev_type      (ev_type_q),
      .s_ev_seq       (ev_seq_q),
      .s_ev_sof_ts    (ev_sof_ts_q),
      .s_ev_epoch     (ev_epoch_q),
      .s_ev_locate    (ev_locate_q),
      .s_ev_shares    (ev_shares_q),
      .s_ev_price     (ev_price_q),
      .s_ev_side      (ev_side_q),
      .s_ev_stock     (ev_stock_q),
      .s_pkt_valid    (pkt_valid_q),
      .s_pkt_status   (pkt_status_q),
      .ctl_kill       (kill_q),
      .ctl_tx_en      (tx_en_eff),
      .cfg_rule_wr    (rule_wr_q),
      .cfg_rule_idx   (rule_idx_q),
      .cfg_rule       (rule_q),
      .cmd_disarm     (disarm_q),
      .cmd_disarm_mask(disarm_mask_q),
      .cmd_arm        (arm_q),
      .cmd_arm_mask   (arm_mask_q),
      .m_ord_valid    (o_valid),
      .m_ord_rule     (o_rule),
      .m_ord_side     (o_side),
      .m_ord_qty      (o_qty),
      .m_ord_price    (o_price),
      .m_ord_locate   (o_locate),
      .m_ord_stock    (o_stock),
      .m_ord_tif      (o_tif),
      .m_ord_seq      (o_seq),
      .m_ord_sof_ts   (o_sof_ts),
      .m_ord_epoch    (o_epoch),
      .st_armed       (st_armed),
      .st_error       (st_error)
  );

  // ---- risk_check ----
  logic t_valid, t_ready;
  logic       [            31:0] t_user_ref;
  order_src_e                    t_source;
  logic       [STRAT_RULE_W-1:0] t_rule;
  logic [7:0] t_side, t_tif;
  logic [31:0] t_qty, t_price;
  logic [15:0] t_locate;
  logic [63:0] t_stock, t_seq, t_sof_ts;
  logic [          111:0] t_clordid;
  logic [CFG_EPOCH_W-1:0] t_epoch;

  risk_check u_risk (
      .clk               (clk),
      .rst               (rst_q),
      .ts_now            (ts_q),
      .epoch_now         (epoch_now_q),
      .s_hw_valid        (o_valid),
      .s_hw_rule         (o_rule),
      .s_hw_side         (o_side),
      .s_hw_qty          (o_qty),
      .s_hw_price        (o_price),
      .s_hw_locate       (o_locate),
      .s_hw_stock        (o_stock),
      .s_hw_tif          (o_tif),
      .s_hw_seq          (o_seq),
      .s_hw_sof_ts       (o_sof_ts),
      .s_hw_epoch        (o_epoch),
      .s_host_valid      (host_valid_q),
      .s_host_ready      (s_host_ready),
      .s_host_side       (host_side_q),
      .s_host_qty        (host_qty_q),
      .s_host_price      (host_price_q),
      .s_host_locate     (host_locate_q),
      .s_host_stock      (host_stock_q),
      .s_host_tif        (host_tif_q),
      .s_host_clordid    (host_clordid_q),
      .ctl_kill          (kill_q),
      .ctl_tx_en         (tx_en_eff),
      .cfg_lim_wr        (lim_wr_q),
      .cfg_max_qty       (max_qty_q),
      .cfg_max_notional  (max_notional_q),
      .cfg_rate_refill   (refill_q),
      .cfg_rate_period   (period_q),
      .cfg_rate_burst    (burst_q),
      .cmd_budget_add    (budget_add_q),
      .cmd_budget_amount (budget_amount_q),
      .cmd_userref_wr    (userref_wr_q),
      .cmd_userref_value (userref_value_q),
      .ev_userref_ignored(ev_userref_ignored),
      .sym_req_valid     (sym_valid_q),
      .sym_req_ready     (sym_req_ready),
      .sym_req_wr        (sym_wr_q),
      .sym_req_addr      (sym_addr_q),
      .sym_req_wdata     (sym_wdata_q),
      .sym_req_wstrb     (sym_wstrb_q),
      .sym_rsp_valid     (sym_rsp_valid),
      .sym_rsp_rdata     (sym_rsp_rdata),
      .m_tx_valid        (t_valid),
      .m_tx_ready        (t_ready),
      .m_tx_user_ref     (t_user_ref),
      .m_tx_source       (t_source),
      .m_tx_rule         (t_rule),
      .m_tx_side         (t_side),
      .m_tx_qty          (t_qty),
      .m_tx_price        (t_price),
      .m_tx_locate       (t_locate),
      .m_tx_stock        (t_stock),
      .m_tx_tif          (t_tif),
      .m_tx_clordid      (t_clordid),
      .m_tx_seq          (t_seq),
      .m_tx_sof_ts       (t_sof_ts),
      .m_tx_epoch        (t_epoch),
      .m_rej_valid       (m_rej_valid),
      .m_rej_reason      (m_rej_reason),
      .m_rej_source      (m_rej_source),
      .m_rej_rule        (m_rej_rule),
      .m_rej_side        (m_rej_side),
      .m_rej_qty         (m_rej_qty),
      .m_rej_price       (m_rej_price),
      .m_rej_locate      (m_rej_locate),
      .m_rej_stock       (m_rej_stock),
      .m_rej_tif         (m_rej_tif),
      .m_rej_clordid     (m_rej_clordid),
      .m_rej_seq         (m_rej_seq),
      .m_rej_sof_ts      (m_rej_sof_ts),
      .m_rej_epoch       (m_rej_epoch),
      .m_rej_ts          (m_rej_ts),
      .ring_credit_ret   (credit_ret_q),
      .ev_rej_dropped    (ev_rej_dropped),
      .st_budget         (st_budget)
  );

  // ---- order_tx_builder ----
  order_tx_builder u_tx (
      .clk             (clk),
      .rst             (rst_q),
      .ts_now          (ts_q),
      .cfg_wr          (tx_wr_q),
      .cfg_template    (tmpl_q),
      .cfg_display     (display_q),
      .cfg_capacity    (capacity_q),
      .cfg_ise         (ise_q),
      .cfg_cross_type  (cross_q),
      .st_cfg_valid    (st_cfg_valid),
      .ev_cfg_refused  (ev_cfg_refused),
      .s_ord_valid     (t_valid),
      .s_ord_ready     (t_ready),
      .s_ord_user_ref  (t_user_ref),
      .s_ord_source    (t_source),
      .s_ord_rule      (t_rule),
      .s_ord_side      (t_side),
      .s_ord_qty       (t_qty),
      .s_ord_price     (t_price),
      .s_ord_locate    (t_locate),
      .s_ord_stock     (t_stock),
      .s_ord_tif       (t_tif),
      .s_ord_clordid   (t_clordid),
      .s_ord_seq       (t_seq),
      .s_ord_sof_ts    (t_sof_ts),
      .s_ord_epoch     (t_epoch),
      .m_axis_tx_tdata (m_axis_tx_tdata),
      .m_axis_tx_tkeep (m_axis_tx_tkeep),
      .m_axis_tx_tlast (m_axis_tx_tlast),
      .m_axis_tx_tvalid(m_axis_tx_tvalid),
      .m_axis_tx_tready(tready_q),
      .m_sent_valid    (m_sent_valid),
      .m_sent_user_ref (m_sent_user_ref),
      .m_sent_source   (m_sent_source),
      .m_sent_rule     (m_sent_rule),
      .m_sent_side     (m_sent_side),
      .m_sent_qty      (m_sent_qty),
      .m_sent_price    (m_sent_price),
      .m_sent_locate   (m_sent_locate),
      .m_sent_stock    (m_sent_stock),
      .m_sent_tif      (m_sent_tif),
      .m_sent_clordid  (m_sent_clordid),
      .m_sent_seq      (m_sent_seq),
      .m_sent_sof_ts   (m_sent_sof_ts),
      .m_sent_epoch    (m_sent_epoch),
      .m_sent_tx_ts    (m_sent_tx_ts)
  );
endmodule
