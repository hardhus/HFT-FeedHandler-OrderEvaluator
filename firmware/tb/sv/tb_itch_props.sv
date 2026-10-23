// tb_itch_props: checks the itch_props checker itself (audit/12 S5). Drives its ports directly,
// cycle by cycle: the reset and the 2048-cycle bitmap sweep, a 12-byte ADMIN message (OUT, with a
// header), an 8-byte UNKNOWN message (OUT, no header), a MASKED message, a packet event, an
// accepted command and its response. Every property's antecedent is hit, so a clean run proves
// the checker quiet on legal traffic. With HFT_VIOLATE=<k> exactly one property is violated
// (numbers in test_sva_checkers.py); its assertion must fire (Verilator stops and no PASS line is
// printed). Values are set after a falling edge and sampled at the next rising edge.
`timescale 1ns / 1ps
module tb_itch_props;
  import hft_pkg::*;

  localparam int unsigned NORM_W = 64 * 5 + 32 * 2 + 8 * 3;

  logic                      clk = 1'b0;
  logic                      rst = 1'b1;
  logic                      s_msg_valid = 1'b0;
  logic [8*MOLD_MAX_MSG-1:0] s_msg_data = '0;
  logic [MOLD_MSG_LEN_W-1:0] s_msg_len = '0;
  logic                      s_pkt_valid = 1'b0;
  logic                      m_ev_valid = 1'b0;
  logic [               2:0] m_ev_class = '0;
  logic [               7:0] m_ev_type = '0;
  logic [MOLD_MSG_LEN_W-1:0] m_ev_len = '0;
  logic [8*MOLD_MAX_MSG-1:0] m_ev_raw = '0;
  logic [              79:0] m_ev_header = '0;
  logic [        NORM_W-1:0] m_ev_norm = '0;
  logic                      m_res_valid = 1'b0;
  logic [               2:0] m_res_class = '0;
  logic [               1:0] m_res_outcome = '0;
  logic                      m_pkt_valid = 1'b0;
  logic                      sym_req_valid = 1'b0;
  logic                      sym_req_ready = 1'b0;
  logic                      sym_rsp_valid = 1'b0;

  always begin
    #3.2 clk = ~clk;
  end

  itch_props u_props (
      .clk          (clk),
      .rst          (rst),
      .s_msg_valid  (s_msg_valid),
      .s_msg_data   (s_msg_data),
      .s_msg_len    (s_msg_len),
      .s_pkt_valid  (s_pkt_valid),
      .m_ev_valid   (m_ev_valid),
      .m_ev_class   (m_ev_class),
      .m_ev_type    (m_ev_type),
      .m_ev_len     (m_ev_len),
      .m_ev_raw     (m_ev_raw),
      .m_ev_header  (m_ev_header),
      .m_ev_norm    (m_ev_norm),
      .m_res_valid  (m_res_valid),
      .m_res_class  (m_res_class),
      .m_res_outcome(m_res_outcome),
      .m_pkt_valid  (m_pkt_valid),
      .sym_req_valid(sym_req_valid),
      .sym_req_ready(sym_req_ready),
      .sym_rsp_valid(sym_rsp_valid)
  );

  // 1 when this run violates property k.
  function automatic logic v(input int k);
`ifdef HFT_VIOLATE
    return k == `HFT_VIOLATE;
`else
    return k < 0;
`endif  // HFT_VIOLATE
  endfunction

  // A message vector: type byte, then n - 1 bytes of 0xA5, the rest 0.
  function automatic logic [8*MOLD_MAX_MSG-1:0] msg(input logic [7:0] typ, input int n);
    msg      = '0;
    msg[7:0] = typ;
    for (int i = 1; i < n; i++) begin
      msg[8*i+:8] = 8'hA5;
    end
  endfunction

  task automatic idle_outputs();
    m_ev_valid    = 1'b0;
    m_res_valid   = 1'b0;
    m_pkt_valid   = 1'b0;
    sym_rsp_valid = 1'b0;
  endtask

  // A result (and an event when OUT) for message vector d of length n.
  task automatic result(input logic [2:0] cls, input logic [1:0] outcome, input logic ev,
                        input logic [8*MOLD_MAX_MSG-1:0] d, input int n);
    m_res_valid   = 1'b1;
    m_res_class   = cls;
    m_res_outcome = outcome;
    m_ev_valid    = ev;
    m_ev_class    = cls;
    m_ev_raw      = d;
    m_ev_len      = MOLD_MSG_LEN_W'(n);
    m_ev_type     = d[7:0];
    m_ev_header   = n >= int'(ITCH_HDR_LEN) ? 80'hA5A5_A5A5_A5A5_A5A5_A5A5 : '0;
    m_ev_norm     = '0;
  endtask

  initial begin
    repeat (2) @(negedge clk);
    rst = 1'b0;
    // The sweep: sym_req_ready low for 2048 cycles (15: high once in the middle).
    for (int i = 0; i < 2048; i++) begin
      sym_req_ready = v(15) && i == 1000;
      @(negedge clk);
    end
    // 16: still low one cycle after the sweep.
    sym_req_ready = !v(16);
    @(negedge clk);
    sym_req_ready = 1'b1;

    // Message 1: ADMIN "S", 12 bytes (1: length 5 -> a_in_len).
    s_msg_valid   = 1'b1;
    s_msg_data    = msg(8'h53, 12);
    s_msg_len     = MOLD_MSG_LEN_W'(v(1) ? 5 : 12);
    @(negedge clk);
    // Message 2: UNKNOWN 0x00, 8 bytes (no header).
    s_msg_data = msg(8'h00, 8);
    s_msg_len  = MOLD_MSG_LEN_W'(8);
    @(negedge clk);
    // Message 3 (MASKED); result 1: ADMIN, OUT.
    s_msg_data = msg(8'h41, 36);
    s_msg_len  = MOLD_MSG_LEN_W'(36);
    if (!v(2)) begin  // 2: a_msg_gets_res
      result(v(5) ? 3'd6 : 3'(ITCH_ADMIN), 2'(ITCH_RES_OUT), !v(4), msg(8'h53, 12), v(1) ? 5 : 12);
      if (v(5)) begin  // 5: a_class_legal (the event carries the same class)
        m_ev_class = 3'd6;
      end
      if (v(7)) begin  // 7: a_ev_class_same
        m_ev_class = 3'(ITCH_ORDER);
      end
      if (v(8)) begin  // 8: a_undecoded_zero
        m_ev_norm = NORM_W'(1);
      end
      if (v(9)) begin  // 9: a_raw_is_input
        m_ev_raw[8*3+:8] = 8'h00;
      end
    end
    @(negedge clk);
    s_msg_valid = 1'b0;
    idle_outputs();
    // Result 2: UNKNOWN, OUT, no header (10: a_short_no_header).
    result(3'(ITCH_UNKNOWN), 2'(ITCH_RES_OUT), 1'b1, msg(8'h00, 8), 8);
    if (v(10)) begin
      m_ev_header = 80'd1;
    end
    @(negedge clk);
    idle_outputs();
    // Result 3: ORDER, MASKED (6: outcome 3).
    result(3'(ITCH_ORDER), v(6) ? 2'd3 : 2'(ITCH_RES_MASKED), 1'b0, msg(8'h41, 36), 36);
    @(negedge clk);
    idle_outputs();
    // 3: a result with no message 2 cycles before.
    if (v(3)) begin
      result(3'(ITCH_ORDER), 2'(ITCH_RES_MASKED), 1'b0, '0, 36);
    end
    @(negedge clk);
    idle_outputs();

    // A packet event, forwarded 2 cycles later (11: not forwarded).
    s_pkt_valid = 1'b1;
    @(negedge clk);
    s_pkt_valid = 1'b0;
    @(negedge clk);
    m_pkt_valid = !v(11);
    @(negedge clk);
    // 12: a forwarded event with no input 2 cycles before.
    m_pkt_valid = v(12);
    @(negedge clk);
    m_pkt_valid   = 1'b0;

    // A command, its response 1 cycle later (13: no response).
    sym_req_valid = 1'b1;
    @(negedge clk);
    sym_req_valid = 1'b0;
    sym_rsp_valid = !v(13);
    @(negedge clk);
    // 14: a response with no accepted command before.
    sym_rsp_valid = v(14);
    @(negedge clk);
    sym_rsp_valid = 1'b0;
    repeat (3) @(negedge clk);
    $display("HFT_TB_RESULT: PASS (itch_props)");
    $finish;
  end
endmodule
