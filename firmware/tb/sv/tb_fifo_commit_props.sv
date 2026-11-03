// tb_fifo_commit_props: checks the COMMIT=1 properties of the fifo_props checker. Drives its ports
// directly: words written, a rollback, a commit in the cycle of a write and a read of each committed
// word, so a clean run proves them quiet. With HFT_VIOLATE=<k> exactly one property is violated
// (numbers in test_sva_checkers.py); its assertion must fire. Values change after a falling edge.
`timescale 1ns / 1ps
module tb_fifo_commit_props;
  logic rst = 1'b1;
  logic wr_clk = 1'b0;
  logic rd_clk = 1'b0;
  logic wr_en = 1'b0;
  logic wr_commit = 1'b0;
  logic wr_rollback = 1'b0;
  logic rd_en = 1'b0;
  logic empty = 1'b1;
  logic rd_rst_busy = 1'b0;

  always begin
    #3.2 wr_clk = ~wr_clk;
  end
  always begin
    #2.0 rd_clk = ~rd_clk;
  end

  fifo_props #(
      .WIDTH (8),
      .COMMIT(1'b1)
  ) u_props (
      .rst        (rst),
      .wr_clk     (wr_clk),
      .wr_en      (wr_en),
      .wr_commit  (wr_commit),
      .wr_rollback(wr_rollback),
      .full       (1'b0),
      .overflow   (1'b0),
      .wr_rst_busy(1'b0),
      .rd_clk     (rd_clk),
      .rd_en      (rd_en),
      .dout       (8'h00),
      .empty      (empty),
      .underflow  (1'b0),
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

  // One read of the word on dout: rd_en with empty low for one rd_clk edge.
  task automatic pop();
    @(negedge rd_clk);
    empty = 1'b0;
    rd_en = 1'b1;
    @(negedge rd_clk);
    empty = 1'b1;
    rd_en = 1'b0;
  endtask

  task automatic write_one();
    @(negedge wr_clk);
    wr_en = 1'b1;
    @(negedge wr_clk);
    wr_en = 1'b0;
  endtask

  initial begin
    repeat (2) @(negedge wr_clk);
    rst = 1'b0;  // the write side arms
    repeat (2) @(negedge rd_clk);
    rd_rst_busy = 1'b1;  // the read side arms on a busy seen after the release
    repeat (2) @(negedge rd_clk);
    rd_rst_busy = 1'b0;
    repeat (2) @(negedge rd_clk);

    // Two words written, rolled back: nothing to read.
    write_one();
    write_one();
    @(negedge wr_clk);
    wr_rollback = 1'b1;
    wr_commit   = v(1);  // 1: a_commit_xor_rollback
    @(negedge wr_clk);
    wr_rollback = 1'b0;
    wr_commit   = 1'b0;
    if (v(2)) begin
      pop();  // 2: a_read_committed, a rolled-back word
    end

    // One word written, then one more in the commit's cycle: two words to read.
    write_one();
    @(negedge wr_clk);
    wr_en     = 1'b1;
    wr_commit = 1'b1;
    @(negedge wr_clk);
    wr_en     = 1'b0;
    wr_commit = 1'b0;
    repeat (4) @(negedge rd_clk);
    pop();
    pop();
    repeat (3) @(negedge rd_clk);
    $display("HFT_TB_RESULT: PASS");
    $finish;
  end
endmodule
