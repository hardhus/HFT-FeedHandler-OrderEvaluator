// risk_props: risk_check gate and queue rules; bind to the risk_check instance. Simulation only. Disabled until
// the first reset has been seen, because flops start with random values (--x-initial unique).
//
// accept: an order passed every check this cycle (it enters the queue). handover: an order went to TX.
// cancelled: live orders a kill rise or tx_en fall took this cycle. held, live: queued cancelled and live orders.
module risk_props #(
    parameter int unsigned QD      = 8,
    parameter int unsigned QP      = 5,
    parameter int unsigned CREDITS = 16,
    parameter int unsigned CW      = 5
) (
    input logic clk,
    input logic rst,
    input logic accept,
    input logic ctl_kill,
    input logic ctl_tx_en,
    input logic [31:0] budget,
    input logic [31:0] tokens,
    input logic handover,
    input logic [QP-1:0] cancelled,
    input logic [QP-1:0] held,
    input logic [QP-1:0] live,
    input logic m_tx_valid,
    input logic [32:0] ref_next,
    input logic [CW-1:0] credits,
    input logic [5:0] flags,  // risk_check's precomputed decision predicates
    input logic [5:0] defs,  // their definitions, from the state (defs[5] without the commit cycle)
    input logic lim_wr,  // cfg_lim_wr or rst: the next cycle has no refill tick
    input logic [63:0] prod2,  // qty * price one stage before the decision
    input logic [63:0] max_notional
);
  // Sim-only: initial + always (not always_ff) on purpose.
  logic        armed;
  int unsigned queued;  // accepted, not handed over, not cancelled
  initial begin
    armed  = 1'b0;
    queued = 0;
  end
  always @(posedge clk) begin
    if (rst) begin
      armed  <= 1'b1;
      queued <= 0;
    end else begin
      // Stops at 0, so a handover without an accepted order fires a_one_gate alone.
      queued <= queued + int'(accept) - int'(handover && queued > 0) - int'(cancelled);
    end
  end

  a_kill_no_accept :
  assert property (@(posedge clk) disable iff (rst || !armed) accept |-> !ctl_kill)
  else begin
    $error("risk_props: an order accepted while killed");
  end

  a_tx_off_no_accept :
  assert property (@(posedge clk) disable iff (rst || !armed) accept |-> ctl_tx_en)
  else begin
    $error("risk_props: an order accepted while tx_en was 0");
  end

  a_budget_no_accept :
  assert property (@(posedge clk) disable iff (rst || !armed) accept |-> budget != '0)
  else begin
    $error("risk_props: an order accepted with budget 0");
  end

  a_rate_no_accept :
  assert property (@(posedge clk) disable iff (rst || !armed) accept |-> tokens != '0)
  else begin
    $error("risk_props: an order accepted with no token");
  end

  a_one_gate :
  assert property (@(posedge clk) disable iff (rst || !armed) handover |-> queued > 0)
  else begin
    $error("risk_props: an order went to TX without passing the checks");
  end

  a_held_live :
  assert property (@(posedge clk) disable iff (rst || !armed) 32'(held) + 32'(live) <= QD)
  else begin
    $error("risk_props: held and live orders above the queue size");
  end

  a_no_tx_while_drain :
  assert property (@(posedge clk) disable iff (rst || !armed) m_tx_valid |-> held == '0)
  else begin
    $error("risk_props: an order offered to TX while cancelled ones are written out");
  end

  a_ref_left :
  assert property (@(posedge clk) disable iff (rst || !armed) handover |-> ref_next <= 33'hFFFF_FFFF)
  else begin
    $error("risk_props: an order handed over with no UserRefNum left");
  end

  a_accept_has_credit :
  assert property (@(posedge clk) disable iff (rst || !armed) accept |-> credits != '0)
  else begin
    $error("risk_props: an order accepted with no ring credit");
  end

  a_credits_bounded :
  assert property (@(posedge clk) disable iff (rst || !armed) 32'(credits) <= CREDITS)
  else begin
    $error("risk_props: more ring credits than the packer's order queue");
  end

  logic        lim_wr_d;  // sim-only
  logic [63:0] prod_d;  // the product of the order in the decision stage
  always @(posedge clk) begin
    lim_wr_d <= lim_wr;
    prod_d   <= prod2;
  end

  a_flags :
  assert property (@(posedge clk) disable iff (rst || !armed)
      flags == {defs[5] && !lim_wr_d, defs[4:1], prod_d > max_notional})
  else begin
    $error("risk_props: a precomputed decision flag differs from its definition");
  end
endmodule
