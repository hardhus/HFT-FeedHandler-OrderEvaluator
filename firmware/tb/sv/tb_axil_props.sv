// tb_axil_props: checks the axil_props checker itself. Drives its ports directly: on each of the
// five channels a transfer stalled for two cycles with its payload held, then accepted, and one
// B and one R that answer an accepted request, so a clean run proves the checker quiet. With
// HFT_VIOLATE=<k> exactly one property is violated (numbers in test_sva_checkers.py); its
// assertion must fire (Verilator stops and no PASS line is printed). Values change after a
// falling edge.
`timescale 1ns / 1ps
module tb_axil_props;
  logic clk = 1'b0;
  logic rst = 1'b1;
  logic awvalid = 1'b0, awready = 1'b0;
  logic [15:0] awaddr = '0;
  logic [ 2:0] awprot = '0;
  logic wvalid = 1'b0, wready = 1'b0;
  logic [31:0] wdata = '0;
  logic [ 3:0] wstrb = '0;
  logic bvalid = 1'b0, bready = 1'b0;
  logic [1:0] bresp = '0;
  logic arvalid = 1'b0, arready = 1'b0;
  logic [15:0] araddr = '0;
  logic [ 2:0] arprot = '0;
  logic rvalid = 1'b0, rready = 1'b0;
  logic [31:0] rdata = '0;
  logic [ 1:0] rresp = '0;

  always begin
    #2.0 clk = ~clk;
  end

  axil_props #(
      .ADDR_W(16),
      .DATA_W(32)
  ) u_props (
      .clk    (clk),
      .rst    (rst),
      .awvalid(awvalid),
      .awready(awready),
      .awaddr (awaddr),
      .awprot (awprot),
      .wvalid (wvalid),
      .wready (wready),
      .wdata  (wdata),
      .wstrb  (wstrb),
      .bvalid (bvalid),
      .bready (bready),
      .bresp  (bresp),
      .arvalid(arvalid),
      .arready(arready),
      .araddr (araddr),
      .arprot (arprot),
      .rvalid (rvalid),
      .rready (rready),
      .rdata  (rdata),
      .rresp  (rresp)
  );

  // 1 when this run violates property k.
  function automatic logic v(input int k);
`ifdef HFT_VIOLATE
    return k == `HFT_VIOLATE;
`else
    return k < 0;
`endif  // HFT_VIOLATE
  endfunction

  initial begin
    repeat (2) @(negedge clk);
    rst = 1'b0;
    @(negedge clk);
    // AW stalled two cycles, then accepted (1: a_aw_stable).
    awvalid = 1'b1;
    awaddr  = 16'h0010;
    repeat (2) @(negedge clk);
    awaddr  = v(1) ? 16'h0014 : 16'h0010;
    awready = 1'b1;
    @(negedge clk);
    {awvalid, awready} = '0;
    awaddr             = 16'hDEAD;  // after the handshake the payload may change
    // W stalled two cycles, then accepted (2: a_w_stable).
    wvalid             = 1'b1;
    wdata              = 32'h1234_5678;
    wstrb              = 4'hF;
    repeat (2) @(negedge clk);
    wstrb  = v(2) ? 4'h1 : 4'hF;
    wready = 1'b1;
    @(negedge clk);
    {wvalid, wready} = '0;
    // B for the accepted write, stalled two cycles (4: a_b_stable).
    bvalid           = 1'b1;
    bresp            = 2'b00;
    repeat (2) @(negedge clk);
    bresp  = v(4) ? 2'b10 : 2'b00;
    bready = 1'b1;
    @(negedge clk);
    {bvalid, bready} = '0;
    // AR stalled two cycles, then accepted (3: a_ar_stable).
    arvalid          = 1'b1;
    araddr           = 16'h0400;
    repeat (2) @(negedge clk);
    arprot  = v(3) ? 3'b010 : 3'b000;
    arready = 1'b1;
    @(negedge clk);
    {arvalid, arready} = '0;
    // R for the accepted read, stalled two cycles (5: a_r_stable).
    rvalid             = 1'b1;
    rdata              = 32'h4846_5431;
    repeat (2) @(negedge clk);
    rdata  = v(5) ? 32'h0 : 32'h4846_5431;
    rready = 1'b1;
    @(negedge clk);
    {rvalid, rready} = '0;
    // Every request has its response now: a B (6: a_b_count) or an R (7: a_r_count) would be
    // invented. Accepted at once, so the stability properties do not apply.
    {bvalid, bready} = {v(6), 1'b1};
    {rvalid, rready} = {v(7), 1'b1};
    @(negedge clk);
    {bvalid, bready, rvalid, rready} = '0;
    repeat (3) @(negedge clk);
    $display("HFT_TB_RESULT: PASS (axil_props)");
    $finish;
  end
endmodule
