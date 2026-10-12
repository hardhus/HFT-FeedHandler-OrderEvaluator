// tb_fifo_props_arming: checks the fifo_props checker itself (regressions for two S6 arming bugs).
// Drives the checker's ports directly. Power-up artefacts must NOT fire assertions:
//  - overflow holds a random power-up value, and the checker can see an initial wr_clk edge that
//    the DUT does not act on (Verilator, random init: seen in CI as a_overflow_cause firing at the
//    first real edge, seed 1438791676). Modelled by overflow staying stale for two edges;
//  - a rd_rst_busy glitch during the power-up rst hold (stale synchronizer state, FIFO not
//    empty) must not be taken for the end of reset.
// The real end of reset must be checked. With HFT_VIOLATE the real end of reset leaves the FIFO non-empty, so the
// assertion must fire (Verilator stops the simulation and no PASS line is printed).
`timescale 1ns / 1ps
module tb_fifo_props_arming;
  logic       rst = 1'b1;
  logic       wr_clk = 1'b0;
  logic       rd_clk = 1'b0;
  logic       rd_rst_busy = 1'b1;  // stale "busy" from random power-up state
  logic       empty = 1'b0;  // stale: looks non-empty
  logic [7:0] dout = 8'h5a;
  logic       overflow = 1'b1;  // random power-up value of the (resetless) overflow flop

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
      .wr_en      (1'b0),
      .full       (1'b0),
      .overflow   (overflow),
      .wr_rst_busy(1'b0),
      .rd_clk     (rd_clk),
      .rd_en      (1'b0),
      .dout       (dout),
      .empty      (empty),
      .underflow  (1'b0),
      .rd_rst_busy(rd_rst_busy)
  );

  initial begin
    repeat (2) @(posedge wr_clk);
    overflow <= 1'b0;  // the DUT computes overflow from wr_en = 0 only from its first real edge on
  end

  initial begin
    // Power-up hold: the stale busy glitches low while rst is still high and empty is low.
    repeat (3) @(posedge rd_clk);
    rd_rst_busy <= 1'b0;
    repeat (3) @(posedge rd_clk);
    rd_rst_busy <= 1'b1;  // the real reset request arrives
    repeat (2) @(posedge wr_clk);
    rst <= 1'b0;  // power-up reset released
    repeat (4) @(posedge rd_clk);
`ifndef HFT_VIOLATE
    empty <= 1'b1;  // read side reset done: FIFO empty
`endif  // HFT_VIOLATE
    repeat (2) @(posedge rd_clk);
    rd_rst_busy <= 1'b0;  // real end of reset
    repeat (6) @(posedge rd_clk);
    $display("HFT_TB_RESULT: PASS (fifo_props arming)");
    $finish;
  end
endmodule
