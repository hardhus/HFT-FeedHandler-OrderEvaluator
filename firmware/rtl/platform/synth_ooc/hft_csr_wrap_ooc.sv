// hft_csr_wrap_ooc: OOC synthesis harness for hft_csr_wrap. Every input goes through one register in
// its own clock domain first (the AXI interconnect's and eth_ip_udp_rx's output registers), so the
// bus decode, read mux and counter paths are register to register and inside the timing gate.
// Every output is a port, so synthesis keeps (and the gate checks) all of the logic. The mailbox outputs
// (m_host_*, the cdc_async_fifo's LUTRAM read data) go through one net_clk register, risk_check's input
// register: without it the host_clk write to net_clk read path ended at a port, outside the CDC constraint
// and the gate (S9). That register takes the FIFO head every cycle, like risk_check, so report_cdc lists the
// head's LUTRAM read as CDC-26; cdc.tcl waives it for this FIFO only (decision 42).
// Not used in simulation.
// Clock domain : host_clk (s_axil_*, host_ring_*), net_clk (cfg_*, stat_*, mpkt_*, mres_*, feed_*, sym_*,
//                cnt_ring_*, and the order path: ctl_*, cmd_*, loc_*, m_host_*, st_*, ev_*, ord_*)
module hft_csr_wrap_ooc
  import hft_pkg::*;
#(
    localparam int unsigned N_FILTERS = 8
) (
    input logic host_clk,
    input logic host_rst,

    input  logic        s_axil_awvalid,
    output logic        s_axil_awready,
    input  logic [15:0] s_axil_awaddr,
    input  logic [ 2:0] s_axil_awprot,
    input  logic        s_axil_wvalid,
    output logic        s_axil_wready,
    input  logic [31:0] s_axil_wdata,
    input  logic [ 3:0] s_axil_wstrb,
    output logic        s_axil_bvalid,
    input  logic        s_axil_bready,
    output logic [ 1:0] s_axil_bresp,
    input  logic        s_axil_arvalid,
    output logic        s_axil_arready,
    input  logic [15:0] s_axil_araddr,
    input  logic [ 2:0] s_axil_arprot,
    output logic        s_axil_rvalid,
    input  logic        s_axil_rready,
    output logic [31:0] s_axil_rdata,
    output logic [ 1:0] s_axil_rresp,

    input logic net_clk,
    input logic net_rst,

    output logic                       cfg_rx_en,
    output logic [         47:0]       cfg_local_mac,
    output logic                       cfg_accept_bcast,
    output logic [N_FILTERS-1:0][31:0] cfg_filter_ip,
    output logic [N_FILTERS-1:0][15:0] cfg_filter_port,
    output logic [N_FILTERS-1:0]       cfg_filter_en,

    input logic              stat_valid,
    input rx_reason_e        stat_reason,
    input logic       [15:0] stat_bytes,

    output logic                 cfg_feed_en,
    output logic                 cfg_session_check,
    output logic [         79:0] cfg_session,
    output logic [N_FILTERS-1:0] cfg_filter_mask,
    output logic                 cfg_resync,
    output logic                 cfg_resync_learn,
    output logic [         63:0] cfg_resync_seq,

    output logic [ITCH_N_CLASS-1:0] cfg_class_en,
    output logic [ITCH_N_CLASS-1:0] cfg_class_filter,
    output logic [ CFG_EPOCH_W-1:0] cfg_epoch,

    output logic        host_ring_enable,
    output logic [63:0] host_ring_base,
    output logic [ 4:0] host_ring_size_log2,
    output logic [31:0] host_ring_cons,
    input  logic [31:0] host_ring_prod_idx,
    input  logic        host_ring_bresp_err,
    input  logic        host_ring_enable_err,
    input  logic [63:0] host_ring_cnt_written,
    input  logic [63:0] host_ring_cnt_bresp_err,

    input logic [63:0] cnt_ring_commit_entries,
    input logic [63:0] cnt_ring_drop_pkts,
    input logic [63:0] cnt_ring_drop_entries,

    input logic                 mpkt_valid,
    input mold_status_e         mpkt_status,
    input logic          [63:0] mpkt_gap,
    input logic          [15:0] mpkt_msgs,
    input logic          [15:0] mpkt_dup_msgs,
    input logic          [15:0] mpkt_oversize,
    input logic                 mres_valid,
    input itch_class_e          mres_class,
    input itch_outcome_e        mres_outcome,
    input logic          [63:0] feed_expected_seq,
    input logic                 feed_synced,
    input logic                 feed_eos,

    output logic        sym_req_valid,
    input  logic        sym_req_ready,
    output logic        sym_req_wr,
    output logic [10:0] sym_req_addr,
    output logic [31:0] sym_req_wdata,
    output logic [ 3:0] sym_req_wstrb,
    input  logic        sym_rsp_valid,
    input  logic [31:0] sym_rsp_rdata,

    output logic                            ctl_kill,
    output logic                            ctl_tx_en,
    output logic                            cfg_rule_wr,
    output logic        [ STRAT_RULE_W-1:0] cfg_rule_idx,
    output strat_rule_t                     cfg_rule,
    output logic                            cmd_arm,
    output logic        [STRAT_N_RULES-1:0] cmd_arm_mask,
    output logic                            cmd_disarm,
    output logic        [STRAT_N_RULES-1:0] cmd_disarm_mask,
    input  logic        [STRAT_N_RULES-1:0] st_armed,
    input  logic        [STRAT_N_RULES-1:0] st_error,
    output logic                            cfg_lim_wr,
    output logic        [             31:0] cfg_max_qty,
    output logic        [             63:0] cfg_max_notional,
    output logic        [             31:0] cfg_rate_refill,
    output logic        [             31:0] cfg_rate_period,
    output logic        [             31:0] cfg_rate_burst,
    output logic                            cmd_budget_add,
    output logic        [             31:0] cmd_budget_amount,
    output logic                            cmd_userref_wr,
    output logic        [             31:0] cmd_userref_value,
    input  logic                            ev_userref_ignored,
    input  logic        [             31:0] st_userref,
    input  logic        [             31:0] st_budget,
    output logic                            loc_req_valid,
    input  logic                            loc_req_ready,
    output logic                            loc_req_wr,
    output logic        [             10:0] loc_req_addr,
    output logic        [             31:0] loc_req_wdata,
    output logic        [              3:0] loc_req_wstrb,
    input  logic                            loc_rsp_valid,
    input  logic        [             31:0] loc_rsp_rdata,
    output logic                            cfg_tx_wr,
    output logic        [         8*42-1:0] cfg_template,
    output logic        [              7:0] cfg_display,
    output logic        [              7:0] cfg_capacity,
    output logic        [              7:0] cfg_ise,
    output logic        [              7:0] cfg_cross_type,
    input  logic                            st_tx_cfg_valid,
    input  logic                            ev_cfg_refused,
    output logic                            m_host_valid,
    input  logic                            m_host_ready,
    output logic        [              7:0] m_host_side,
    output logic        [             31:0] m_host_qty,
    output logic        [             31:0] m_host_price,
    output logic        [             15:0] m_host_locate,
    output logic        [             63:0] m_host_stock,
    output logic        [              7:0] m_host_tif,
    output logic        [            111:0] m_host_clordid,

    input logic                                 ord_fire_valid,
    input logic         [     STRAT_RULE_W-1:0] ord_fire_rule,
    input logic                                 ord_sent_valid,
    input order_src_e                           ord_sent_source,
    input logic                                 ord_rej_valid,
    input risk_reason_e                         ord_rej_reason,
    input logic                                 ord_lost_valid,
    input risk_reason_e                         ord_lost_reason,
    input logic         [RING_ORD_QUEUED_W-1:0] ord_queued
);
  logic host_rst_q, awvalid_q, wvalid_q, bready_q, arvalid_q, rready_q;
  logic [15:0] awaddr_q, araddr_q;
  logic [2:0] awprot_q, arprot_q;
  logic [31:0] wdata_q;
  logic [ 3:0] wstrb_q;
  logic net_rst_q, stat_valid_q;
  rx_reason_e        stat_reason_q;
  logic       [15:0] stat_bytes_q;
  logic mpkt_valid_q, feed_synced_q, feed_eos_q;
  mold_status_e mpkt_status_q;
  logic [63:0] mpkt_gap_q, feed_expected_seq_q;
  logic [15:0] mpkt_msgs_q, mpkt_dup_msgs_q, mpkt_oversize_q;
  logic mres_valid_q, sym_req_ready_q, sym_rsp_valid_q;
  itch_class_e          mres_class_q;
  itch_outcome_e        mres_outcome_q;
  logic          [31:0] sym_rsp_rdata_q;
  logic          [31:0] ring_prod_idx_q;
  logic ring_bresp_err_q, ring_enable_err_q;
  logic [63:0] ring_cnt_written_q, ring_cnt_bresp_err_q;
  logic [63:0] cnt_ring_commit_entries_q, cnt_ring_drop_pkts_q, cnt_ring_drop_entries_q;
  logic [STRAT_N_RULES-1:0] st_armed_q, st_error_q;
  logic ev_userref_ignored_q, loc_req_ready_q, loc_rsp_valid_q, st_tx_cfg_valid_q, ev_cfg_refused_q;
  logic m_host_ready_q, ord_fire_valid_q, ord_sent_valid_q, ord_rej_valid_q, ord_lost_valid_q;
  logic [31:0] st_userref_q, st_budget_q, loc_rsp_rdata_q;
  logic       [STRAT_RULE_W-1:0] ord_fire_rule_q;
  order_src_e                    ord_sent_source_q;
  risk_reason_e ord_rej_reason_q, ord_lost_reason_q;
  logic [RING_ORD_QUEUED_W-1:0] ord_queued_q;
  // mailbox outputs before their net_clk register
  logic                         host_valid_d;
  logic [7:0] host_side_d, host_tif_d;
  logic [31:0] host_qty_d, host_price_d;
  logic [ 15:0] host_locate_d;
  logic [ 63:0] host_stock_d;
  logic [111:0] host_clordid_d;

  always_ff @(posedge host_clk) begin
    host_rst_q           <= host_rst;
    awvalid_q            <= s_axil_awvalid;
    awaddr_q             <= s_axil_awaddr;
    awprot_q             <= s_axil_awprot;
    wvalid_q             <= s_axil_wvalid;
    wdata_q              <= s_axil_wdata;
    wstrb_q              <= s_axil_wstrb;
    bready_q             <= s_axil_bready;
    arvalid_q            <= s_axil_arvalid;
    araddr_q             <= s_axil_araddr;
    arprot_q             <= s_axil_arprot;
    rready_q             <= s_axil_rready;
    ring_prod_idx_q      <= host_ring_prod_idx;
    ring_bresp_err_q     <= host_ring_bresp_err;
    ring_enable_err_q    <= host_ring_enable_err;
    ring_cnt_written_q   <= host_ring_cnt_written;
    ring_cnt_bresp_err_q <= host_ring_cnt_bresp_err;
  end

  always_ff @(posedge net_clk) begin
    net_rst_q                 <= net_rst;
    stat_valid_q              <= stat_valid;
    stat_reason_q             <= stat_reason;
    stat_bytes_q              <= stat_bytes;
    mpkt_valid_q              <= mpkt_valid;
    mpkt_status_q             <= mpkt_status;
    mpkt_gap_q                <= mpkt_gap;
    mpkt_msgs_q               <= mpkt_msgs;
    mpkt_dup_msgs_q           <= mpkt_dup_msgs;
    mpkt_oversize_q           <= mpkt_oversize;
    feed_expected_seq_q       <= feed_expected_seq;
    feed_synced_q             <= feed_synced;
    feed_eos_q                <= feed_eos;
    mres_valid_q              <= mres_valid;
    mres_class_q              <= mres_class;
    mres_outcome_q            <= mres_outcome;
    sym_req_ready_q           <= sym_req_ready;
    sym_rsp_valid_q           <= sym_rsp_valid;
    sym_rsp_rdata_q           <= sym_rsp_rdata;
    cnt_ring_commit_entries_q <= cnt_ring_commit_entries;
    cnt_ring_drop_pkts_q      <= cnt_ring_drop_pkts;
    cnt_ring_drop_entries_q   <= cnt_ring_drop_entries;
    st_armed_q                <= st_armed;
    st_error_q                <= st_error;
    ev_userref_ignored_q      <= ev_userref_ignored;
    st_userref_q              <= st_userref;
    st_budget_q               <= st_budget;
    loc_req_ready_q           <= loc_req_ready;
    loc_rsp_valid_q           <= loc_rsp_valid;
    loc_rsp_rdata_q           <= loc_rsp_rdata;
    st_tx_cfg_valid_q         <= st_tx_cfg_valid;
    ev_cfg_refused_q          <= ev_cfg_refused;
    m_host_ready_q            <= m_host_ready;
    ord_fire_valid_q          <= ord_fire_valid;
    ord_fire_rule_q           <= ord_fire_rule;
    ord_sent_valid_q          <= ord_sent_valid;
    ord_sent_source_q         <= ord_sent_source;
    ord_rej_valid_q           <= ord_rej_valid;
    ord_rej_reason_q          <= ord_rej_reason;
    ord_lost_valid_q          <= ord_lost_valid;
    ord_lost_reason_q         <= ord_lost_reason;
    ord_queued_q              <= ord_queued;
  end

  hft_csr_wrap u_wrap (
      .host_clk               (host_clk),
      .host_rst               (host_rst_q),
      .s_axil_awvalid         (awvalid_q),
      .s_axil_awready         (s_axil_awready),
      .s_axil_awaddr          (awaddr_q),
      .s_axil_awprot          (awprot_q),
      .s_axil_wvalid          (wvalid_q),
      .s_axil_wready          (s_axil_wready),
      .s_axil_wdata           (wdata_q),
      .s_axil_wstrb           (wstrb_q),
      .s_axil_bvalid          (s_axil_bvalid),
      .s_axil_bready          (bready_q),
      .s_axil_bresp           (s_axil_bresp),
      .s_axil_arvalid         (arvalid_q),
      .s_axil_arready         (s_axil_arready),
      .s_axil_araddr          (araddr_q),
      .s_axil_arprot          (arprot_q),
      .s_axil_rvalid          (s_axil_rvalid),
      .s_axil_rready          (rready_q),
      .s_axil_rdata           (s_axil_rdata),
      .s_axil_rresp           (s_axil_rresp),
      .net_clk                (net_clk),
      .net_rst                (net_rst_q),
      .cfg_rx_en              (cfg_rx_en),
      .cfg_local_mac          (cfg_local_mac),
      .cfg_accept_bcast       (cfg_accept_bcast),
      .cfg_filter_ip          (cfg_filter_ip),
      .cfg_filter_port        (cfg_filter_port),
      .cfg_filter_en          (cfg_filter_en),
      .stat_valid             (stat_valid_q),
      .stat_reason            (stat_reason_q),
      .stat_bytes             (stat_bytes_q),
      .cfg_feed_en            (cfg_feed_en),
      .cfg_session_check      (cfg_session_check),
      .cfg_session            (cfg_session),
      .cfg_filter_mask        (cfg_filter_mask),
      .cfg_resync             (cfg_resync),
      .cfg_resync_learn       (cfg_resync_learn),
      .cfg_resync_seq         (cfg_resync_seq),
      .cfg_class_en           (cfg_class_en),
      .cfg_class_filter       (cfg_class_filter),
      .cfg_epoch              (cfg_epoch),
      .host_ring_enable       (host_ring_enable),
      .host_ring_base         (host_ring_base),
      .host_ring_size_log2    (host_ring_size_log2),
      .host_ring_cons         (host_ring_cons),
      .host_ring_prod_idx     (ring_prod_idx_q),
      .host_ring_bresp_err    (ring_bresp_err_q),
      .host_ring_enable_err   (ring_enable_err_q),
      .host_ring_cnt_written  (ring_cnt_written_q),
      .host_ring_cnt_bresp_err(ring_cnt_bresp_err_q),
      .cnt_ring_commit_entries(cnt_ring_commit_entries_q),
      .cnt_ring_drop_pkts     (cnt_ring_drop_pkts_q),
      .cnt_ring_drop_entries  (cnt_ring_drop_entries_q),
      .mpkt_valid             (mpkt_valid_q),
      .mpkt_status            (mpkt_status_q),
      .mpkt_gap               (mpkt_gap_q),
      .mpkt_msgs              (mpkt_msgs_q),
      .mpkt_dup_msgs          (mpkt_dup_msgs_q),
      .mpkt_oversize          (mpkt_oversize_q),
      .mres_valid             (mres_valid_q),
      .mres_class             (mres_class_q),
      .mres_outcome           (mres_outcome_q),
      .feed_expected_seq      (feed_expected_seq_q),
      .feed_synced            (feed_synced_q),
      .feed_eos               (feed_eos_q),
      .sym_req_valid          (sym_req_valid),
      .sym_req_ready          (sym_req_ready_q),
      .sym_req_wr             (sym_req_wr),
      .sym_req_addr           (sym_req_addr),
      .sym_req_wdata          (sym_req_wdata),
      .sym_req_wstrb          (sym_req_wstrb),
      .sym_rsp_valid          (sym_rsp_valid_q),
      .sym_rsp_rdata          (sym_rsp_rdata_q),
      .ctl_kill               (ctl_kill),
      .ctl_tx_en              (ctl_tx_en),
      .cfg_rule_wr            (cfg_rule_wr),
      .cfg_rule_idx           (cfg_rule_idx),
      .cfg_rule               (cfg_rule),
      .cmd_arm                (cmd_arm),
      .cmd_arm_mask           (cmd_arm_mask),
      .cmd_disarm             (cmd_disarm),
      .cmd_disarm_mask        (cmd_disarm_mask),
      .st_armed               (st_armed_q),
      .st_error               (st_error_q),
      .cfg_lim_wr             (cfg_lim_wr),
      .cfg_max_qty            (cfg_max_qty),
      .cfg_max_notional       (cfg_max_notional),
      .cfg_rate_refill        (cfg_rate_refill),
      .cfg_rate_period        (cfg_rate_period),
      .cfg_rate_burst         (cfg_rate_burst),
      .cmd_budget_add         (cmd_budget_add),
      .cmd_budget_amount      (cmd_budget_amount),
      .cmd_userref_wr         (cmd_userref_wr),
      .cmd_userref_value      (cmd_userref_value),
      .ev_userref_ignored     (ev_userref_ignored_q),
      .st_userref             (st_userref_q),
      .st_budget              (st_budget_q),
      .loc_req_valid          (loc_req_valid),
      .loc_req_ready          (loc_req_ready_q),
      .loc_req_wr             (loc_req_wr),
      .loc_req_addr           (loc_req_addr),
      .loc_req_wdata          (loc_req_wdata),
      .loc_req_wstrb          (loc_req_wstrb),
      .loc_rsp_valid          (loc_rsp_valid_q),
      .loc_rsp_rdata          (loc_rsp_rdata_q),
      .cfg_tx_wr              (cfg_tx_wr),
      .cfg_template           (cfg_template),
      .cfg_display            (cfg_display),
      .cfg_capacity           (cfg_capacity),
      .cfg_ise                (cfg_ise),
      .cfg_cross_type         (cfg_cross_type),
      .st_tx_cfg_valid        (st_tx_cfg_valid_q),
      .ev_cfg_refused         (ev_cfg_refused_q),
      .m_host_valid           (host_valid_d),
      .m_host_ready           (m_host_ready_q),
      .m_host_side            (host_side_d),
      .m_host_qty             (host_qty_d),
      .m_host_price           (host_price_d),
      .m_host_locate          (host_locate_d),
      .m_host_stock           (host_stock_d),
      .m_host_tif             (host_tif_d),
      .m_host_clordid         (host_clordid_d),
      .ord_fire_valid         (ord_fire_valid_q),
      .ord_fire_rule          (ord_fire_rule_q),
      .ord_sent_valid         (ord_sent_valid_q),
      .ord_sent_source        (ord_sent_source_q),
      .ord_rej_valid          (ord_rej_valid_q),
      .ord_rej_reason         (ord_rej_reason_q),
      .ord_lost_valid         (ord_lost_valid_q),
      .ord_lost_reason        (ord_lost_reason_q),
      .ord_queued             (ord_queued_q)
  );

  always_ff @(posedge net_clk) begin
    m_host_valid   <= host_valid_d;
    m_host_side    <= host_side_d;
    m_host_qty     <= host_qty_d;
    m_host_price   <= host_price_d;
    m_host_locate  <= host_locate_d;
    m_host_stock   <= host_stock_d;
    m_host_tif     <= host_tif_d;
    m_host_clordid <= host_clordid_d;
  end
endmodule
