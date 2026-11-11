// rx_csr_top: simulation-only system top: eth_ip_udp_rx, moldudp64_rx, itch_decoder, event_packer, the commit FIFO
// and event_ring_writer, configured and counted through the host CSR block (hft_csr_wrap), with one asynchronous
// reset source and one reset_sync per clock domain.
// Clock domain : host_clk (s_axil_*, m_axi_hmw_*, ring_fifo_underflow), net_clk (s_axis_rx_*, ts_now, m_axis_udp_*,
//                m_udp_*, m_msg_*, m_pkt_*, m_ev_*, m_res_*, ring_fifo_overflow)
// Latency      : those of its blocks
// ITCH         : moldudp64_rx carries the ITCH config per packet: cfg_msg_ctx = {cfg_epoch, cfg_class_filter,
//                cfg_class_en}. The counter banks in hft_csr_wrap count itch_decoder's forwarded
//                packet events and results; m_pkt_* are moldudp64_rx's own,
//                m_fwd_pkt_* the forwarded ones.
// Ring         : itch_decoder's events and forwarded packet events go through event_packer and the COMMIT=1 FIFO
//                to event_ring_writer, which writes host memory through m_axi_hmw_* (audit/13 §2.2).
// Order path   : until S8 (audit/14) there is no strategy_engine, risk_check or order_tx_builder: hft_csr_wrap's
//                order configuration goes nowhere (order_cfg_unused), its status inputs are constants (no
//                rule error, budget 0, UserRefNum 1, no TX config), the mailbox is never read, and LOCATE_ALLOW
//                is a symbol_filter of its own so that its accesses answer. ctl_kill and ctl_tx_en are ports.
// Note         : stands for the platform shell of F8 (MAC, PCIe bridge and clocking there).
module rx_csr_top
  import hft_pkg::*;
#(
    localparam int unsigned CTX_W           = CFG_EPOCH_W + 2 * ITCH_N_CLASS,  // {epoch, class_filter, class_en}
    localparam int unsigned RING_DATA_W = 512,
    localparam int unsigned RING_DEPTH_LOG2 = 8
) (
    input logic host_clk,
    input logic net_clk,
    input logic rst_in,    // async, active-high, one source for both domains

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

    input  logic [63:0] s_axis_rx_tdata,
    input  logic [ 7:0] s_axis_rx_tkeep,
    input  logic        s_axis_rx_tvalid,
    output logic        s_axis_rx_tready,
    input  logic        s_axis_rx_tlast,
    input  logic        s_axis_rx_tuser,
    input  logic [63:0] ts_now,

    output logic [63:0] m_axis_udp_tdata,
    output logic [ 7:0] m_axis_udp_tkeep,
    output logic        m_axis_udp_tvalid,
    output logic        m_axis_udp_tlast,
    output logic        m_axis_udp_tuser,
    output logic [63:0] m_udp_sof_ts,
    output logic [31:0] m_udp_src_ip,
    output logic [31:0] m_udp_dst_ip,
    output logic [15:0] m_udp_dst_port,
    output logic [15:0] m_udp_len,
    output logic [ 2:0] m_udp_filter_idx,

    output logic                      m_msg_valid,
    output logic [8*MOLD_MAX_MSG-1:0] m_msg_data,
    output logic [MOLD_MSG_LEN_W-1:0] m_msg_len,
    output logic [              63:0] m_msg_seq,
    output logic [              63:0] m_msg_sof_ts,
    output logic [         CTX_W-1:0] m_msg_ctx,

    output logic                     m_pkt_valid,
    output mold_status_e             m_pkt_status,
    output logic         [     63:0] m_pkt_seq,
    output logic         [     15:0] m_pkt_count,
    output logic         [     63:0] m_pkt_gap,
    output logic         [     15:0] m_pkt_msgs,
    output logic         [     15:0] m_pkt_dup_msgs,
    output logic         [     15:0] m_pkt_oversize,
    output logic                     m_pkt_synced_now,
    output logic         [     63:0] m_pkt_sof_ts,
    output logic         [CTX_W-1:0] m_pkt_ctx,

    // itch_decoder events and results
    output logic          [   CFG_EPOCH_W-1:0] m_ev_epoch,
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

    // itch_decoder's forwarded packet events (what the counter banks count)
    output logic                           m_fwd_pkt_valid,
    output mold_status_e                   m_fwd_pkt_status,
    output logic         [           63:0] m_fwd_pkt_seq,
    output logic         [           15:0] m_fwd_pkt_count,
    output logic         [           63:0] m_fwd_pkt_gap,
    output logic         [           15:0] m_fwd_pkt_msgs,
    output logic         [           15:0] m_fwd_pkt_dup_msgs,
    output logic         [           15:0] m_fwd_pkt_oversize,
    output logic                           m_fwd_pkt_synced_now,
    output logic         [           63:0] m_fwd_pkt_sof_ts,
    output logic         [CFG_EPOCH_W-1:0] m_fwd_pkt_epoch,

    // Commit FIFO: a write that found it full (event_packer drops the packet), a read of it empty (never)
    output logic                         ring_fifo_overflow,
    output logic                         ring_fifo_underflow,
    // event_packer's order queue (its inputs are tied off until S8)
    output logic                         ring_credit_ret,
    output logic [RING_ORD_QUEUED_W-1:0] ring_ord_queued,
    // Order path controls (hft_csr_wrap; their users come in S8)
    output logic                         ctl_kill,
    output logic                         ctl_tx_en,

    // Host memory
    output logic [             63:0] m_axi_hmw_awaddr,
    output logic [              7:0] m_axi_hmw_awlen,
    output logic [              2:0] m_axi_hmw_awsize,
    output logic [              1:0] m_axi_hmw_awburst,
    output logic                     m_axi_hmw_awvalid,
    input  logic                     m_axi_hmw_awready,
    output logic [  RING_DATA_W-1:0] m_axi_hmw_wdata,
    output logic [RING_DATA_W/8-1:0] m_axi_hmw_wstrb,
    output logic                     m_axi_hmw_wlast,
    output logic                     m_axi_hmw_wvalid,
    input  logic                     m_axi_hmw_wready,
    input  logic [              1:0] m_axi_hmw_bresp,
    input  logic                     m_axi_hmw_bvalid,
    output logic                     m_axi_hmw_bready
);
  localparam int unsigned N_FILTERS = 8;

  logic host_rst, net_rst;
  logic cfg_rx_en, cfg_accept_bcast;
  logic       [         47:0]       cfg_local_mac;
  logic       [N_FILTERS-1:0][31:0] cfg_filter_ip;
  logic       [N_FILTERS-1:0][15:0] cfg_filter_port;
  logic       [N_FILTERS-1:0]       cfg_filter_en;
  logic                             stat_valid;
  rx_reason_e                       stat_reason;
  logic       [         15:0]       stat_bytes;
  logic cfg_feed_en, cfg_session_check, cfg_resync, cfg_resync_learn;
  logic [         79:0] cfg_session;
  logic [N_FILTERS-1:0] cfg_filter_mask;
  logic [         63:0] cfg_resync_seq;
  logic [         63:0] feed_expected_seq;
  logic feed_synced, feed_eos;
  logic [ITCH_N_CLASS-1:0] cfg_class_en, cfg_class_filter;
  logic [CFG_EPOCH_W-1:0] cfg_epoch;
  logic sym_req_valid, sym_req_ready, sym_req_wr, sym_rsp_valid;
  logic [10:0] sym_req_addr;
  logic [31:0] sym_req_wdata, sym_rsp_rdata;
  logic [3:0] sym_req_wstrb;
  logic ring_enable, ring_bresp_err, ring_enable_err;
  logic [63:0] ring_base, ring_cnt_written, ring_cnt_bresp_err;
  logic [4:0] ring_size_log2;
  logic [31:0] ring_cons, ring_prod_idx;
  logic [63:0] ring_commit_entries, ring_drop_pkts, ring_drop_entries;
  logic fifo_wr_en, fifo_commit, fifo_rollback, fifo_full, fifo_wr_busy;
  logic [RING_DEPTH_LOG2:0] fifo_wr_count_unused;  // the descriptor FIFO needs no fill level (WR_COUNT=0)
  logic fifo_rd_en, fifo_empty, fifo_rd_busy;
  logic [event_desc_pkg::DESC_W-1:0] fifo_din, fifo_dout;
  // Order path until S8 (header)
  logic cfg_rule_wr, cmd_arm, cmd_disarm, cfg_lim_wr, cmd_budget_add, cmd_userref_wr, cfg_tx_wr;
  logic        [STRAT_RULE_W-1:0] cfg_rule_idx;
  strat_rule_t                    cfg_rule;
  logic [STRAT_N_RULES-1:0] cmd_arm_mask, cmd_disarm_mask;
  logic [31:0]
      cfg_max_qty,
      cfg_rate_refill,
      cfg_rate_period,
      cfg_rate_burst,
      cmd_budget_amount,
      cmd_userref_value;
  logic [    63:0] cfg_max_notional;
  logic [8*42-1:0] cfg_template;
  logic [7:0] cfg_display, cfg_capacity, cfg_ise, cfg_cross_type;
  logic loc_req_valid, loc_req_ready, loc_req_wr, loc_rsp_valid, loc_hit_unused;
  logic [10:0] loc_req_addr;
  logic [31:0] loc_req_wdata, loc_rsp_rdata;
  logic [3:0] loc_req_wstrb;
  logic       m_host_valid;
  logic [7:0] m_host_side, m_host_tif;
  logic [31:0] m_host_qty, m_host_price;
  logic [ 15:0] m_host_locate;
  logic [ 63:0] m_host_stock;
  logic [111:0] m_host_clordid;
  logic         order_cfg_unused;

  reset_sync u_host_rst (
      .clk    (host_clk),
      .rst_in (rst_in),
      .rst_out(host_rst)
  );

  reset_sync u_net_rst (
      .clk    (net_clk),
      .rst_in (rst_in),
      .rst_out(net_rst)
  );

  hft_csr_wrap u_wrap (
      .host_clk               (host_clk),
      .host_rst               (host_rst),
      .s_axil_awvalid         (s_axil_awvalid),
      .s_axil_awready         (s_axil_awready),
      .s_axil_awaddr          (s_axil_awaddr),
      .s_axil_awprot          (s_axil_awprot),
      .s_axil_wvalid          (s_axil_wvalid),
      .s_axil_wready          (s_axil_wready),
      .s_axil_wdata           (s_axil_wdata),
      .s_axil_wstrb           (s_axil_wstrb),
      .s_axil_bvalid          (s_axil_bvalid),
      .s_axil_bready          (s_axil_bready),
      .s_axil_bresp           (s_axil_bresp),
      .s_axil_arvalid         (s_axil_arvalid),
      .s_axil_arready         (s_axil_arready),
      .s_axil_araddr          (s_axil_araddr),
      .s_axil_arprot          (s_axil_arprot),
      .s_axil_rvalid          (s_axil_rvalid),
      .s_axil_rready          (s_axil_rready),
      .s_axil_rdata           (s_axil_rdata),
      .s_axil_rresp           (s_axil_rresp),
      .net_clk                (net_clk),
      .net_rst                (net_rst),
      .cfg_rx_en              (cfg_rx_en),
      .cfg_local_mac          (cfg_local_mac),
      .cfg_accept_bcast       (cfg_accept_bcast),
      .cfg_filter_ip          (cfg_filter_ip),
      .cfg_filter_port        (cfg_filter_port),
      .cfg_filter_en          (cfg_filter_en),
      .stat_valid             (stat_valid),
      .stat_reason            (stat_reason),
      .stat_bytes             (stat_bytes),
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
      .host_ring_enable       (ring_enable),
      .host_ring_base         (ring_base),
      .host_ring_size_log2    (ring_size_log2),
      .host_ring_cons         (ring_cons),
      .host_ring_prod_idx     (ring_prod_idx),
      .host_ring_bresp_err    (ring_bresp_err),
      .host_ring_enable_err   (ring_enable_err),
      .host_ring_cnt_written  (ring_cnt_written),
      .host_ring_cnt_bresp_err(ring_cnt_bresp_err),
      .cnt_ring_commit_entries(ring_commit_entries),
      .cnt_ring_drop_pkts     (ring_drop_pkts),
      .cnt_ring_drop_entries  (ring_drop_entries),
      .mpkt_valid             (m_fwd_pkt_valid),
      .mpkt_status            (m_fwd_pkt_status),
      .mpkt_gap               (m_fwd_pkt_gap),
      .mpkt_msgs              (m_fwd_pkt_msgs),
      .mpkt_dup_msgs          (m_fwd_pkt_dup_msgs),
      .mpkt_oversize          (m_fwd_pkt_oversize),
      .mres_valid             (m_res_valid),
      .mres_class             (m_res_class),
      .mres_outcome           (m_res_outcome),
      .feed_expected_seq      (feed_expected_seq),
      .feed_synced            (feed_synced),
      .feed_eos               (feed_eos),
      .sym_req_valid          (sym_req_valid),
      .sym_req_ready          (sym_req_ready),
      .sym_req_wr             (sym_req_wr),
      .sym_req_addr           (sym_req_addr),
      .sym_req_wdata          (sym_req_wdata),
      .sym_req_wstrb          (sym_req_wstrb),
      .sym_rsp_valid          (sym_rsp_valid),
      .sym_rsp_rdata          (sym_rsp_rdata),
      .ctl_kill               (ctl_kill),
      .ctl_tx_en              (ctl_tx_en),
      .cfg_rule_wr            (cfg_rule_wr),
      .cfg_rule_idx           (cfg_rule_idx),
      .cfg_rule               (cfg_rule),
      .cmd_arm                (cmd_arm),
      .cmd_arm_mask           (cmd_arm_mask),
      .cmd_disarm             (cmd_disarm),
      .cmd_disarm_mask        (cmd_disarm_mask),
      .st_armed               ('0),
      .st_error               ('0),
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
      .ev_userref_ignored     (1'b0),
      .st_userref             (32'd1),
      .st_budget              ('0),
      .loc_req_valid          (loc_req_valid),
      .loc_req_ready          (loc_req_ready),
      .loc_req_wr             (loc_req_wr),
      .loc_req_addr           (loc_req_addr),
      .loc_req_wdata          (loc_req_wdata),
      .loc_req_wstrb          (loc_req_wstrb),
      .loc_rsp_valid          (loc_rsp_valid),
      .loc_rsp_rdata          (loc_rsp_rdata),
      .cfg_tx_wr              (cfg_tx_wr),
      .cfg_template           (cfg_template),
      .cfg_display            (cfg_display),
      .cfg_capacity           (cfg_capacity),
      .cfg_ise                (cfg_ise),
      .cfg_cross_type         (cfg_cross_type),
      .st_tx_cfg_valid        (1'b0),
      .ev_cfg_refused         (1'b0),
      .m_host_valid           (m_host_valid),
      .m_host_ready           (1'b0),
      .m_host_side            (m_host_side),
      .m_host_qty             (m_host_qty),
      .m_host_price           (m_host_price),
      .m_host_locate          (m_host_locate),
      .m_host_stock           (m_host_stock),
      .m_host_tif             (m_host_tif),
      .m_host_clordid         (m_host_clordid),
      .ord_fire_valid         (1'b0),
      .ord_fire_rule          ('0),
      .ord_sent_valid         (1'b0),
      .ord_sent_source        (ORDER_SRC_RULE),
      .ord_rej_valid          (1'b0),
      .ord_rej_reason         (RISK_NONE),
      .ord_lost_valid         (1'b0),
      .ord_lost_reason        (RISK_NONE),
      .ord_queued             (ring_ord_queued)
  );

  // Until S8: the order configuration has no user, and LOCATE_ALLOW is a bitmap of its own (header).
  assign order_cfg_unused = ^{
    cfg_rule_wr, cfg_rule_idx, cfg_rule, cmd_arm, cmd_arm_mask, cmd_disarm, cmd_disarm_mask, cfg_lim_wr,
    cfg_max_qty, cfg_max_notional, cfg_rate_refill, cfg_rate_period, cfg_rate_burst, cmd_budget_add,
    cmd_budget_amount, cmd_userref_wr, cmd_userref_value, cfg_tx_wr, cfg_template, cfg_display, cfg_capacity,
    cfg_ise, cfg_cross_type, m_host_valid, m_host_side, m_host_qty, m_host_price, m_host_locate, m_host_stock,
    m_host_tif, m_host_clordid
  };

  symbol_filter u_locate_allow (
      .clk          (net_clk),
      .rst          (net_rst),
      .lk_locate    ('0),
      .lk_hit       (loc_hit_unused),
      .sym_req_valid(loc_req_valid),
      .sym_req_ready(loc_req_ready),
      .sym_req_wr   (loc_req_wr),
      .sym_req_addr (loc_req_addr),
      .sym_req_wdata(loc_req_wdata),
      .sym_req_wstrb(loc_req_wstrb),
      .sym_rsp_valid(loc_rsp_valid),
      .sym_rsp_rdata(loc_rsp_rdata)
  );

  eth_ip_udp_rx #(
      .N_FILTERS(N_FILTERS)
  ) u_rx (
      .clk              (net_clk),
      .rst              (net_rst),
      .s_axis_rx_tdata  (s_axis_rx_tdata),
      .s_axis_rx_tkeep  (s_axis_rx_tkeep),
      .s_axis_rx_tvalid (s_axis_rx_tvalid),
      .s_axis_rx_tready (s_axis_rx_tready),
      .s_axis_rx_tlast  (s_axis_rx_tlast),
      .s_axis_rx_tuser  (s_axis_rx_tuser),
      .cfg_rx_en        (cfg_rx_en),
      .cfg_local_mac    (cfg_local_mac),
      .cfg_accept_bcast (cfg_accept_bcast),
      .cfg_filter_ip    (cfg_filter_ip),
      .cfg_filter_port  (cfg_filter_port),
      .cfg_filter_en    (cfg_filter_en),
      .ts_now           (ts_now),
      .m_axis_udp_tdata (m_axis_udp_tdata),
      .m_axis_udp_tkeep (m_axis_udp_tkeep),
      .m_axis_udp_tvalid(m_axis_udp_tvalid),
      .m_axis_udp_tlast (m_axis_udp_tlast),
      .m_axis_udp_tuser (m_axis_udp_tuser),
      .m_udp_sof_ts     (m_udp_sof_ts),
      .m_udp_src_ip     (m_udp_src_ip),
      .m_udp_dst_ip     (m_udp_dst_ip),
      .m_udp_dst_port   (m_udp_dst_port),
      .m_udp_len        (m_udp_len),
      .m_udp_filter_idx (m_udp_filter_idx),
      .stat_valid       (stat_valid),
      .stat_reason      (stat_reason),
      .stat_bytes       (stat_bytes)
  );

  moldudp64_rx #(
      .N_FILTERS(N_FILTERS),
      .CTX_W    (CTX_W)
  ) u_mold (
      .clk              (net_clk),
      .rst              (net_rst),
      .s_axis_udp_tdata (m_axis_udp_tdata),
      .s_axis_udp_tkeep (m_axis_udp_tkeep),
      .s_axis_udp_tvalid(m_axis_udp_tvalid),
      .s_axis_udp_tlast (m_axis_udp_tlast),
      .s_axis_udp_tuser (m_axis_udp_tuser),
      .s_udp_len        (m_udp_len),
      .s_udp_filter_idx (m_udp_filter_idx),
      .s_udp_sof_ts     (m_udp_sof_ts),
      .cfg_feed_en      (cfg_feed_en),
      .cfg_session_check(cfg_session_check),
      .cfg_session      (cfg_session),
      .cfg_filter_mask  (cfg_filter_mask),
      .cfg_resync       (cfg_resync),
      .cfg_resync_learn (cfg_resync_learn),
      .cfg_resync_seq   (cfg_resync_seq),
      .cfg_msg_ctx      ({cfg_epoch, cfg_class_filter, cfg_class_en}),
      .m_msg_valid      (m_msg_valid),
      .m_msg_data       (m_msg_data),
      .m_msg_len        (m_msg_len),
      .m_msg_seq        (m_msg_seq),
      .m_msg_sof_ts     (m_msg_sof_ts),
      .m_msg_ctx        (m_msg_ctx),
      .m_pkt_valid      (m_pkt_valid),
      .m_pkt_status     (m_pkt_status),
      .m_pkt_seq        (m_pkt_seq),
      .m_pkt_count      (m_pkt_count),
      .m_pkt_gap        (m_pkt_gap),
      .m_pkt_msgs       (m_pkt_msgs),
      .m_pkt_dup_msgs   (m_pkt_dup_msgs),
      .m_pkt_oversize   (m_pkt_oversize),
      .m_pkt_synced_now (m_pkt_synced_now),
      .m_pkt_sof_ts     (m_pkt_sof_ts),
      .m_pkt_ctx        (m_pkt_ctx),
      .feed_expected_seq(feed_expected_seq),
      .feed_synced      (feed_synced),
      .feed_eos         (feed_eos)
  );

  itch_decoder u_itch (
      .clk               (net_clk),
      .rst               (net_rst),
      .s_msg_valid       (m_msg_valid),
      .s_msg_data        (m_msg_data),
      .s_msg_len         (m_msg_len),
      .s_msg_seq         (m_msg_seq),
      .s_msg_sof_ts      (m_msg_sof_ts),
      .s_msg_class_en    (m_msg_ctx[ITCH_N_CLASS-1:0]),
      .s_msg_class_filter(m_msg_ctx[2*ITCH_N_CLASS-1:ITCH_N_CLASS]),
      .s_msg_epoch       (m_msg_ctx[CTX_W-1:2*ITCH_N_CLASS]),
      .s_pkt_valid       (m_pkt_valid),
      .s_pkt_status      (m_pkt_status),
      .s_pkt_seq         (m_pkt_seq),
      .s_pkt_count       (m_pkt_count),
      .s_pkt_gap         (m_pkt_gap),
      .s_pkt_msgs        (m_pkt_msgs),
      .s_pkt_dup_msgs    (m_pkt_dup_msgs),
      .s_pkt_oversize    (m_pkt_oversize),
      .s_pkt_synced_now  (m_pkt_synced_now),
      .s_pkt_sof_ts      (m_pkt_sof_ts),
      .s_pkt_epoch       (m_pkt_ctx[CTX_W-1:2*ITCH_N_CLASS]),
      .sym_req_valid     (sym_req_valid),
      .sym_req_ready     (sym_req_ready),
      .sym_req_wr        (sym_req_wr),
      .sym_req_addr      (sym_req_addr),
      .sym_req_wdata     (sym_req_wdata),
      .sym_req_wstrb     (sym_req_wstrb),
      .sym_rsp_valid     (sym_rsp_valid),
      .sym_rsp_rdata     (sym_rsp_rdata),
      .m_ev_valid        (m_ev_valid),
      .m_ev_class        (m_ev_class),
      .m_ev_type         (m_ev_type),
      .m_ev_len          (m_ev_len),
      .m_ev_seq          (m_ev_seq),
      .m_ev_sof_ts       (m_ev_sof_ts),
      .m_ev_epoch        (m_ev_epoch),
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
      .m_pkt_valid       (m_fwd_pkt_valid),
      .m_pkt_status      (m_fwd_pkt_status),
      .m_pkt_seq         (m_fwd_pkt_seq),
      .m_pkt_count       (m_fwd_pkt_count),
      .m_pkt_gap         (m_fwd_pkt_gap),
      .m_pkt_msgs        (m_fwd_pkt_msgs),
      .m_pkt_dup_msgs    (m_fwd_pkt_dup_msgs),
      .m_pkt_oversize    (m_fwd_pkt_oversize),
      .m_pkt_synced_now  (m_fwd_pkt_synced_now),
      .m_pkt_sof_ts      (m_fwd_pkt_sof_ts),
      .m_pkt_epoch       (m_fwd_pkt_epoch)
  );

  event_packer u_packer (
      .clk               (net_clk),
      .rst               (net_rst),
      .s_ev_valid        (m_ev_valid),
      .s_ev_class        (m_ev_class),
      .s_ev_type         (m_ev_type),
      .s_ev_len          (m_ev_len),
      .s_ev_seq          (m_ev_seq),
      .s_ev_sof_ts       (m_ev_sof_ts),
      .s_ev_epoch        (m_ev_epoch),
      .s_ev_locate       (m_ev_locate),
      .s_ev_tracking     (m_ev_tracking),
      .s_ev_ts           (m_ev_ts),
      .s_ev_order_ref    (m_ev_order_ref),
      .s_ev_new_ref      (m_ev_new_ref),
      .s_ev_shares       (m_ev_shares),
      .s_ev_price        (m_ev_price),
      .s_ev_match        (m_ev_match),
      .s_ev_side         (m_ev_side),
      .s_ev_stock        (m_ev_stock),
      .s_ev_attribution  (m_ev_attribution),
      .s_ev_printable    (m_ev_printable),
      .s_ev_cross_type   (m_ev_cross_type),
      .s_ev_raw          (m_ev_raw),
      .s_pkt_valid       (m_fwd_pkt_valid),
      .s_pkt_status      (m_fwd_pkt_status),
      .s_pkt_seq         (m_fwd_pkt_seq),
      .s_pkt_gap         (m_fwd_pkt_gap),
      .s_pkt_sof_ts      (m_fwd_pkt_sof_ts),
      .s_pkt_epoch       (m_fwd_pkt_epoch),
      // Order entries: wired in S8 (audit/14)
      .s_sent_valid      ('0),
      .s_sent_user_ref   ('0),
      .s_sent_source     (ORDER_SRC_RULE),
      .s_sent_rule       ('0),
      .s_sent_side       ('0),
      .s_sent_qty        ('0),
      .s_sent_price      ('0),
      .s_sent_locate     ('0),
      .s_sent_stock      ('0),
      .s_sent_tif        ('0),
      .s_sent_clordid    ('0),
      .s_sent_seq        ('0),
      .s_sent_sof_ts     ('0),
      .s_sent_epoch      ('0),
      .s_sent_tx_ts      ('0),
      .s_rej_valid       ('0),
      .s_rej_reason      (RISK_NONE),
      .s_rej_source      (ORDER_SRC_RULE),
      .s_rej_rule        ('0),
      .s_rej_side        ('0),
      .s_rej_qty         ('0),
      .s_rej_price       ('0),
      .s_rej_locate      ('0),
      .s_rej_stock       ('0),
      .s_rej_tif         ('0),
      .s_rej_clordid     ('0),
      .s_rej_seq         ('0),
      .s_rej_sof_ts      ('0),
      .s_rej_epoch       ('0),
      .s_rej_ts          ('0),
      .s_rej_lost        ('0),
      .ring_credit_ret   (ring_credit_ret),
      .fifo_wr_en        (fifo_wr_en),
      .fifo_din          (fifo_din),
      .fifo_commit       (fifo_commit),
      .fifo_rollback     (fifo_rollback),
      .fifo_full         (fifo_full),
      .fifo_wr_busy      (fifo_wr_busy),
      .cnt_commit_entries(ring_commit_entries),
      .cnt_drop_pkts     (ring_drop_pkts),
      .cnt_drop_entries  (ring_drop_entries),
      .cnt_ord_queued    (ring_ord_queued)
  );

  cdc_async_fifo #(
      .WIDTH     (event_desc_pkg::DESC_W),
      .DEPTH_LOG2(RING_DEPTH_LOG2),
      .COMMIT    (1'b1)
  ) u_ring_fifo (
      .rst        (net_rst),
      .wr_clk     (net_clk),
      .wr_en      (fifo_wr_en),
      .din        (fifo_din),
      .wr_commit  (fifo_commit),
      .wr_rollback(fifo_rollback),
      .full       (fifo_full),
      .overflow   (ring_fifo_overflow),
      .wr_rst_busy(fifo_wr_busy),
      .wr_count   (fifo_wr_count_unused),
      .rd_clk     (host_clk),
      .rd_en      (fifo_rd_en),
      .dout       (fifo_dout),
      .empty      (fifo_empty),
      .underflow  (ring_fifo_underflow),
      .rd_rst_busy(fifo_rd_busy)
  );

  event_ring_writer #(
      .DATA_W(RING_DATA_W)
  ) u_ring_writer (
      .clk              (host_clk),
      .rst              (host_rst),
      .fifo_rd_en       (fifo_rd_en),
      .fifo_dout        (fifo_dout),
      .fifo_empty       (fifo_empty),
      .fifo_rd_rst_busy (fifo_rd_busy),
      .cfg_enable       (ring_enable),
      .cfg_base         (ring_base),
      .cfg_size_log2    (ring_size_log2),
      .cfg_cons         (ring_cons),
      .prod_idx         (ring_prod_idx),
      .status_bresp_err (ring_bresp_err),
      .status_enable_err(ring_enable_err),
      .m_axi_hmw_awaddr (m_axi_hmw_awaddr),
      .m_axi_hmw_awlen  (m_axi_hmw_awlen),
      .m_axi_hmw_awsize (m_axi_hmw_awsize),
      .m_axi_hmw_awburst(m_axi_hmw_awburst),
      .m_axi_hmw_awvalid(m_axi_hmw_awvalid),
      .m_axi_hmw_awready(m_axi_hmw_awready),
      .m_axi_hmw_wdata  (m_axi_hmw_wdata),
      .m_axi_hmw_wstrb  (m_axi_hmw_wstrb),
      .m_axi_hmw_wlast  (m_axi_hmw_wlast),
      .m_axi_hmw_wvalid (m_axi_hmw_wvalid),
      .m_axi_hmw_wready (m_axi_hmw_wready),
      .m_axi_hmw_bresp  (m_axi_hmw_bresp),
      .m_axi_hmw_bvalid (m_axi_hmw_bvalid),
      .m_axi_hmw_bready (m_axi_hmw_bready),
      .cnt_written      (ring_cnt_written),
      .cnt_bresp_err    (ring_cnt_bresp_err)
  );
endmodule
