// tb_reset_sync_smoke: self-checking SV smoke test of reset_sync for the run_svtb() flow.
// Runs under Verilator (--binary --timing) and XSIM; prints one HFT_TB_RESULT line.
`timescale 1ns / 1ps
module tb_reset_sync_smoke;
  localparam int unsigned STAGES = 3;
  localparam int unsigned COPIES = 2;

  logic                     clk = 1'b0;
  logic                     rst_in = 1'b1;
  logic        [COPIES-1:0] rst_out;
  int unsigned              errors = 0;
  int unsigned              seed = 1;

  always begin
    #5 clk = ~clk;
  end

  reset_sync #(
      .STAGES(STAGES),
      .COPIES(COPIES)
  ) dut (
      .clk    (clk),
      .rst_in (rst_in),
      .rst_out(rst_out)
  );

  task automatic check(input logic expected, input string what);
    if (rst_out !== {COPIES{expected}}) begin  // every copy, together
      errors++;
      $display("ERROR %s: rst_out='b%b expected %0d on every copy at %0t", what, rst_out, expected,
               $time);
    end
  endtask

  initial begin
    void'($value$plusargs("seed=%d", seed));
    repeat (STAGES + 2) @(posedge clk);
    #1 check(1'b1, "held in reset");

    // Release between edges: rst_out must fall exactly STAGES + 1 rising edges later (the output copies).
    @(negedge clk) rst_in = 1'b0;
    repeat (STAGES) @(posedge clk);
    #1 check(1'b1, "still in reset before STAGES + 1 edges");
    @(posedge clk);
    #1 check(1'b0, "released after STAGES + 1 edges");

    // Asynchronous assert: no clock edge needed (random point within the cycle).
    repeat (1 + $urandom(seed) % 3) #1;
    rst_in = 1'b1;
    #0.5 check(1'b1, "asynchronous assert");

    $display("HFT_TB_RESULT: %s (reset_sync STAGES=%0d COPIES=%0d errors=%0d)",
             errors == 0 ? "PASS" : "FAIL", STAGES, COPIES, errors);
    $finish;
  end
endmodule
