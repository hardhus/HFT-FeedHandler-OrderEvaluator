// tb_mold_props: checks the mold_props checker itself (audit/11 S5). Drives its ports directly,
// cycle by cycle: a 3-beat packet with two messages (the second over a skipped seq) and its
// event, a 1-beat DISABLED packet and its event, a 1-beat packet whose event is the last one.
// Every property's antecedent is hit, so a clean run proves the checker quiet on legal traffic.
// With HFT_VIOLATE=<k> exactly one property is violated (numbers in test_sva_checkers.py); its
// assertion must fire (Verilator stops and no PASS line is printed). Values are set after a
// falling edge and sampled at the next rising edge.
`timescale 1ns / 1ps
module tb_mold_props;
  import hft_pkg::*;

  logic                      clk = 1'b0;
  logic                      rst = 1'b1;
  logic                      s_tvalid = 1'b0;
  logic                      s_tlast = 1'b0;
  logic [               7:0] s_tkeep = '0;
  logic [               7:0] s_meta = '0;
  logic                      m_msg_valid = 1'b0;
  logic [MOLD_MSG_LEN_W-1:0] m_msg_len = '0;
  logic [8*MOLD_MAX_MSG-1:0] m_msg_data = '0;
  logic [              63:0] m_msg_seq = '0;
  logic                      m_pkt_valid = 1'b0;
  logic [               3:0] m_pkt_status = '0;
  logic [              15:0] m_pkt_msgs = '0;

  always begin
    #3.2 clk = ~clk;
  end

  mold_props #(
      .META_W(8)
  ) u_props (
      .clk         (clk),
      .rst         (rst),
      .s_tvalid    (s_tvalid),
      .s_tlast     (s_tlast),
      .s_tkeep     (s_tkeep),
      .s_meta      (s_meta),
      .m_msg_valid (m_msg_valid),
      .m_msg_len   (m_msg_len),
      .m_msg_data  (m_msg_data),
      .m_msg_seq   (m_msg_seq),
      .m_pkt_valid (m_pkt_valid),
      .m_pkt_status(m_pkt_status),
      .m_pkt_msgs  (m_pkt_msgs)
  );

  // 1 when this run violates property k.
  function automatic logic v(input int k);
`ifdef HFT_VIOLATE
    return k == `HFT_VIOLATE;
`else
    return k < 0;
`endif  // HFT_VIOLATE
  endfunction

  // n bytes of 0xA5, the rest 0.
  function automatic logic [8*MOLD_MAX_MSG-1:0] bytes(input int n);
    bytes = '0;
    for (int i = 0; i < n; i++) begin
      bytes[8*i+:8] = 8'hA5;
    end
  endfunction

  task automatic message(input int len, input logic [63:0] seq, input logic tail);
    m_msg_valid = 1'b1;
    m_msg_len   = MOLD_MSG_LEN_W'(len);
    m_msg_data  = bytes(len) | (tail ? bytes(len + 1) : '0);
    m_msg_seq   = seq;
  endtask

  initial begin
    repeat (2) @(negedge clk);
    rst = 1'b0;

    // Packet A: 3 beats (beat 1: 1 a_in_keep_full, 3 a_in_meta_stable).
    @(negedge clk);
    s_tvalid = 1'b1;
    s_tkeep  = 8'hFF;
    s_meta   = 8'hA5;
    @(negedge clk);
    s_tkeep = v(1) ? 8'h7F : 8'hFF;
    s_meta  = v(3) ? 8'h5A : 8'hA5;
    // tlast beat (2: a_in_keep_contig) and the first message (4: a_msg_len, 5: a_msg_zero_tail).
    @(negedge clk);
    s_tlast = 1'b1;
    s_tkeep = v(2) ? 8'h05 : 8'h0F;
    s_meta  = 8'hA5;
    message(v(4) ? 5 : 12, 64'd100, v(5));
    // Second message: seq 102 (101 skipped as oversize) (6: a_msg_seq_up).
    @(negedge clk);
    s_tvalid = 1'b0;
    s_tlast  = 1'b0;
    s_meta   = 8'h3C;  // free between packets
    message(20, v(6) ? 64'd100 : 64'd102, 1'b0);
    @(negedge clk);
    m_msg_valid = 1'b0;
    // The event, 3 cycles after tlast (10: a_pkt_msgs_count).
    @(negedge clk);
    m_pkt_valid  = 1'b1;
    m_pkt_status = 4'(MOLD_OK);
    m_pkt_msgs   = v(10) ? 16'd1 : 16'd2;

    // Packet B: 1 beat, DISABLED (11: a_unframed_no_msgs, 12: a_status_legal).
    @(negedge clk);
    m_pkt_valid = 1'b0;
    s_tvalid    = 1'b1;
    s_tlast     = 1'b1;
    s_tkeep     = 8'h0F;
    @(negedge clk);
    s_tvalid = 1'b0;
    s_tlast  = 1'b0;
    if (v(11)) begin
      message(12, 64'd7, 1'b0);
    end
    @(negedge clk);
    m_msg_valid = 1'b0;
    @(negedge clk);
    m_pkt_valid  = 1'b1;
    m_pkt_status = v(12) ? 4'd11 : 4'(MOLD_DISABLED);
    m_pkt_msgs   = v(11) ? 16'd1 : 16'd0;
    @(negedge clk);
    m_pkt_valid = 1'b0;
    // An event with no input tlast 3 cycles before (8: a_pkt_after_tlast).
    @(negedge clk);
    m_pkt_valid  = v(8);
    m_pkt_status = 4'(MOLD_RUNT);
    m_pkt_msgs   = '0;
    @(negedge clk);
    m_pkt_valid = 1'b0;

    // Packet C: 1 beat; its event (9: a_tlast_gets_pkt).
    @(negedge clk);
    s_tvalid = !v(8);  // after the extra event of 8, pend is off by one: no packet then
    s_tlast  = !v(8);
    s_tkeep  = 8'h01;
    @(negedge clk);
    s_tvalid = 1'b0;
    s_tlast  = 1'b0;
    repeat (2) @(negedge clk);
    m_pkt_valid  = !v(9) && !v(8);
    m_pkt_status = 4'(MOLD_RUNT);
    @(negedge clk);
    m_pkt_valid = 1'b0;

    // Nothing is waiting for an event now (7: a_msg_in_packet).
    @(negedge clk);
    if (v(7)) begin
      message(12, 64'd1, 1'b0);
    end
    @(negedge clk);
    m_msg_valid = 1'b0;
    repeat (3) @(negedge clk);
    $display("HFT_TB_RESULT: PASS (mold_props)");
    $finish;
  end
endmodule
