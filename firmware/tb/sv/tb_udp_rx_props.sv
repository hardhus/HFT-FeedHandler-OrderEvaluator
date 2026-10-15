// tb_udp_rx_props: checks the udp_rx_props checker itself (audit/09 S4). Drives its ports
// directly, cycle by cycle: one frame with a two-beat payload packet and its stat event, then a
// long frame whose beat counter sits at its maximum. Every property's antecedent is hit, so a
// clean run proves the checker quiet on legal traffic. With HFT_VIOLATE=<k> exactly one property
// is violated (numbers in test_sva_checkers.py); its assertion must fire (Verilator stops and no
// PASS line is printed). Values are set after a falling edge and sampled at the next rising edge.
`timescale 1ns / 1ps
module tb_udp_rx_props;
  localparam int unsigned BEAT_W = 4;  // saturates at 15 beats: short to simulate

  logic              clk = 1'b0;
  logic              rst = 1'b1;
  logic              s_tvalid = 1'b0;
  logic              s_tlast = 1'b0;
  logic [       7:0] s_tkeep = '0;
  logic              m_tvalid = 1'b0;
  logic              m_tlast = 1'b0;
  logic [       7:0] m_tkeep = '0;
  logic              m_tuser = 1'b0;
  logic [       7:0] meta = '0;
  logic              stat_valid = 1'b0;
  logic              in_frame_q = 1'b0;
  logic [BEAT_W-1:0] beat_cnt_q = '0;
  logic              hdr_done_q = 1'b0;
  logic              stage_b_q = 1'b0;
  logic              pay_q = 1'b0;
  logic              pay = 1'b0;
  logic              tail_q = 1'b0;

  always begin
    #3.2 clk = ~clk;
  end

  udp_rx_props #(
      .BEAT_W(BEAT_W),
      .META_W(8)
  ) u_props (
      .clk       (clk),
      .rst       (rst),
      .s_tvalid  (s_tvalid),
      .s_tlast   (s_tlast),
      .s_tkeep   (s_tkeep),
      .m_tvalid  (m_tvalid),
      .m_tlast   (m_tlast),
      .m_tkeep   (m_tkeep),
      .m_tuser   (m_tuser),
      .meta      (meta),
      .stat_valid(stat_valid),
      .in_frame_q(in_frame_q),
      .beat_cnt_q(beat_cnt_q),
      .hdr_done_q(hdr_done_q),
      .stage_b_q (stage_b_q),
      .pay_q     (pay_q),
      .pay       (pay),
      .tail_q    (tail_q)
  );

  // 1 when this run violates property k.
  function automatic logic v(input int k);
`ifdef HFT_VIOLATE
    return k == `HFT_VIOLATE;
`else
    return k < 0;
`endif  // HFT_VIOLATE
  endfunction

  initial begin
    repeat (2) @(negedge clk);
    rst = 1'b0;

    // Frame 1. SOF: the previous verdict must be clear (8: a_sof_clean).
    @(negedge clk);
    s_tvalid   = 1'b1;
    s_tkeep    = 8'hFF;
    hdr_done_q = v(8);
    // Beat 1 (1: a_in_keep_full).
    @(negedge clk);
    in_frame_q = 1'b1;
    beat_cnt_q = 1;
    s_tkeep    = v(1) ? 8'h7F : 8'hFF;
    hdr_done_q = 1'b0;
    // Beat 2, stage B: the headers pass (6: a_out_only_after_hdr needs this pay).
    @(negedge clk);
    beat_cnt_q = 2;
    s_tkeep    = 8'hFF;
    stage_b_q  = 1'b1;
    pay        = !v(6);
    // Beat 3, first payload beat (3: a_out_keep_full, 5: a_out_user_last).
    @(negedge clk);
    beat_cnt_q = 3;
    stage_b_q  = 1'b0;
    hdr_done_q = 1'b1;
    pay_q      = 1'b1;
    pay        = 1'b1;
    m_tvalid   = 1'b1;
    m_tkeep    = v(3) ? 8'h7F : 8'hFF;
    m_tuser    = v(5);
    meta       = 8'hA5;
    // Beat 4 = input tlast and the last payload beat (2: a_in_keep_contig,
    // 4: a_out_keep_contig, 7: a_meta_stable).
    @(negedge clk);
    beat_cnt_q = 4;
    s_tlast    = 1'b1;
    s_tkeep    = v(2) ? 8'h05 : 8'h0F;
    m_tlast    = 1'b1;
    m_tkeep    = v(4) ? 8'h0D : 8'h0F;
    m_tuser    = 1'b1;  // abort is legal on the last beat
    meta       = v(7) ? 8'h5A : 8'hA5;
    // The stat event, 1 cycle after the input tlast.
    @(negedge clk);
    s_tvalid   = 1'b0;
    s_tlast    = 1'b0;
    in_frame_q = 1'b0;
    beat_cnt_q = 0;
    hdr_done_q = 1'b0;
    pay_q      = 1'b0;
    pay        = 1'b0;
    m_tvalid   = 1'b0;
    m_tlast    = 1'b0;
    m_tuser    = 1'b0;
    stat_valid = 1'b1;
    // No input tlast before this cycle (10: a_stat_after_tlast).
    @(negedge clk);
    stat_valid = v(10);

    // Frame 2: the beat counter at its maximum stays there (9: a_beat_cnt_saturates).
    @(negedge clk);
    stat_valid = 1'b0;
    s_tvalid   = 1'b1;
    s_tkeep    = 8'hFF;
    @(negedge clk);
    in_frame_q = 1'b1;
    beat_cnt_q = '1;
    @(negedge clk);
    beat_cnt_q = v(9) ? '0 : '1;
    @(negedge clk);
    s_tlast = 1'b1;
    @(negedge clk);
    s_tvalid   = 1'b0;
    s_tlast    = 1'b0;
    in_frame_q = 1'b0;
    beat_cnt_q = 0;
    stat_valid = 1'b1;
    @(negedge clk);
    stat_valid = 1'b0;
    repeat (3) @(negedge clk);
    $display("HFT_TB_RESULT: PASS (udp_rx_props)");
    $finish;
  end
endmodule
