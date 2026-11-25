// mbox_props: hft_csr_wrap's mailbox output (m_host_*, decision 12, audit/15); bind to hft_csr_wrap.
// Simulation only. Disabled until the first reset has been seen, because flops start with random
// values (--x-initial unique).
module mbox_props #(
    parameter int unsigned W = 272
) (
    input logic         clk,
    input logic         rst,
    input logic         valid,
    input logic         ready,
    input logic [W-1:0] data
);
  logic armed;  // sim-only: initial + always (not always_ff) on purpose

  initial begin
    armed = 1'b0;
  end
  always @(posedge clk) begin
    if (rst) begin
      armed <= 1'b1;
    end
  end

  // An order stays offered and unchanged until it is taken.
  a_stall_stable :
  assert property (@(posedge clk) disable iff (rst || !armed) valid && !ready |=> valid && $stable(
      data
  ))
  else begin
    $error("mbox_props: order changed or valid dropped before it was taken");
  end

  // After a take the register is empty for one cycle (the next head is read after the pop).
  a_gap_after_take :
  assert property (@(posedge clk) disable iff (rst || !armed) valid && ready |=> !valid)
  else begin
    $error("mbox_props: an order offered in the cycle after a take");
  end
endmodule
