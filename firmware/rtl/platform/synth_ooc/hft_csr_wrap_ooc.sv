// hft_csr_wrap_ooc: OOC synthesis harness for hft_csr_wrap. Every input goes through one register in
// its own clock domain first (the AXI interconnect's and eth_ip_udp_rx's output registers), so the
// bus decode, read mux and counter paths are register to register and inside the timing gate.
// Every output is a port, so synthesis keeps (and the gate checks) all of the logic.
// Not used in simulation.
// Clock domain : host_clk (s_axil_*), net_clk (cfg_*, stat_*, mpkt_*, feed_*)
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

    input logic                mpkt_valid,
    input mold_status_e        mpkt_status,
    input logic         [63:0] mpkt_gap,
    input logic         [15:0] mpkt_msgs,
    input logic         [15:0] mpkt_dup_msgs,
    input logic         [15:0] mpkt_oversize,
    input logic         [63:0] feed_expected_seq,
    input logic                feed_synced,
    input logic                feed_eos
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

  always_ff @(posedge host_clk) begin
    host_rst_q <= host_rst;
    awvalid_q  <= s_axil_awvalid;
    awaddr_q   <= s_axil_awaddr;
    awprot_q   <= s_axil_awprot;
    wvalid_q   <= s_axil_wvalid;
    wdata_q    <= s_axil_wdata;
    wstrb_q    <= s_axil_wstrb;
    bready_q   <= s_axil_bready;
    arvalid_q  <= s_axil_arvalid;
    araddr_q   <= s_axil_araddr;
    arprot_q   <= s_axil_arprot;
    rready_q   <= s_axil_rready;
  end

  always_ff @(posedge net_clk) begin
    net_rst_q           <= net_rst;
    stat_valid_q        <= stat_valid;
    stat_reason_q       <= stat_reason;
    stat_bytes_q        <= stat_bytes;
    mpkt_valid_q        <= mpkt_valid;
    mpkt_status_q       <= mpkt_status;
    mpkt_gap_q          <= mpkt_gap;
    mpkt_msgs_q         <= mpkt_msgs;
    mpkt_dup_msgs_q     <= mpkt_dup_msgs;
    mpkt_oversize_q     <= mpkt_oversize;
    feed_expected_seq_q <= feed_expected_seq;
    feed_synced_q       <= feed_synced;
    feed_eos_q          <= feed_eos;
  end

  hft_csr_wrap u_wrap (
      .host_clk         (host_clk),
      .host_rst         (host_rst_q),
      .s_axil_awvalid   (awvalid_q),
      .s_axil_awready   (s_axil_awready),
      .s_axil_awaddr    (awaddr_q),
      .s_axil_awprot    (awprot_q),
      .s_axil_wvalid    (wvalid_q),
      .s_axil_wready    (s_axil_wready),
      .s_axil_wdata     (wdata_q),
      .s_axil_wstrb     (wstrb_q),
      .s_axil_bvalid    (s_axil_bvalid),
      .s_axil_bready    (bready_q),
      .s_axil_bresp     (s_axil_bresp),
      .s_axil_arvalid   (arvalid_q),
      .s_axil_arready   (s_axil_arready),
      .s_axil_araddr    (araddr_q),
      .s_axil_arprot    (arprot_q),
      .s_axil_rvalid    (s_axil_rvalid),
      .s_axil_rready    (rready_q),
      .s_axil_rdata     (s_axil_rdata),
      .s_axil_rresp     (s_axil_rresp),
      .net_clk          (net_clk),
      .net_rst          (net_rst_q),
      .cfg_rx_en        (cfg_rx_en),
      .cfg_local_mac    (cfg_local_mac),
      .cfg_accept_bcast (cfg_accept_bcast),
      .cfg_filter_ip    (cfg_filter_ip),
      .cfg_filter_port  (cfg_filter_port),
      .cfg_filter_en    (cfg_filter_en),
      .stat_valid       (stat_valid_q),
      .stat_reason      (stat_reason_q),
      .stat_bytes       (stat_bytes_q),
      .cfg_feed_en      (cfg_feed_en),
      .cfg_session_check(cfg_session_check),
      .cfg_session      (cfg_session),
      .cfg_filter_mask  (cfg_filter_mask),
      .cfg_resync       (cfg_resync),
      .cfg_resync_learn (cfg_resync_learn),
      .cfg_resync_seq   (cfg_resync_seq),
      .mpkt_valid       (mpkt_valid_q),
      .mpkt_status      (mpkt_status_q),
      .mpkt_gap         (mpkt_gap_q),
      .mpkt_msgs        (mpkt_msgs_q),
      .mpkt_dup_msgs    (mpkt_dup_msgs_q),
      .mpkt_oversize    (mpkt_oversize_q),
      .feed_expected_seq(feed_expected_seq_q),
      .feed_synced      (feed_synced_q),
      .feed_eos         (feed_eos_q)
  );
endmodule
