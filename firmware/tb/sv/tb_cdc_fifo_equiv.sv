// tb_cdc_fifo_equiv: self-checking TB for cdc_async_fifo that runs unchanged under Verilator
// (--binary --timing) and Vivado XSIM, so the portable and the XPM implementation see the same
// stimulus for the same +seed (own xorshift32 generator; $urandom differs between simulators).
// Define HFT_FIFO_IMPL_XPM (with HFT_HAVE_XPM) to test the XPM implementation.
// Prints exactly one "HFT_TB_RESULT: PASS|FAIL ..." line.
`timescale 1ns / 1ps
module tb_cdc_fifo_equiv;
`ifdef HFT_FIFO_IMPL_XPM
  localparam string IMPL = "xpm";
`else
  localparam string IMPL = "portable";
`endif  // HFT_FIFO_IMPL_XPM
  localparam int unsigned W = 32;
  localparam int unsigned DEPTH_LOG2 = 4;  // XPM minimum
  localparam int unsigned NWORDS = 20000;
  localparam int unsigned RESET_CYCLES = 20;  // >= 2*SYNC_STAGES+2 of the slower clock

  logic         rst = 1'b1;
  logic         wr_clk = 1'b0;
  logic         rd_clk = 1'b0;
  logic         wr_en = 1'b0;
  logic         rd_en = 1'b0;
  logic [W-1:0] din = '0;
  logic [W-1:0] dout;
  logic full, overflow, wr_rst_busy, empty, underflow, rd_rst_busy;

  logic                run_wr = 1'b0;
  logic                run_rd = 1'b0;
  int unsigned         seed = 1;
  int unsigned         rng = 1;
  int unsigned         sent = 0;
  int unsigned         got = 0;
  int unsigned         errors = 0;
  int unsigned         rd_pct = 6;  // reader enable probability, in tenths
  logic        [W-1:0] expq                                                [$];

  // 156.25 MHz write clock, 250 MHz read clock (net -> host direction)
  always begin
    #3.2 wr_clk = ~wr_clk;
  end
  always begin
    #2.0 rd_clk = ~rd_clk;
  end

  cdc_async_fifo #(
      .WIDTH     (W),
      .DEPTH_LOG2(DEPTH_LOG2),
      .IMPL      (IMPL)
  ) dut (
      .rst        (rst),
      .wr_clk     (wr_clk),
      .wr_en      (wr_en),
      .din        (din),
      .full       (full),
      .overflow   (overflow),
      .wr_rst_busy(wr_rst_busy),
      .rd_clk     (rd_clk),
      .rd_en      (rd_en),
      .dout       (dout),
      .empty      (empty),
      .underflow  (underflow),
      .rd_rst_busy(rd_rst_busy)
  );

  // Shared, simulator-independent random stream (xorshift32).
  function automatic int unsigned rnd();
    rng ^= rng << 13;
    rng ^= rng >> 17;
    rng ^= rng << 5;
    return rng;
  endfunction

  // Writer: an accepted write is exactly what the DUT accepts at this edge.
  always @(posedge wr_clk) begin
    if (wr_en && !full && !wr_rst_busy && !rst) begin
      expq.push_back(din);
      sent++;
    end
    wr_en <= 1'b0;
    if (run_wr && !rst && !wr_rst_busy && !full && (rnd() % 10) < 7) begin
      din   <= rnd();
      wr_en <= 1'b1;
    end
  end

  // Reader (FWFT): a word is popped when rd_en is seen with empty low and not busy.
  always @(posedge rd_clk) begin
    if (rd_en && !empty && !rd_rst_busy) begin
      if (expq.size() == 0) begin
        errors++;
        $display("ERROR read %0d: got 0x%h, nothing expected", got, dout);
      end else begin
        if (dout !== expq[0]) begin
          errors++;
          $display("ERROR read %0d: got 0x%h, expected 0x%h", got, dout, expq[0]);
        end
        void'(expq.pop_front());
      end
      got++;
    end
    rd_en <= run_rd && !rd_rst_busy && (rnd() % 10) < rd_pct;
  end

  task automatic wait_idle();
    while (wr_rst_busy || rd_rst_busy) begin
      @(posedge wr_clk);
    end
    repeat (8) @(posedge rd_clk);
  endtask

  task automatic reset_fifo();
    @(posedge wr_clk) rst <= 1'b1;
    repeat (RESET_CYCLES) @(posedge wr_clk);
    rst <= 1'b0;
    @(posedge wr_clk);
    wait_idle();
  endtask

  // Write until `target` words were accepted, reading with probability pct/10, then drain.
  task automatic stream(input int unsigned target, input int unsigned pct);
    rd_pct = pct;
    run_wr = 1'b1;
    run_rd = 1'b1;
    while (sent < target) begin
      @(posedge wr_clk);
    end
    run_wr = 1'b0;
    while (expq.size() != 0) begin
      @(posedge rd_clk);
    end
    run_rd = 1'b0;
    repeat (4) @(posedge rd_clk);
  endtask

  initial begin
    if (!$value$plusargs("seed=%d", seed)) begin
      seed = 1;
    end
    rng = (seed == 0) ? 32'h1 : seed;

    reset_fifo();  // power-up reset
    stream(NWORDS / 3, 6);  // reader faster than writer: FIFO mostly near empty
    stream(2 * NWORDS / 3, 2);  // reader slower: FIFO mostly at the full boundary

    // Reset with data inside: everything is discarded, the FIFO comes back empty.
    run_rd = 1'b0;
    run_wr = 1'b1;
    while (!full) begin
      @(posedge wr_clk);
    end
    run_wr = 1'b0;
    repeat (4) @(posedge wr_clk);
    expq.delete();
    reset_fifo();
    if (!empty || full) begin
      errors++;
      $display("ERROR after reset: empty=%0d full=%0d", empty, full);
    end

    stream(NWORDS, 6);
    $display("HFT_TB_RESULT: %s (impl=%s seed=%0d written=%0d read=%0d errors=%0d)",
             errors == 0 ? "PASS" : "FAIL", IMPL, seed, sent, got, errors);
    $finish;
  end

  initial begin
    #20ms;
    $display("HFT_TB_RESULT: FAIL (impl=%s timeout, written=%0d read=%0d errors=%0d)", IMPL, sent,
             got, errors);
    $finish;
  end
endmodule
