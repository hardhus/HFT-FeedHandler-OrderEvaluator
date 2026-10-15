// tb_fifo_props: checks the fifo_props checker itself. Drives its ports directly: legal traffic
// that hits every antecedent of the six properties below (rejected write while full and while
// busy, rejected read while empty, a held FWFT word), so a clean run proves the checker quiet.
// With HFT_VIOLATE=<k> exactly one property is violated (numbers in test_sva_checkers.py); its
// assertion must fire (Verilator stops and no PASS line is printed). a_empty_after_reset has
// its own testbench (tb_fifo_props_arming.sv). Values change after a falling edge.
`timescale 1ns / 1ps
module tb_fifo_props;
  logic       rst = 1'b1;
  logic       wr_clk = 1'b0;
  logic       rd_clk = 1'b0;
  logic       wr_en = 1'b0;
  logic       full = 1'b0;
  logic       overflow = 1'b0;
  logic       wr_rst_busy = 1'b0;
  logic       rd_en = 1'b0;
  logic [7:0] dout = 8'h00;
  logic       empty = 1'b1;
  logic       underflow = 1'b0;
  logic       rd_rst_busy = 1'b1;
  logic       wr_done = 1'b0;

  always begin
    #3.2 wr_clk = ~wr_clk;
  end
  always begin
    #2.0 rd_clk = ~rd_clk;
  end

  fifo_props #(
      .WIDTH(8)
  ) u_props (
      .rst        (rst),
      .wr_clk     (wr_clk),
      .wr_en      (wr_en),
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

  // 1 when this run violates property k.
  function automatic logic v(input int k);
`ifdef HFT_VIOLATE
    return k == `HFT_VIOLATE;
`else
    return k < 0;
`endif  // HFT_VIOLATE
  endfunction

  // ---- write side ----
  initial begin
    repeat (2) @(negedge wr_clk);
    rst = 1'b0;  // power-up reset released: the write side arms
    repeat (3) @(negedge wr_clk);
    // Write while full (1: a_reject_full): overflow must follow.
    full  = 1'b1;
    wr_en = 1'b1;
    @(negedge wr_clk);
    full     = 1'b0;
    wr_en    = 1'b0;
    overflow = !v(1);
    @(negedge wr_clk);
    overflow    = 1'b0;
    // Write while busy (2: a_reject_busy): overflow must follow.
    wr_rst_busy = 1'b1;
    wr_en       = 1'b1;
    @(negedge wr_clk);
    wr_rst_busy = 1'b0;
    wr_en       = 1'b0;
    overflow    = !v(2);
    @(negedge wr_clk);
    overflow = 1'b0;
    // An overflow pulse without a rejected write the cycle before (3: a_overflow_cause).
    @(negedge wr_clk);
    overflow = v(3);
    @(negedge wr_clk);
    overflow = 1'b0;
    repeat (3) @(negedge wr_clk);
    wr_done = 1'b1;
  end

  // ---- read side ----
  initial begin
    wait (!rst);
    repeat (3) @(negedge rd_clk);  // rd_rst_busy seen high after the release: the read side arms
    rd_rst_busy = 1'b0;  // end of reset with an empty FIFO
    repeat (2) @(negedge rd_clk);
    // Read while empty (4: a_reject_empty): underflow must follow.
    rd_en = 1'b1;
    @(negedge rd_clk);
    rd_en     = 1'b0;
    underflow = !v(4);
    @(negedge rd_clk);
    underflow = 1'b0;
    // An underflow pulse without a rejected read the cycle before (5: a_underflow_cause).
    @(negedge rd_clk);
    underflow = v(5);
    @(negedge rd_clk);
    underflow = 1'b0;
    // A word shown and not popped stays, unchanged (6: a_fwft_hold); then it is popped.
    empty     = 1'b0;
    dout      = 8'hA5;
    repeat (3) @(negedge rd_clk);
    dout = v(6) ? 8'h5A : 8'hA5;
    @(negedge rd_clk);
    rd_en = 1'b1;
    @(negedge rd_clk);
    rd_en = 1'b0;
    empty = 1'b1;
    repeat (3) @(negedge rd_clk);
    wait (wr_done);
    $display("HFT_TB_RESULT: PASS (fifo_props)");
    $finish;
  end
endmodule
