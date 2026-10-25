// tb_assert_arm: checks assert_arm (audit/12 S7). An immediate assertion that fails while `bad` is
// high: `bad` is high before the reset and during it, which must fire nothing (assertions off
// until the reset has been seen and released); it falls with the release. With HFT_VIOLATE=1 `bad` rises again after the release and the assertion must fire
// (Verilator stops and no PASS line is printed). Values are set after a falling edge.
`timescale 1ns / 1ps
module tb_assert_arm;
  logic clk = 1'b0;
  logic rst = 1'b0;
  logic bad = 1'b1;

  always begin
    #3.2 clk = ~clk;
  end

  assert_arm u_arm (
      .clk(clk),
      .rst(rst)
  );

  always @(posedge clk) begin
    a_after_arm :
    assert (!bad)
    else begin
      $error("tb_assert_arm: assertion fired while bad is high");
    end
  end

  initial begin
    repeat (3) @(negedge clk);  // before any reset: off
    rst = 1'b1;
    repeat (3) @(negedge clk);  // in reset: off
    rst = 1'b0;  // the next edge arms; bad is low from it on (no race with the arming)
    bad = 1'b0;
    repeat (4) @(negedge clk);
`ifdef HFT_VIOLATE
    bad = 1'b1;
`endif  // HFT_VIOLATE
    repeat (3) @(negedge clk);
    $display("HFT_TB_RESULT: PASS (assert_arm)");
    $finish;
  end
endmodule
