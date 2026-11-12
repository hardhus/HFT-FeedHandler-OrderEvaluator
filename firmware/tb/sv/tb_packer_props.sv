// tb_packer_props: checks the packer_props checker itself. Drives its ports directly: a message, a write, an
// accepted packet event and its commit 1 cycle later, then one with 2 entries and its commit 2 cycles
// later, a rejected event and its rollback, an order entry taken, written and its credit given back (the
// queue read address pick follows rp + s1_ord, with rp away from 0), a full queue. Every property's antecedent is hit, so a clean run proves the
// checker quiet. With HFT_VIOLATE=<k> exactly one property is violated (numbers in test_sva_checkers.py);
// its assertion must fire. Values are set after a falling edge and sampled at the next rising edge.
`timescale 1ns / 1ps
module tb_packer_props;
  import hft_pkg::*;

  logic                         clk = 1'b0;
  logic                         rst = 1'b1;
  logic                         s_ev_valid = 1'b0;
  logic                         s_pkt_valid = 1'b0;
  logic                         pkt_accepted = 1'b0;
  logic                         s1_valid = 1'b0;
  logic                         fifo_wr_en = 1'b0;
  logic                         fifo_commit = 1'b0;
  logic                         fifo_rollback = 1'b0;
  logic                         fifo_full = 1'b0;
  logic                         fifo_wr_busy = 1'b0;
  logic                         s_sent_valid = 1'b0;
  logic                         s_rej_valid = 1'b0;
  logic                         s1_commit = 1'b0;
  logic                         s1_rollback = 1'b0;
  logic                         s1_ord = 1'b0;
  logic                         ord_write = 1'b0;
  logic                         ord_free = 1'b0;
  logic [RING_ORD_QUEUED_W-1:0] oq_n = '0;
  logic                         ring_credit_ret = 1'b0;
  logic [$clog2(RISK_RING_CREDITS)-1:0] rp = '0, pick;

  always begin
    #3.2 clk = ~clk;
  end

  packer_props u_props (
      .clk            (clk),
      .rst            (rst),
      .s_ev_valid     (s_ev_valid),
      .s_pkt_valid    (s_pkt_valid),
      .pkt_accepted   (pkt_accepted),
      .s1_valid       (s1_valid),
      .fifo_wr_en     (fifo_wr_en),
      .fifo_commit    (fifo_commit),
      .fifo_rollback  (fifo_rollback),
      .fifo_full      (fifo_full),
      .fifo_wr_busy   (fifo_wr_busy),
      .s_sent_valid   (s_sent_valid),
      .s_rej_valid    (s_rej_valid),
      .s1_commit      (s1_commit),
      .s1_rollback    (s1_rollback),
      .s1_ord         (s1_ord),
      .ord_write      (ord_write),
      .ord_free       (ord_free),
      .oq_n           (oq_n),
      .ring_credit_ret(ring_credit_ret),
      .rp             (rp),
      .pick           (pick)
  );

  // 1 when this run violates property k.
  function automatic logic v(input int k);
`ifdef HFT_VIOLATE
    return k == `HFT_VIOLATE;
`else
    return k < 0;
`endif  // HFT_VIOLATE
  endfunction

  // The packer's pick_q: rp + s1_ord; one off while an order entry is taken under violation 14 (a_pick).
  assign pick = rp + $bits(rp)'(s1_ord) + $bits(rp)'(s1_ord && v(14));

  task automatic idle(input int unsigned n = 1);
    repeat (n) begin
      @(negedge clk);
      s_ev_valid    = 1'b0;
      s_pkt_valid   = 1'b0;
      pkt_accepted  = 1'b0;
      s1_valid      = 1'b0;
      fifo_wr_en    = 1'b0;
      fifo_commit   = 1'b0;
      fifo_rollback = 1'b0;
      fifo_full     = 1'b0;
    end
  endtask

  // One accepted event; its commit in the next cycle.
  task automatic accepted(input logic with_message);
    @(negedge clk);
    s_pkt_valid  = 1'b1;
    pkt_accepted = 1'b1;
    s_ev_valid   = with_message;
    @(negedge clk);
    s_pkt_valid  = 1'b0;
    pkt_accepted = 1'b0;
    s_ev_valid   = 1'b0;
    fifo_commit  = 1'b1;
    idle();
  endtask

  initial begin
    repeat (2) @(negedge clk);
    rst = 1'b0;
    idle(2);

    // A message and its write.
    @(negedge clk);
    s_ev_valid = 1'b1;
    @(negedge clk);
    s_ev_valid = 1'b0;
    s1_valid   = 1'b1;
    fifo_wr_en = 1'b1;
    fifo_full  = v(5);  // 5: a_write_when_ready
    idle();

    accepted(v(1));  // 1: a_no_ev_with_pkt
    // accepted() returns after the falling edge of the 2nd cycle after the event, so what is set here is sampled in
    // that cycle: the last one its window covers.
    if (v(2)) begin  // 2: a_ev_after_pkt
      s_ev_valid = 1'b1;
      idle();
    end
    idle(2);

    // Legal at the edge: a message and an accepted event 3 cycles after an event.
    accepted(1'b0);
    @(negedge clk);
    s_ev_valid = 1'b1;
    idle(2);
    accepted(1'b0);
    accepted(1'b0);
    idle(2);

    accepted(1'b0);
    if (v(3)) begin  // 3: a_accepted_spacing
      s_pkt_valid  = 1'b1;
      pkt_accepted = 1'b1;
      @(negedge clk);
      s_pkt_valid  = 1'b0;
      pkt_accepted = 1'b0;
      fifo_commit  = 1'b1;
      idle();
    end
    idle(3);

    // An accepted event with GAP and EOS: the commit comes 2 cycles later.
    @(negedge clk);
    s_pkt_valid  = 1'b1;
    pkt_accepted = 1'b1;
    @(negedge clk);
    s_pkt_valid  = 1'b0;
    pkt_accepted = 1'b0;
    s1_valid     = 1'b1;
    fifo_wr_en   = 1'b1;
    @(negedge clk);
    fifo_commit   = !v(8);  // 8: a_accepted_gets_end
    fifo_rollback = v(6);  // 6: a_end_xor
    idle(3);

    // A rejected event and its rollback.
    @(negedge clk);
    s_pkt_valid = 1'b1;
    @(negedge clk);
    s_pkt_valid   = 1'b0;
    fifo_rollback = 1'b1;
    idle(3);

    if (v(7)) begin  // 7: a_end_has_event
      @(negedge clk);
      fifo_commit = 1'b1;
      idle();
    end

    // An order entry: it comes in, is taken in a free cycle, written with its commit, its credit comes back.
    @(negedge clk);
    s_sent_valid = 1'b1;
    @(negedge clk);
    s_sent_valid = 1'b0;
    oq_n         = 1;
    ord_free     = 1'b1;
    rp           = 5;
    @(negedge clk);
    s1_ord      = !v(13);  // 13: a_ord_taken
    ord_write   = !v(13);
    fifo_wr_en  = !v(13);
    fifo_commit = !v(13) && !v(10);  // 10: a_ord_commits
    s1_commit   = v(9);  // 9: a_ord_outside_unit
    @(negedge clk);
    s1_ord          = 1'b0;
    ord_write       = 1'b0;
    fifo_wr_en      = 1'b0;
    fifo_commit     = 1'b0;
    s1_commit       = 1'b0;
    ord_free        = 1'b0;
    oq_n            = v(13) ? 1 : 0;
    ring_credit_ret = !v(13) && !v(11);  // 11: a_credit_ret
    @(negedge clk);
    ring_credit_ret = 1'b0;
    oq_n            = '0;
    rp              = '0;
    idle(2);

    // A full queue: nothing more may come in.
    @(negedge clk);
    oq_n        = RING_ORD_QUEUED_W'(RISK_RING_CREDITS);
    s_rej_valid = v(12);  // 12: a_order_room
    @(negedge clk);
    s_rej_valid = 1'b0;
    oq_n        = '0;
    idle(2);

    if (v(4)) begin  // 4: a_capacity
      repeat (RING_MAX_PKT_ENTRIES + 1) begin
        @(negedge clk);
        s1_valid = 1'b1;
      end
      idle();
    end else begin
      repeat (RING_MAX_PKT_ENTRIES) begin
        @(negedge clk);
        s1_valid = 1'b1;
      end
      idle();
    end
    idle(2);
    $display("HFT_TB_RESULT: PASS");
    $finish;
  end
endmodule
