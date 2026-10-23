// itch_props: itch_decoder checks (audit/12 §2.10); bind to the itch_decoder instance.
// Simulation only. Disabled until the first reset has been seen, because flops start with
// random values (--x-initial unique). Classes, outcomes and field values are checked against the
// golden model by the cocotb monitor; these check counts, timing, ordering and the zero rules.
module itch_props
  import hft_pkg::*;
#(
    parameter int unsigned NORM_W = 64 * 5 + 32 * 2 + 8 * 3  // the normalized m_ev_* fields
) (
    input logic                      clk,
    input logic                      rst,
    // message input (contract checks: a failure here is a testbench or moldudp64_rx bug)
    input logic                      s_msg_valid,
    input logic [8*MOLD_MAX_MSG-1:0] s_msg_data,
    input logic [MOLD_MSG_LEN_W-1:0] s_msg_len,
    input logic                      s_pkt_valid,
    // events and results
    input logic                      m_ev_valid,
    input logic [               2:0] m_ev_class,
    input logic [               7:0] m_ev_type,
    input logic [MOLD_MSG_LEN_W-1:0] m_ev_len,
    input logic [8*MOLD_MAX_MSG-1:0] m_ev_raw,
    input logic [              79:0] m_ev_header,    // locate, tracking, timestamp
    input logic [        NORM_W-1:0] m_ev_norm,
    input logic                      m_res_valid,
    input logic [               2:0] m_res_class,
    input logic [               1:0] m_res_outcome,
    input logic                      m_pkt_valid,
    // symbol bitmap commands
    input logic                      sym_req_valid,
    input logic                      sym_req_ready,
    input logic                      sym_rsp_valid
);
  localparam int unsigned SWEEP = 2048;  // bitmap words written 0 after a reset

  logic        armed;  // sim-only: initial + always (not always_ff) on purpose
  logic [11:0] sweep;  // cycles of the sweep still to come

  initial begin
    armed = 1'b0;
    sweep = '0;
  end
  always @(posedge clk) begin
    if (rst) begin
      armed <= 1'b1;
      sweep <= 12'(SWEEP);
    end else if (sweep != '0) begin
      sweep <= sweep - 12'd1;
    end
  end

  // ---- input contract (moldudp64_rx messages) ----
  a_in_len :
  assert property (@(posedge clk) disable iff (rst || !armed)
      s_msg_valid |-> s_msg_len >= MOLD_MSG_LEN_W'(MOLD_MIN_LEN) &&
                      s_msg_len <= MOLD_MSG_LEN_W'(MOLD_MAX_MSG))
  else begin
    $error("itch_props: input message length outside MOLD_MIN_LEN..MOLD_MAX_MSG");
  end

  // ---- one result per message, 2 cycles after it ----
  a_msg_gets_res :
  assert property (@(posedge clk) disable iff (rst || !armed) s_msg_valid |-> ##2 m_res_valid)
  else begin
    $error("itch_props: input message without a result 2 cycles later");
  end

  a_res_after_msg :
  assert property (@(posedge clk) disable iff (rst || !armed) m_res_valid |-> $past(s_msg_valid, 2))
  else begin
    $error("itch_props: result not 2 cycles after an input message");
  end

  a_ev_iff_out :
  assert property (@(posedge clk) disable iff (rst || !armed)
      m_ev_valid == (m_res_valid && m_res_outcome == 2'(ITCH_RES_OUT)))
  else begin
    $error("itch_props: m_ev_valid differs from a result with outcome OUT");
  end

  a_class_legal :
  assert property (@(posedge clk) disable iff (rst || !armed)
      m_res_valid |-> m_res_class <= 3'(ITCH_MALFORMED))
  else begin
    $error("itch_props: result class is not an itch_class_e code");
  end

  a_outcome_legal :
  assert property (@(posedge clk) disable iff (rst || !armed)
      m_res_valid |-> m_res_outcome <= 2'(ITCH_RES_MASKED))
  else begin
    $error("itch_props: result outcome is not an itch_outcome_e code");
  end

  a_ev_class_same :
  assert property (@(posedge clk) disable iff (rst || !armed) m_ev_valid |-> m_ev_class == m_res_class)
  else begin
    $error("itch_props: event class differs from the result class");
  end

  // ---- event contents ----
  a_undecoded_zero :
  assert property (@(posedge clk) disable iff (rst || !armed)
      m_ev_valid && !(m_ev_class inside {3'(ITCH_ORDER), 3'(ITCH_TRADE)}) |-> m_ev_norm == '0)
  else begin
    $error("itch_props: normalized fields not 0 for an ADMIN, AUCTION, UNKNOWN or MALFORMED event");
  end

  a_raw_is_input :
  assert property (@(posedge clk) disable iff (rst || !armed) m_ev_valid |-> m_ev_raw == $past(
      s_msg_data, 2
  ) && m_ev_len == $past(
      s_msg_len, 2
  ) && m_ev_type == m_ev_raw[7:0])
  else begin
    $error("itch_props: m_ev_raw, m_ev_len or m_ev_type is not the input message");
  end

  a_short_no_header :
  assert property (@(posedge clk) disable iff (rst || !armed)
      m_ev_valid && m_ev_len < MOLD_MSG_LEN_W'(ITCH_HDR_LEN) |-> m_ev_header == '0)
  else begin
    $error("itch_props: header fields not 0 for a message shorter than the header");
  end

  // ---- packet events: passed through 2 cycles later (after the packet's message results) ----
  a_pkt_gets_fwd :
  assert property (@(posedge clk) disable iff (rst || !armed) s_pkt_valid |-> ##2 m_pkt_valid)
  else begin
    $error("itch_props: input packet event not forwarded 2 cycles later");
  end

  a_pkt_after_input :
  assert property (@(posedge clk) disable iff (rst || !armed) m_pkt_valid |-> $past(s_pkt_valid, 2))
  else begin
    $error("itch_props: packet event not 2 cycles after an input packet event");
  end

  // ---- symbol bitmap commands ----
  a_cmd_gets_rsp :
  assert property (@(posedge clk) disable iff (rst || !armed)
      sym_req_valid && sym_req_ready |=> sym_rsp_valid)
  else begin
    $error("itch_props: accepted command without a response 1 cycle later");
  end

  a_rsp_after_cmd :
  assert property (@(posedge clk) disable iff (rst || !armed) sym_rsp_valid |-> $past(
      sym_req_valid && sym_req_ready
  ))
  else begin
    $error("itch_props: command response not 1 cycle after an accepted command");
  end

  a_no_cmd_in_sweep :
  assert property (@(posedge clk) disable iff (rst || !armed) sweep != '0 |-> !sym_req_ready)
  else begin
    $error("itch_props: sym_req_ready during the bitmap sweep");
  end

  a_ready_after_sweep :
  assert property (@(posedge clk) disable iff (rst || !armed) sweep == '0 |-> sym_req_ready)
  else begin
    $error("itch_props: sym_req_ready low after the bitmap sweep");
  end
endmodule
