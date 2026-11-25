// tb_mbox_props: checks the mbox_props checker itself. Drives its ports directly: an order held while the reader
// stalls, then taken, a gap, a second order taken at once. Every property's antecedent is hit, so a clean run
// proves the checker quiet. With HFT_VIOLATE=<k> exactly one property is violated (numbers in
// test_sva_checkers.py); its assertion must fire. Values are set after a falling edge and sampled at the next rising
// edge.
`timescale 1ns / 1ps
module tb_mbox_props;
  logic clk = 1'b0;
  logic rst = 1'b1;
  logic valid = 1'b0, ready = 1'b0;
  logic [15:0] data = '0;

  always begin
    #2 clk = ~clk;
  end

  mbox_props #(
      .W(16)
  ) u_props (
      .clk  (clk),
      .rst  (rst),
      .valid(valid),
      .ready(ready),
      .data (data)
  );

  // 1 when this run violates property k.
  function automatic logic v(input int k);
`ifdef HFT_VIOLATE
    return k == `HFT_VIOLATE;
`else
    return k < 0;
`endif  // HFT_VIOLATE
  endfunction

  task automatic cycle(input logic vl, input logic rd, input logic [15:0] d);
    @(negedge clk);
    valid = vl;
    ready = rd;
    data  = d;
  endtask

  initial begin
    repeat (3) @(negedge clk);
    rst = 1'b0;
    // An order held for three cycles while the reader stalls, then taken.
    cycle(1'b1, 1'b0, 16'hA001);
    cycle(1'b1, 1'b0, v(1) ? 16'hA0FF : 16'hA001);  // 1: a_stall_stable
    cycle(1'b1, 1'b0, v(1) ? 16'hA0FF : 16'hA001);
    cycle(1'b1, 1'b1, v(1) ? 16'hA0FF : 16'hA001);
    // The gap after the take, or (2: a_gap_after_take) the next order at once.
    cycle(v(2), 1'b0, 16'hB002);
    cycle(1'b1, 1'b1, 16'hB002);
    cycle(1'b0, 1'b1, 16'h0000);
    cycle(1'b0, 1'b0, 16'h0000);
    $display("HFT_TB_RESULT: PASS");
    $finish;
  end
endmodule
