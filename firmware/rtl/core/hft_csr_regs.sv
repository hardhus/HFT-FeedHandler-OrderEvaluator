// hft_csr_regs: the CSR map's register blocks behind one AXI4-Lite port (decision 41). axil_cpuif2 sends
// every access in the order blocks' windows (RISK 0x0A00-0x0BFF, STRATEGY 0x1000-0x1FFF, TX 0x2000-0x2FFF)
// to hft_order_csr and every other one to hft_csr, through their PeakRDL passthrough interfaces with the
// request from flops (A40); both decode absolute addresses and answer SLVERR for an unmapped address or an
// illegal access. hft_csr has RISK, STRATEGY and TX as external blocks that never see an access: their acks
// are tied low.
// Clock domain : clk (host_clk)
// Latency      : +1 cycle over a register block with its own AXI4-Lite interface (axil_cpuif2); accesses to one
//                block overlap, a switch to the other block waits until every earlier access is answered
//                (decision 43)
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

  // index 0 = hft_csr, 1 = hft_order_csr
  logic [1:0] req, stall_wr, stall_rd, rd_ack, rd_err, wr_ack, wr_err;
  logic        req_is_wr;
  logic [15:0] addr;
  logic [31:0] wr_data, wr_biten;
  logic [63:0] rd_data;

  axil_cpuif2 #(
      .ADDR_W  (16),
      .DATA_W  (32),
      .N_WIN   (CSR_ORDER_N_WIN),
      .WIN_BASE(CSR_ORDER_WIN_BASE),
      .WIN_MASK(CSR_ORDER_WIN_MASK)
  ) u_bridge (
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
      .m_req         (req),
      .m_req_is_wr   (req_is_wr),
      .m_addr        (addr),
      .m_wr_data     (wr_data),
      .m_wr_biten    (wr_biten),
      .m_req_stall_wr(stall_wr),
      .m_req_stall_rd(stall_rd),
      .m_rd_ack      (rd_ack),
      .m_rd_err      (rd_err),
      .m_rd_data     (rd_data),
      .m_wr_ack      (wr_ack),
      .m_wr_err      (wr_err)
  );

  hft_csr_pkg::hft_csr__in_t csr_in;
  always_comb begin
    csr_in          = hwif_in;
    csr_in.RISK     = '{default: '0};
    csr_in.STRATEGY = '{default: '0};
    csr_in.TX       = '{default: '0};
  end

  hft_csr u_csr (
      .clk                 (clk),
      .rst                 (rst),
      .s_cpuif_req         (req[0]),
      .s_cpuif_req_is_wr   (req_is_wr),
      .s_cpuif_addr        (addr),
      .s_cpuif_wr_data     (wr_data),
      .s_cpuif_wr_biten    (wr_biten),
      .s_cpuif_req_stall_wr(stall_wr[0]),
      .s_cpuif_req_stall_rd(stall_rd[0]),
      .s_cpuif_rd_ack      (rd_ack[0]),
      .s_cpuif_rd_err      (rd_err[0]),
      .s_cpuif_rd_data     (rd_data[31:0]),
      .s_cpuif_wr_ack      (wr_ack[0]),
      .s_cpuif_wr_err      (wr_err[0]),
      .hwif_in             (csr_in),
      .hwif_out            (hwif_out)
  );

  hft_order_csr u_ocsr (
      .clk                 (clk),
      .rst                 (rst),
      .s_cpuif_req         (req[1]),
      .s_cpuif_req_is_wr   (req_is_wr),
      .s_cpuif_addr        (addr),
      .s_cpuif_wr_data     (wr_data),
      .s_cpuif_wr_biten    (wr_biten),
      .s_cpuif_req_stall_wr(stall_wr[1]),
      .s_cpuif_req_stall_rd(stall_rd[1]),
      .s_cpuif_rd_ack      (rd_ack[1]),
      .s_cpuif_rd_err      (rd_err[1]),
      .s_cpuif_rd_data     (rd_data[63:32]),
      .s_cpuif_wr_ack      (wr_ack[1]),
      .s_cpuif_wr_err      (wr_err[1]),
      .hwif_in             (ohwif_in),
      .hwif_out            (ohwif_out)
  );
endmodule
