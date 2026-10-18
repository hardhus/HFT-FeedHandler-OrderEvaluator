// rx_csr_top: simulation-only system top: eth_ip_udp_rx configured and counted through the host CSR
// block (hft_csr_wrap), with one asynchronous reset source and one reset_sync per clock domain.
// Clock domain : host_clk (s_axil_*), net_clk (s_axis_rx_*, ts_now, m_axis_udp_*, m_udp_*)
// Latency      : those of hft_csr_wrap and eth_ip_udp_rx
// Note         : stands for the platform shell of F8 (MAC, PCIe bridge and clocking there).
module rx_csr_top
  import hft_pkg::*;
(
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
    output logic [ 2:0] m_udp_filter_idx
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
      .host_clk        (host_clk),
      .host_rst        (host_rst),
      .s_axil_awvalid  (s_axil_awvalid),
      .s_axil_awready  (s_axil_awready),
      .s_axil_awaddr   (s_axil_awaddr),
      .s_axil_awprot   (s_axil_awprot),
      .s_axil_wvalid   (s_axil_wvalid),
      .s_axil_wready   (s_axil_wready),
      .s_axil_wdata    (s_axil_wdata),
      .s_axil_wstrb    (s_axil_wstrb),
      .s_axil_bvalid   (s_axil_bvalid),
      .s_axil_bready   (s_axil_bready),
      .s_axil_bresp    (s_axil_bresp),
      .s_axil_arvalid  (s_axil_arvalid),
      .s_axil_arready  (s_axil_arready),
      .s_axil_araddr   (s_axil_araddr),
      .s_axil_arprot   (s_axil_arprot),
      .s_axil_rvalid   (s_axil_rvalid),
      .s_axil_rready   (s_axil_rready),
      .s_axil_rdata    (s_axil_rdata),
      .s_axil_rresp    (s_axil_rresp),
      .net_clk         (net_clk),
      .net_rst         (net_rst),
      .cfg_rx_en       (cfg_rx_en),
      .cfg_local_mac   (cfg_local_mac),
      .cfg_accept_bcast(cfg_accept_bcast),
      .cfg_filter_ip   (cfg_filter_ip),
      .cfg_filter_port (cfg_filter_port),
      .cfg_filter_en   (cfg_filter_en),
      .stat_valid      (stat_valid),
      .stat_reason     (stat_reason),
      .stat_bytes      (stat_bytes)
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
endmodule
