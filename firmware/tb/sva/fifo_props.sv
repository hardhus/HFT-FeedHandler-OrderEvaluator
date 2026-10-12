// fifo_props: contract checks for cdc_async_fifo (any IMPL); bind to the FIFO instance.
// Simulation only. Contract: outputs are defined once the power-up reset has been released
// and both busy flags have dropped. During the power-up hold, random initial synchronizer
// state can make rd_rst_busy glitch before the real reset request arrives, so the read side
// is armed only by a busy seen after rst was released (the real request is still in flight).
module fifo_props #(
    parameter int unsigned WIDTH = 64
) (
    input logic             rst,
    input logic             wr_clk,
    input logic             wr_en,
    input logic             full,
    input logic             overflow,
    input logic             wr_rst_busy,
    input logic             rd_clk,
    input logic             rd_en,
    input logic [WIDTH-1:0] dout,
    input logic             empty,
    input logic             underflow,
    input logic             rd_rst_busy
);
  // Sim-only: initial + always (not always_ff) on purpose.
  logic armed_wr, released_wr, armed_rd;
  initial begin
    armed_wr    = 1'b0;
    released_wr = 1'b0;
    armed_rd    = 1'b0;
  end
  always @(posedge wr_clk) begin
    if (rst) begin
      armed_wr <= 1'b1;
    end
    if (armed_wr && !rst) begin
      released_wr <= 1'b1;  // power-up reset released
    end
  end
  always @(posedge rd_clk) begin
    if (released_wr && rd_rst_busy) begin
      armed_rd <= 1'b1;
    end
  end

  // ---- write side: a rejected write pulses overflow, and only a rejected write does ----
  a_reject_full :
  assert property (@(posedge wr_clk) disable iff (!armed_wr) wr_en && full |=> overflow)
  else begin
    $error("fifo_props: write while full did not pulse overflow");
  end

  a_reject_busy :
  assert property (@(posedge wr_clk) disable iff (!armed_wr) wr_en && wr_rst_busy |=> overflow)
  else begin
    $error("fifo_props: write while wr_rst_busy did not pulse overflow");
  end

  a_overflow_cause :
  assert property (@(posedge wr_clk) disable iff (!armed_wr) overflow |-> $past(
      wr_en && (full || wr_rst_busy || rst)
  ))
  else begin
    $error("fifo_props: overflow without a rejected write");
  end

  // ---- read side ----
  a_reject_empty :
  assert property (@(posedge rd_clk) disable iff (!armed_rd) rd_en && empty |=> underflow)
  else begin
    $error("fifo_props: read while empty did not pulse underflow");
  end

  a_underflow_cause :
  assert property (@(posedge rd_clk) disable iff (!armed_rd) underflow |-> $past(
      rd_en && (empty || rd_rst_busy)
  ))
  else begin
    $error("fifo_props: underflow without a rejected read");
  end

  // FWFT: a word that is shown and not popped stays shown, unchanged.
  a_fwft_hold :
  assert property (@(posedge rd_clk) disable iff (!armed_rd || rd_rst_busy)
      !empty && !rd_en |=> !empty && $stable(
      dout
  ))
  else begin
    $error("fifo_props: FWFT word vanished or changed without rd_en");
  end

  // A finished reset leaves the FIFO empty.
  a_empty_after_reset :
  assert property (@(posedge rd_clk) disable iff (!armed_rd) $fell(rd_rst_busy) |-> empty)
  else begin
    $error("fifo_props: FIFO not empty when rd_rst_busy fell");
  end
endmodule
