// packer_props: event_packer input contract, FIFO write rules and order entries (audit/14 P8, decisions 25,
// 28-31); bind to the event_packer instance.
// Simulation only. Disabled until the first reset has been seen, because flops start with random
// values (--x-initial unique).
module packer_props
  import hft_pkg::*;
(
    input logic                         clk,
    input logic                         rst,
    input logic                         s_ev_valid,
    input logic                         s_pkt_valid,
    input logic                         pkt_accepted,
    input logic                         s1_valid,
    input logic                         fifo_wr_en,
    input logic                         fifo_commit,
    input logic                         fifo_rollback,
    input logic                         fifo_full,
    input logic                         fifo_wr_busy,
    // order entries
    input logic                         s_sent_valid,
    input logic                         s_rej_valid,
    input logic                         s1_commit,
    input logic                         s1_rollback,
    input logic                         s1_ord,
    input logic                         ord_write,
    input logic                         ord_free,
    input logic [RING_ORD_QUEUED_W-1:0] oq_n,
    input logic                         ring_credit_ret
);
  // Sim-only: initial + always (not always_ff) on purpose.
  logic        armed;
  int unsigned entries;  // of the current packet, the dropped ones included
  logic end_unit, end_pkt, accepted_event;
  initial begin
    armed   = 1'b0;
    entries = 0;
  end
  assign end_unit = fifo_commit || fifo_rollback;
  assign end_pkt        = (fifo_commit && !ord_write) || fifo_rollback;  // a packet's end, not an order entry's
  assign accepted_event = s_pkt_valid && pkt_accepted;
  always @(posedge clk) begin
    if (rst) begin
      armed   <= 1'b1;
      entries <= 0;
    end else if (end_unit) begin
      entries <= 0;
    end else begin
      entries <= entries + int'(s1_valid);
    end
  end

  // ---- input contract ----
  a_no_ev_with_pkt :
  assert property (@(posedge clk) disable iff (rst || !armed) s_pkt_valid |-> !s_ev_valid)
  else begin
    $error("packer_props: a message in the cycle of a packet event");
  end

  a_ev_after_pkt :
  assert property (@(posedge clk) disable iff (rst || !armed)
      s_pkt_valid |=> !s_ev_valid ##1 !s_ev_valid)
  else begin
    $error("packer_props: a message within 2 cycles after a packet event");
  end

  a_accepted_spacing :
  assert property (@(posedge clk) disable iff (rst || !armed)
      accepted_event |=> !accepted_event ##1 !accepted_event)
  else begin
    $error("packer_props: an accepted packet event within 2 cycles after another");
  end

  a_capacity :
  assert property (@(posedge clk) disable iff (rst || !armed) s1_valid |-> entries < RING_MAX_PKT_ENTRIES)
  else begin
    $error("packer_props: a packet of more than RING_MAX_PKT_ENTRIES entries");
  end

  // ---- FIFO side ----
  a_write_when_ready :
  assert property (@(posedge clk) disable iff (rst || !armed)
      fifo_wr_en |-> !fifo_full && !fifo_wr_busy)
  else begin
    $error("packer_props: a write to a full or busy FIFO");
  end

  a_end_xor :
  assert property (@(posedge clk) disable iff (rst || !armed) !(fifo_commit && fifo_rollback))
  else begin
    $error("packer_props: fifo_commit and fifo_rollback in one cycle");
  end

  a_end_has_event :
  assert property (@(posedge clk) disable iff (rst || !armed) end_pkt |-> $past(
      s_pkt_valid
  ) || $past(
      s_pkt_valid, 2
  ))
  else begin
    $error("packer_props: a commit or rollback without a packet event");
  end

  // Not `end_unit or ##1 end_unit`: Verilator 5.053 compiles that form and it never fires.
  a_accepted_gets_end :
  assert property (@(posedge clk) disable iff (rst || !armed) accepted_event |-> ##[1:2] end_pkt)
  else begin
    $error("packer_props: an accepted packet event without a commit or rollback");
  end

  // ---- order entries ----
  // Never inside a packet's unit: no entry of the current packet written, no packet item in stage 1.
  a_ord_outside_unit :
  assert property (@(posedge clk) disable iff (rst || !armed)
      ord_write |-> entries == 0 && !s1_valid && !s1_commit && !s1_rollback)
  else begin
    $error("packer_props: an order entry inside a packet's unit");
  end

  a_ord_commits :
  assert property (@(posedge clk) disable iff (rst || !armed) ord_write |-> fifo_wr_en && fifo_commit)
  else begin
    $error("packer_props: an order entry written without its commit");
  end

  a_credit_ret :
  assert property (@(posedge clk) disable iff (rst || !armed) !$past(
      rst
  ) |-> ring_credit_ret == $past(
      ord_write
  ))
  else begin
    $error("packer_props: ring_credit_ret is not one per written order entry, one cycle later");
  end

  // Input contract: the producers keep to the credits (risk_check), so the queue never holds more.
  a_order_room :
  assert property (@(posedge clk) disable iff (rst || !armed)
      32'(oq_n) - 32'(ord_write) + 32'(s_sent_valid) + 32'(s_rej_valid) <= 32'(RISK_RING_CREDITS))
  else begin
    $error("packer_props: more order entries than the queue holds");
  end

  // An entry waits only for a reason: in a free cycle with one queued behind stage 1 (and stage 1 not
  // refused), it is taken.
  a_ord_taken :
  assert property (@(posedge clk) disable iff (rst || !armed)
      ord_free && 32'(oq_n) > 32'(s1_ord) && !(s1_ord && !ord_write) |=> s1_ord)
  else begin
    $error("packer_props: a queued order entry not taken in a free cycle");
  end
endmodule
