// hft_core_ooc: OOC synthesis harness for HFT_Core, the whole core. Every synchronous input goes through one register
// in its own clock domain first, and every output into one, as the platform's MAC, PCIe bridge and register slices
// take them: all paths, the core's output logic included, are register to register and inside the timing and CDC
// gates (decision 15, audit/15). rst_in goes straight to the core's reset_syncs (it is asynchronous). Every output
// register drives a port, so synthesis keeps all of the logic.
// Not used in simulation.
// Clock domain : net_clk (s_axis_rx_*, m_axis_tx_*), host_clk (s_axil_*, m_axi_hmw_*, irq)
module hft_core_ooc #(
    parameter int unsigned RING_DATA_W     = 512,
    parameter int unsigned RING_DEPTH_LOG2 = 8
) (
    input logic net_clk,
    input logic host_clk,
    input logic rst_in,

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

    output logic [63:0] m_axis_tx_tdata,
    output logic [ 7:0] m_axis_tx_tkeep,
    output logic        m_axis_tx_tvalid,
    input  logic        m_axis_tx_tready,
    output logic        m_axis_tx_tlast,

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
    output logic                     m_axi_hmw_bready,

    output logic irq
);
  logic awvalid_q, wvalid_q, bready_q, arvalid_q, rready_q;
  logic [15:0] awaddr_q, araddr_q;
  logic [2:0] awprot_q, arprot_q;
  logic [31:0] wdata_q;
  logic [ 3:0] wstrb_q;
  logic awready_q, wready_q, bvalid_q;
  logic [ 1:0] bresp_q;
  logic [63:0] rx_tdata_q;
  logic [ 7:0] rx_tkeep_q;
  logic rx_tvalid_q, rx_tlast_q, rx_tuser_q, tx_tready_q;
  // core outputs, before their register
  logic                     s_axil_awready_c;
  logic                     s_axil_wready_c;
  logic                     s_axil_bvalid_c;
  logic [              1:0] s_axil_bresp_c;
  logic                     s_axil_arready_c;
  logic                     s_axil_rvalid_c;
  logic [             31:0] s_axil_rdata_c;
  logic [              1:0] s_axil_rresp_c;
  logic [             63:0] m_axi_hmw_awaddr_c;
  logic [              7:0] m_axi_hmw_awlen_c;
  logic [              2:0] m_axi_hmw_awsize_c;
  logic [              1:0] m_axi_hmw_awburst_c;
  logic                     m_axi_hmw_awvalid_c;
  logic [  RING_DATA_W-1:0] m_axi_hmw_wdata_c;
  logic [RING_DATA_W/8-1:0] m_axi_hmw_wstrb_c;
  logic                     m_axi_hmw_wlast_c;
  logic                     m_axi_hmw_wvalid_c;
  logic                     m_axi_hmw_bready_c;
  logic                     irq_c;
  logic                     s_axis_rx_tready_c;
  logic [             63:0] m_axis_tx_tdata_c;
  logic [              7:0] m_axis_tx_tkeep_c;
  logic                     m_axis_tx_tvalid_c;
  logic                     m_axis_tx_tlast_c;

  always_ff @(posedge host_clk) begin
    awvalid_q         <= s_axil_awvalid;
    awaddr_q          <= s_axil_awaddr;
    awprot_q          <= s_axil_awprot;
    wvalid_q          <= s_axil_wvalid;
    wdata_q           <= s_axil_wdata;
    wstrb_q           <= s_axil_wstrb;
    bready_q          <= s_axil_bready;
    arvalid_q         <= s_axil_arvalid;
    araddr_q          <= s_axil_araddr;
    arprot_q          <= s_axil_arprot;
    rready_q          <= s_axil_rready;
    awready_q         <= m_axi_hmw_awready;
    wready_q          <= m_axi_hmw_wready;
    bresp_q           <= m_axi_hmw_bresp;
    bvalid_q          <= m_axi_hmw_bvalid;
    s_axil_awready    <= s_axil_awready_c;
    s_axil_wready     <= s_axil_wready_c;
    s_axil_bvalid     <= s_axil_bvalid_c;
    s_axil_bresp      <= s_axil_bresp_c;
    s_axil_arready    <= s_axil_arready_c;
    s_axil_rvalid     <= s_axil_rvalid_c;
    s_axil_rdata      <= s_axil_rdata_c;
    s_axil_rresp      <= s_axil_rresp_c;
    m_axi_hmw_awaddr  <= m_axi_hmw_awaddr_c;
    m_axi_hmw_awlen   <= m_axi_hmw_awlen_c;
    m_axi_hmw_awsize  <= m_axi_hmw_awsize_c;
    m_axi_hmw_awburst <= m_axi_hmw_awburst_c;
    m_axi_hmw_awvalid <= m_axi_hmw_awvalid_c;
    m_axi_hmw_wdata   <= m_axi_hmw_wdata_c;
    m_axi_hmw_wstrb   <= m_axi_hmw_wstrb_c;
    m_axi_hmw_wlast   <= m_axi_hmw_wlast_c;
    m_axi_hmw_wvalid  <= m_axi_hmw_wvalid_c;
    m_axi_hmw_bready  <= m_axi_hmw_bready_c;
    irq               <= irq_c;
  end

  always_ff @(posedge net_clk) begin
    rx_tdata_q       <= s_axis_rx_tdata;
    rx_tkeep_q       <= s_axis_rx_tkeep;
    rx_tvalid_q      <= s_axis_rx_tvalid;
    rx_tlast_q       <= s_axis_rx_tlast;
    rx_tuser_q       <= s_axis_rx_tuser;
    tx_tready_q      <= m_axis_tx_tready;
    s_axis_rx_tready <= s_axis_rx_tready_c;
    m_axis_tx_tdata  <= m_axis_tx_tdata_c;
    m_axis_tx_tkeep  <= m_axis_tx_tkeep_c;
    m_axis_tx_tvalid <= m_axis_tx_tvalid_c;
    m_axis_tx_tlast  <= m_axis_tx_tlast_c;
  end

  HFT_Core #(
      .RING_DATA_W    (RING_DATA_W),
      .RING_DEPTH_LOG2(RING_DEPTH_LOG2)
  ) u_core (
      .net_clk          (net_clk),
      .host_clk         (host_clk),
      .rst_in           (rst_in),
      .s_axil_awvalid   (awvalid_q),
      .s_axil_awready   (s_axil_awready_c),
      .s_axil_awaddr    (awaddr_q),
      .s_axil_awprot    (awprot_q),
      .s_axil_wvalid    (wvalid_q),
      .s_axil_wready    (s_axil_wready_c),
      .s_axil_wdata     (wdata_q),
      .s_axil_wstrb     (wstrb_q),
      .s_axil_bvalid    (s_axil_bvalid_c),
      .s_axil_bready    (bready_q),
      .s_axil_bresp     (s_axil_bresp_c),
      .s_axil_arvalid   (arvalid_q),
      .s_axil_arready   (s_axil_arready_c),
      .s_axil_araddr    (araddr_q),
      .s_axil_arprot    (arprot_q),
      .s_axil_rvalid    (s_axil_rvalid_c),
      .s_axil_rready    (rready_q),
      .s_axil_rdata     (s_axil_rdata_c),
      .s_axil_rresp     (s_axil_rresp_c),
      .s_axis_rx_tdata  (rx_tdata_q),
      .s_axis_rx_tkeep  (rx_tkeep_q),
      .s_axis_rx_tvalid (rx_tvalid_q),
      .s_axis_rx_tready (s_axis_rx_tready_c),
      .s_axis_rx_tlast  (rx_tlast_q),
      .s_axis_rx_tuser  (rx_tuser_q),
      .m_axis_tx_tdata  (m_axis_tx_tdata_c),
      .m_axis_tx_tkeep  (m_axis_tx_tkeep_c),
      .m_axis_tx_tvalid (m_axis_tx_tvalid_c),
      .m_axis_tx_tready (tx_tready_q),
      .m_axis_tx_tlast  (m_axis_tx_tlast_c),
      .m_axi_hmw_awaddr (m_axi_hmw_awaddr_c),
      .m_axi_hmw_awlen  (m_axi_hmw_awlen_c),
      .m_axi_hmw_awsize (m_axi_hmw_awsize_c),
      .m_axi_hmw_awburst(m_axi_hmw_awburst_c),
      .m_axi_hmw_awvalid(m_axi_hmw_awvalid_c),
      .m_axi_hmw_awready(awready_q),
      .m_axi_hmw_wdata  (m_axi_hmw_wdata_c),
      .m_axi_hmw_wstrb  (m_axi_hmw_wstrb_c),
      .m_axi_hmw_wlast  (m_axi_hmw_wlast_c),
      .m_axi_hmw_wvalid (m_axi_hmw_wvalid_c),
      .m_axi_hmw_wready (wready_q),
      .m_axi_hmw_bresp  (bresp_q),
      .m_axi_hmw_bvalid (bvalid_q),
      .m_axi_hmw_bready (m_axi_hmw_bready_c),
      .irq              (irq_c)
  );
endmodule
