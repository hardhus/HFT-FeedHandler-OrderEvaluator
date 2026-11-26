// tb_irq_props: checks the irq_props checker itself. Drives its ports from a model of the wrapper (irq = the cause one
// cycle later, status = the cause now): reset, the ring stopped, IRQ_EN off, entries done and handled, done ahead of
// the host index, the host catching up. Every property's antecedent is hit, so a clean run proves the checker quiet.
// With HFT_VIOLATE=<k> exactly one property is violated (numbers in test_sva_checkers.py) by flipping irq or status
// in one situation; its assertion must fire. Values are set after a falling edge and sampled at the next rising edge.
`timescale 1ns / 1ps
module tb_irq_props;
  logic clk = 1'b0;
  logic rst = 1'b1;
  logic en = 1'b0, running = 1'b0;
  logic [31:0] done_idx = '0, ring_idx = '0;
  logic irq_q = 1'b0, flip_irq = 1'b0, flip_status = 1'b0;
  logic irq, status, pend;

  always begin
    #2 clk = ~clk;
  end

  assign pend = running && done_idx != ring_idx;
  always @(posedge clk) begin
    irq_q <= rst ? 1'b0 : en && pend;
  end
  assign irq    = irq_q ^ flip_irq;
  assign status = pend ^ flip_status;

  irq_props u_props (
      .clk     (clk),
      .rst     (rst),
      .irq     (irq),
      .status  (status),
      .en      (en),
      .running (running),
      .done_idx(done_idx),
      .ring_idx(ring_idx)
  );

  // 1 when this run violates property k.
  function automatic logic v(input int k);
`ifdef HFT_VIOLATE
    return k == `HFT_VIOLATE;
`else
    return k < 0;
`endif  // HFT_VIOLATE
  endfunction

  // n cycles in this state; with bad, flip irq (or status) for the last of them.
  task automatic hold(input int unsigned n, input logic bad_irq = 1'b0,
                      input logic bad_status = 1'b0);
    repeat (n - 1) @(negedge clk);
    @(negedge clk);
    flip_irq    = bad_irq;
    flip_status = bad_status;
    @(negedge clk);
    flip_irq    = 1'b0;
    flip_status = 1'b0;
  endtask

  initial begin
    // Reset, with entries done and the writer running: irq stays 0 (1: a_irq_reset).
    en       = 1'b1;
    running  = 1'b1;
    done_idx = 32'd3;
    hold(3, v(1));
    rst = 1'b0;
    // The cause and irq one cycle later.
    hold(3);
    // IRQ_EN off with the cause there (2: a_irq_off).
    @(negedge clk);
    en = 1'b0;
    hold(3, v(2));
    // The ring stopped (the same property, the other term), IRQ_EN on again.
    @(negedge clk);
    en      = 1'b1;
    running = 1'b0;
    hold(3);
    // Running again; the host handles everything: done = idx (3: a_irq_handled).
    @(negedge clk);
    running = 1'b1;
    hold(2);
    @(negedge clk);
    ring_idx = done_idx;
    hold(3, v(3));
    // New entries done, the host behind: irq must come (4: a_irq_on); status flipped (5: a_status).
    @(negedge clk);
    done_idx = 32'hFFFF_FFFF;  // across the wrap: only equality matters
    hold(3, v(4), v(5));
    @(negedge clk);
    ring_idx = 32'hFFFF_FFFF;
    hold(3);
    $display("HFT_TB_RESULT: PASS");
    $finish;
  end
endmodule
