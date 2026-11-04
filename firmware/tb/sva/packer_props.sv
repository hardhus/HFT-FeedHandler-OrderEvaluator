// packer_props: event_packer input contract and FIFO write rules; bind to the event_packer instance.
// Simulation only. Disabled until the first reset has been seen, because flops start with random
// values (--x-initial unique).
module packer_props
  import hft_pkg::*;
(
    input logic clk,
    input logic rst,
    input logic s_ev_valid,
    input logic s_pkt_valid,
    input logic pkt_accepted,
    input logic s1_valid,
    input logic fifo_wr_en,
    input logic fifo_commit,
    input logic fifo_rollback,
    input logic fifo_full,
    input logic fifo_wr_busy
);
  // Sim-only: initial + always (not always_ff) on purpose.
  logic        armed;
  int unsigned entries;  // of the current packet, the dropped ones included
  logic end_unit, accepted_event;
  initial begin
    armed   = 1'b0;
    entries = 0;
  end
  assign end_unit       = fifo_commit || fifo_rollback;
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
  assert property (@(posedge clk) disable iff (rst || !armed) end_unit |-> $past(
      s_pkt_valid
  ) || $past(
      s_pkt_valid, 2
  ))
  else begin
    $error("packer_props: a commit or rollback without a packet event");
  end

  // Not `end_unit or ##1 end_unit`: Verilator 5.053 compiles that form and it never fires.
  a_accepted_gets_end :
  assert property (@(posedge clk) disable iff (rst || !armed) accepted_event |-> ##[1:2] end_unit)
  else begin
    $error("packer_props: an accepted packet event without a commit or rollback");
  end
endmodule
