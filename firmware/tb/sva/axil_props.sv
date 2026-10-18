// axil_props: AXI4-Lite protocol checks at a subordinate's port (bind to the s_axil_* ports).
// Simulation only. Disabled until the first reset has been seen, because flops start with
// random values (--x-initial unique).
// Checks the manager side (AW, W, AR: the testbench drivers) and the subordinate side (B, R):
// - a channel that raised VALID keeps it and its payload until the handshake (all 5 channels);
// - a response needs a request: B only while an accepted AW and an accepted W have no B yet,
//   R only while an accepted AR has no R yet. The counts are cleared by rst together with the
//   subordinate, so this also forbids BVALID/RVALID after reset (legacy K7/K8: lost or invented
//   responses).
module axil_props #(
    parameter int unsigned ADDR_W = 16,
    parameter int unsigned DATA_W = 32
) (
    input logic clk,
    input logic rst,

    input logic              awvalid,
    input logic              awready,
    input logic [ADDR_W-1:0] awaddr,
    input logic [       2:0] awprot,

    input logic                wvalid,
    input logic                wready,
    input logic [  DATA_W-1:0] wdata,
    input logic [DATA_W/8-1:0] wstrb,

    input logic       bvalid,
    input logic       bready,
    input logic [1:0] bresp,

    input logic              arvalid,
    input logic              arready,
    input logic [ADDR_W-1:0] araddr,
    input logic [       2:0] arprot,

    input logic              rvalid,
    input logic              rready,
    input logic [DATA_W-1:0] rdata,
    input logic [       1:0] rresp
);
  logic armed;  // sim-only: initial + always (not always_ff) on purpose
  logic [31:0] aw_n, w_n, b_n, ar_n, r_n;  // accepted handshakes per channel since reset

  initial begin
    armed = 1'b0;
  end
  always @(posedge clk) begin
    if (rst) begin
      armed <= 1'b1;
    end
  end

  always @(posedge clk) begin
    if (rst) begin
      aw_n <= '0;
      w_n  <= '0;
      b_n  <= '0;
      ar_n <= '0;
      r_n  <= '0;
    end else begin
      aw_n <= aw_n + 32'(awvalid && awready);
      w_n  <= w_n + 32'(wvalid && wready);
      b_n  <= b_n + 32'(bvalid && bready);
      ar_n <= ar_n + 32'(arvalid && arready);
      r_n  <= r_n + 32'(rvalid && rready);
    end
  end

  a_aw_stable :
  assert property (@(posedge clk) disable iff (rst || !armed)
      awvalid && !awready |=> awvalid && $stable(
      {awaddr, awprot}
  ))
  else begin
    $error("axil_props: AW changed or AWVALID dropped before AWREADY");
  end

  a_w_stable :
  assert property (@(posedge clk) disable iff (rst || !armed)
      wvalid && !wready |=> wvalid && $stable(
      {wdata, wstrb}
  ))
  else begin
    $error("axil_props: W changed or WVALID dropped before WREADY");
  end

  a_ar_stable :
  assert property (@(posedge clk) disable iff (rst || !armed)
      arvalid && !arready |=> arvalid && $stable(
      {araddr, arprot}
  ))
  else begin
    $error("axil_props: AR changed or ARVALID dropped before ARREADY");
  end

  a_b_stable :
  assert property (@(posedge clk) disable iff (rst || !armed)
      bvalid && !bready |=> bvalid && $stable(
      bresp
  ))
  else begin
    $error("axil_props: BRESP changed or BVALID dropped before BREADY");
  end

  a_r_stable :
  assert property (@(posedge clk) disable iff (rst || !armed)
      rvalid && !rready |=> rvalid && $stable(
      {rdata, rresp}
  ))
  else begin
    $error("axil_props: R changed or RVALID dropped before RREADY");
  end

  a_b_count :
  assert property (@(posedge clk) disable iff (rst || !armed) bvalid |-> b_n < aw_n && b_n < w_n)
  else begin
    $error("axil_props: BVALID without an accepted write that has no response yet");
  end

  a_r_count :
  assert property (@(posedge clk) disable iff (rst || !armed) rvalid |-> r_n < ar_n)
  else begin
    $error("axil_props: RVALID without an accepted read that has no response yet");
  end
endmodule
