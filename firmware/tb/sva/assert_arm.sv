// assert_arm: holds the simulation's assertions off until the first reset of the bound block has
// been seen and released (audit/12 S7). Simulation only.
// Why          : with --public-flat-rw Verilator keeps each instance's clock input as its own
//                variable, and --x-initial unique gives it and the top-level port independent
//                random start values; the first evaluation can then see a rising edge at time 0,
//                when every flop still holds its random power-up value. The hand-written checkers
//                are armed by their first reset; generated code (PeakRDL-regblock's external ack
//                assertions) is not, so this applies the same rule to it.
// Arming       : at the first rising edge with rst low after one with rst high. A spurious edge at
//                time 0 cannot arm it: the reset is driven high before the clocks start and held
//                for several cycles.
// Scope        : $assertoff/$asserton without arguments act on every assertion of the simulation.
//                Assertions that are already armed by their own reset lose nothing (none of them
//                checks anything before the first reset).
module assert_arm (
    input logic clk,
    input logic rst
);
  logic seen_rst, armed;  // sim-only: initial + always (not always_ff) on purpose

  initial begin
    seen_rst = 1'b0;
    armed    = 1'b0;
    $assertoff;
  end

  always @(posedge clk) begin
    if (rst) begin
      seen_rst <= 1'b1;
    end else if (seen_rst && !armed) begin
      armed <= 1'b1;
      $asserton;
    end
  end
endmodule
