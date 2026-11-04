// ring_props: event_ring_writer ring protocol checks; bind to the writer. Simulation only. Disabled until the
// first reset has been seen, because flops start with random values (--x-initial unique).
module ring_props #(
    parameter int unsigned MAX_OUTSTANDING = 4,
    parameter int unsigned OUT_W           = 3
) (
    input logic             clk,
    input logic             rst,
    input logic             enable_q,
    input logic             start,
    input logic [     31:0] cfg_cons,
    input logic             take,
    input logic             fifo_rd_en,
    input logic [     31:0] mask_q,
    input logic [     51:0] base_q,
    input logic [     31:0] prod_q,
    input logic [     31:0] cons_q,
    input logic [     56:0] addr_q,
    input logic             phase_q,
    input logic [OUT_W-1:0] out_q
);
  // Sim-only: initial + always (not always_ff) on purpose.
  logic        armed;
  logic        ring_full;
  logic [31:0] slot;
  initial begin
    armed = 1'b0;
  end
  always @(posedge clk) begin
    if (rst) begin
      armed <= 1'b1;
    end
  end
  assign ring_full = 33'(prod_q - cons_q) == 33'(mask_q) + 33'd1;
  assign slot      = prod_q & mask_q;

  a_no_take_when_full :
  assert property (@(posedge clk) disable iff (rst || !armed) enable_q && take |-> !ring_full)
  else begin
    $error("ring_props: a burst started on a full ring");
  end

  a_no_pop_when_full :
  assert property (@(posedge clk) disable iff (rst || !armed) enable_q && fifo_rd_en |-> !ring_full)
  else begin
    $error("ring_props: the FIFO read on a full ring");
  end

  a_prod_step :
  assert property (@(posedge clk) disable iff (rst || !armed) !start |=> prod_q == $past(
      prod_q
  ) + 32'($past(
      take
  )))
  else begin
    $error("ring_props: prod moved other than by one per burst");
  end

  a_prod_start :
  assert property (@(posedge clk) disable iff (rst || !armed) start && cfg_cons == '0 |=> prod_q == '0)
  else begin
    $error("ring_props: prod not 0 after enable");
  end

  a_enable_refused :
  assert property (@(posedge clk) disable iff (rst || !armed) start && cfg_cons != '0 |=> !enable_q)
  else begin
    $error("ring_props: an enable with a stale cons was not refused");
  end

  a_phase :
  assert property (@(posedge clk) disable iff (rst || !armed) enable_q |-> phase_q == ((prod_q & (mask_q + 32'd1)) == '0))
  else begin
    $error("ring_props: phase differs from its formula");
  end

  a_addr :
  assert property (@(posedge clk) disable iff (rst || !armed)
      enable_q |-> {addr_q, 7'd0} == {base_q, 12'd0} + (64'(slot) << 7))
  else begin
    $error("ring_props: address differs from its formula");
  end

  a_outstanding :
  assert property (@(posedge clk) disable iff (rst || !armed) int'(out_q) <= int'(MAX_OUTSTANDING))
  else begin
    $error("ring_props: more bursts unanswered than MAX_OUTSTANDING");
  end
endmodule
