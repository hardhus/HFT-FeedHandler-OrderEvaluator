// tb_axis_props: checks the axis_props checker itself. Drives its ports directly: a beat stalled
// for several cycles with its payload held, then accepted, and a tlast beat with a non-empty
// tkeep, so a clean run proves the checker quiet. With HFT_VIOLATE=<k> exactly one property is
// violated (numbers in test_sva_checkers.py); its assertion must fire (Verilator stops and no
// PASS line is printed). Values change after a falling edge.
`timescale 1ns / 1ps
module tb_axis_props;
  logic        clk = 1'b0;
  logic        rst = 1'b1;
  logic        tvalid = 1'b0;
  logic        tready = 1'b0;
  logic        tlast = 1'b0;
  logic [15:0] tdata = '0;
  logic [ 1:0] tkeep = '0;
  logic        tuser = 1'b0;

  always begin
    #3.2 clk = ~clk;
  end

  axis_props #(
      .DATA_W(16),
      .USER_W(1)
  ) u_props (
      .clk   (clk),
      .rst   (rst),
      .tvalid(tvalid),
      .tready(tready),
      .tlast (tlast),
      .tdata (tdata),
      .tkeep (tkeep),
      .tuser (tuser)
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
    // A beat stalled for three cycles (1: a_stall_stable): payload and tvalid must hold.
    tvalid = 1'b1;
    tdata  = 16'h1234;
    tkeep  = 2'b11;
    repeat (2) @(negedge clk);
    tdata = v(1) ? 16'h4321 : 16'h1234;
    @(negedge clk);
    tready = 1'b1;  // accepted
    @(negedge clk);
    // The last beat carries a byte (2: a_last_keep).
    tdata = 16'h00AB;
    tkeep = v(2) ? 2'b00 : 2'b01;
    tlast = 1'b1;
    @(negedge clk);
    tvalid = 1'b0;
    tlast  = 1'b0;
    repeat (3) @(negedge clk);
    $display("HFT_TB_RESULT: PASS (axis_props)");
    $finish;
  end
endmodule
