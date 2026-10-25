// tb_itch_stats_props: checks the itch_stats_props checker itself (audit/12 S6). Drives its ports
// directly: the reset, then a result on a full pending count together with a packet event (the
// property's antecedent, legal), and results on pending counts below the limit. A clean run
// proves the checker quiet on legal traffic. With HFT_VIOLATE=1 the result on the full pending
// count comes without a packet event; the assertion must fire (Verilator stops and no PASS line
// is printed). Values are set after a falling edge and sampled at the next rising edge.
`timescale 1ns / 1ps
module tb_itch_stats_props;
  import hft_pkg::*;

  logic                        clk = 1'b0;
  logic                        rst = 1'b1;
  logic                        pkt_valid = 1'b0;
  logic [ITCH_N_CNT-1:0]       inc = '0;
  logic [ITCH_N_CNT-1:0][15:0] pend = '0;

  always begin
    #3.2 clk = ~clk;
  end

  itch_stats_props u_props (
      .clk      (clk),
      .rst      (rst),
      .pkt_valid(pkt_valid),
      .inc      (inc),
      .pend     (pend)
  );

  task automatic step();
    @(negedge clk);
  endtask

  initial begin
    repeat (2) step();
    rst = 1'b0;
    step();
    // results below the limit, without a packet event
    pend               = '0;
    pend[ITCH_ORDER]   = 16'hFFFE;
    pend[ITCH_CNT_OUT] = 16'hFFFE;
    inc[ITCH_ORDER]    = 1'b1;
    inc[ITCH_CNT_OUT]  = 1'b1;
    step();
    // a result on the full pending counts: legal only with a packet event
    pend[ITCH_ORDER]   = 16'hFFFF;
    pend[ITCH_CNT_OUT] = 16'hFFFF;
`ifdef HFT_VIOLATE
    pkt_valid = 1'b0;
`else
    pkt_valid = 1'b1;
`endif  // HFT_VIOLATE
    step();
    // a full pending count without a result is legal
    inc       = '0;
    pkt_valid = 1'b0;
    step();
    repeat (2) step();
    $display("HFT_TB_RESULT: PASS (itch_stats_props)");
    $finish;
  end
endmodule
