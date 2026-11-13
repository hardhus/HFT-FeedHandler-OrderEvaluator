// hft_csr_regs: the CSR map's register blocks behind one AXI4-Lite port (decision 41). axil_route2 sends
// every access in the order blocks' windows (RISK 0x0A00-0x0BFF, STRATEGY 0x1000-0x1FFF, TX 0x2000-0x2FFF)
// to hft_order_csr and every other one to hft_csr; both decode absolute addresses and answer SLVERR for an
// unmapped address or an illegal access. hft_csr has RISK, STRATEGY and TX as external blocks that never see
// an access: their acks are tied low.
// Clock domain : clk (host_clk)
// Latency      : +1 cycle over the register block alone (axil_route2); accesses to one block overlap, a switch
//                to the other block waits until every earlier access is answered (decision 43)
// Note         : a register block placed in a window later would answer SLVERR (test_csr_map checks the map).
module hft_csr_regs (
    input logic clk,
    input logic rst,

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

    // hft_csr's hwif; its RISK, STRATEGY and TX members are not used (tied here)
    input  hft_csr_pkg::hft_csr__in_t              hwif_in,
    output hft_csr_pkg::hft_csr__out_t             hwif_out,
    // hft_order_csr's hwif: RISK, STRATEGY and TX
    input  hft_order_csr_pkg::hft_order_csr__in_t  ohwif_in,
    output hft_order_csr_pkg::hft_order_csr__out_t ohwif_out
);
  import hft_pkg::*;

  logic [1:0] awvalid, awready, wvalid, wready, bvalid, bready, arvalid, arready, rvalid, rready;
  logic [15:0] awaddr, araddr;
  logic [2:0] awprot, arprot;
  logic [31:0] wdata;
  logic [3:0] wstrb, bresp, rresp;
  logic [63:0] rdata;

  axil_route2 #(
      .ADDR_W  (16),
      .DATA_W  (32),
      .N_WIN   (CSR_ORDER_N_WIN),
      .WIN_BASE(CSR_ORDER_WIN_BASE),
      .WIN_MASK(CSR_ORDER_WIN_MASK)
  ) u_route (
      .clk           (clk),
      .rst           (rst),
      .s_axil_awvalid(s_axil_awvalid),
      .s_axil_awready(s_axil_awready),
      .s_axil_awaddr (s_axil_awaddr),
      .s_axil_awprot (s_axil_awprot),
      .s_axil_wvalid (s_axil_wvalid),
      .s_axil_wready (s_axil_wready),
      .s_axil_wdata  (s_axil_wdata),
      .s_axil_wstrb  (s_axil_wstrb),
      .s_axil_bvalid (s_axil_bvalid),
      .s_axil_bready (s_axil_bready),
      .s_axil_bresp  (s_axil_bresp),
      .s_axil_arvalid(s_axil_arvalid),
      .s_axil_arready(s_axil_arready),
      .s_axil_araddr (s_axil_araddr),
      .s_axil_arprot (s_axil_arprot),
      .s_axil_rvalid (s_axil_rvalid),
      .s_axil_rready (s_axil_rready),
      .s_axil_rdata  (s_axil_rdata),
      .s_axil_rresp  (s_axil_rresp),
      .m_axil_awvalid(awvalid),
      .m_axil_awready(awready),
      .m_axil_awaddr (awaddr),
      .m_axil_awprot (awprot),
      .m_axil_wvalid (wvalid),
      .m_axil_wready (wready),
      .m_axil_wdata  (wdata),
      .m_axil_wstrb  (wstrb),
      .m_axil_bvalid (bvalid),
      .m_axil_bready (bready),
      .m_axil_bresp  (bresp),
      .m_axil_arvalid(arvalid),
      .m_axil_arready(arready),
      .m_axil_araddr (araddr),
      .m_axil_arprot (arprot),
      .m_axil_rvalid (rvalid),
      .m_axil_rready (rready),
      .m_axil_rdata  (rdata),
      .m_axil_rresp  (rresp)
  );

  hft_csr_pkg::hft_csr__in_t csr_in;
  always_comb begin
    csr_in          = hwif_in;
    csr_in.RISK     = '{default: '0};
    csr_in.STRATEGY = '{default: '0};
    csr_in.TX       = '{default: '0};
  end

  hft_csr u_csr (
      .clk           (clk),
      .rst           (rst),
      .s_axil_awready(awready[0]),
      .s_axil_awvalid(awvalid[0]),
      .s_axil_awaddr (awaddr),
      .s_axil_awprot (awprot),
      .s_axil_wready (wready[0]),
      .s_axil_wvalid (wvalid[0]),
      .s_axil_wdata  (wdata),
      .s_axil_wstrb  (wstrb),
      .s_axil_bready (bready[0]),
      .s_axil_bvalid (bvalid[0]),
      .s_axil_bresp  (bresp[1:0]),
      .s_axil_arready(arready[0]),
      .s_axil_arvalid(arvalid[0]),
      .s_axil_araddr (araddr),
      .s_axil_arprot (arprot),
      .s_axil_rready (rready[0]),
      .s_axil_rvalid (rvalid[0]),
      .s_axil_rdata  (rdata[31:0]),
      .s_axil_rresp  (rresp[1:0]),
      .hwif_in       (csr_in),
      .hwif_out      (hwif_out)
  );

  hft_order_csr u_ocsr (
      .clk           (clk),
      .rst           (rst),
      .s_axil_awready(awready[1]),
      .s_axil_awvalid(awvalid[1]),
      .s_axil_awaddr (awaddr),
      .s_axil_awprot (awprot),
      .s_axil_wready (wready[1]),
      .s_axil_wvalid (wvalid[1]),
      .s_axil_wdata  (wdata),
      .s_axil_wstrb  (wstrb),
      .s_axil_bready (bready[1]),
      .s_axil_bvalid (bvalid[1]),
      .s_axil_bresp  (bresp[3:2]),
      .s_axil_arready(arready[1]),
      .s_axil_arvalid(arvalid[1]),
      .s_axil_araddr (araddr),
      .s_axil_arprot (arprot),
      .s_axil_rready (rready[1]),
      .s_axil_rvalid (rvalid[1]),
      .s_axil_rdata  (rdata[63:32]),
      .s_axil_rresp  (rresp[3:2]),
      .hwif_in       (ohwif_in),
      .hwif_out      (ohwif_out)
  );
endmodule
