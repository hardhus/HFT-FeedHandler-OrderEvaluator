// irq_props: hft_csr_wrap's ring interrupt (audit/15 decisions 4 and 11, A6, A7); bind to hft_csr_wrap. The cause is
// computed here from the inputs, not taken from the wrapper: irq is IRQ_EN.ring && running && done_idx != ring_idx
// one cycle later, IRQ_STATUS.ring the same cause without IRQ_EN, live. Simulation only. Disabled until the first
// reset has been seen, because flops start with random values (--x-initial unique).
module irq_props (
    input logic        clk,
    input logic        rst,
    input logic        irq,
    input logic        status,    // the IRQ_STATUS.ring value hardware drives
    input logic        en,        // IRQ_EN.ring
    input logic        running,   // the writer runs (accepted enable, enable still 1)
    input logic [31:0] done_idx,  // RING_DONE_IDX
    input logic [31:0] ring_idx   // IRQ_RING_IDX
);
  logic armed;  // sim-only: initial + always (not always_ff) on purpose
  logic pend;

  initial begin
    armed = 1'b0;
  end
  always @(posedge clk) begin
    if (rst) begin
      armed <= 1'b1;
    end
  end
  assign pend = running && done_idx != ring_idx;

  a_irq_reset :
  assert property (@(posedge clk) disable iff (!armed) rst |=> !irq)
  else begin
    $error("irq_props: irq high after reset");
  end

  a_irq_off :
  assert property (@(posedge clk) disable iff (rst || !armed) !en || !running |=> !irq)
  else begin
    $error("irq_props: irq with IRQ_EN.ring = 0 or the ring not running");
  end

  a_irq_handled :
  assert property (@(posedge clk) disable iff (rst || !armed) done_idx == ring_idx |=> !irq)
  else begin
    $error("irq_props: irq with RING_DONE_IDX = IRQ_RING_IDX");
  end

  a_irq_on :
  assert property (@(posedge clk) disable iff (rst || !armed) en && pend |=> irq)
  else begin
    $error("irq_props: no irq for a pending ring interrupt");
  end

  a_status :
  assert property (@(posedge clk) disable iff (rst || !armed) status == pend)
  else begin
    $error("irq_props: IRQ_STATUS.ring differs from running && RING_DONE_IDX != IRQ_RING_IDX");
  end
endmodule
