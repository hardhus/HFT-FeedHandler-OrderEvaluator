// mold_props: moldudp64_rx checks (audit/11 §2.9); bind to the moldudp64_rx instance.
// Simulation only. Disabled until the first reset has been seen, because flops start with
// random values (--x-initial unique). Message contents and packet outcomes are checked against
// the golden model by the cocotb monitor; these check framing, ordering and counts.
module mold_props
  import hft_pkg::*;
#(
    parameter int unsigned META_W = 83  // s_udp_len, s_udp_filter_idx, s_udp_sof_ts
) (
    input logic                      clk,
    input logic                      rst,
    // UDP payload input (contract checks: a failure here is a testbench or F2 bug)
    input logic                      s_tvalid,
    input logic                      s_tlast,
    input logic [               7:0] s_tkeep,
    input logic [        META_W-1:0] s_meta,
    // message output
    input logic                      m_msg_valid,
    input logic [MOLD_MSG_LEN_W-1:0] m_msg_len,
    input logic [8*MOLD_MAX_MSG-1:0] m_msg_data,
    input logic [              63:0] m_msg_seq,
    // packet event
    input logic                      m_pkt_valid,
    input logic [               3:0] m_pkt_status,
    input logic [              15:0] m_pkt_msgs
);
  logic        armed;  // sim-only: initial + always (not always_ff) on purpose
  logic        in_q;  // an input packet has started and has not ended yet
  logic [ 3:0] pend;  // input packets started whose event has not come
  logic [15:0] n_msgs;  // messages since the last packet event
  logic [63:0] prev_seq;  // the last message's seq, valid when n_msgs != 0
  logic        sof;

  assign sof = s_tvalid && !in_q;
  initial begin
    armed  = 1'b0;
    in_q   = 1'b0;
    pend   = '0;
    n_msgs = '0;
  end
  always @(posedge clk) begin
    if (rst) begin
      armed  <= 1'b1;
      in_q   <= 1'b0;
      pend   <= '0;
      n_msgs <= '0;
    end else begin
      if (s_tvalid) begin
        in_q <= !s_tlast;
      end
      pend <= pend + 4'(sof) - 4'(m_pkt_valid);
      if (m_pkt_valid) begin
        n_msgs <= '0;
      end else if (m_msg_valid) begin
        n_msgs <= n_msgs + 16'd1;
      end
      if (m_msg_valid) begin
        prev_seq <= m_msg_seq;
      end
    end
  end

  // tkeep = 1..8 low bytes: 01, 03, 07, ..., FF.
  function automatic logic keep_contig(input logic [7:0] keep);
    return keep != '0 && (keep & (keep + 8'd1)) == '0;
  endfunction

  // ---- input contract (eth_ip_udp_rx payload) ----
  a_in_keep_full :
  assert property (@(posedge clk) disable iff (rst || !armed) s_tvalid && !s_tlast |-> s_tkeep == 8'hFF)
  else begin
    $error("mold_props: input beat before tlast without a full tkeep");
  end

  a_in_keep_contig :
  assert property (@(posedge clk) disable iff (rst || !armed) s_tvalid && s_tlast |-> keep_contig(
      s_tkeep
  ))
  else begin
    $error("mold_props: input tlast beat with a non-contiguous tkeep");
  end

  a_in_meta_stable :
  assert property (@(posedge clk) disable iff (rst || !armed) in_q |-> $stable(s_meta))
  else begin
    $error("mold_props: s_udp_* changed inside an input packet");
  end

  // ---- messages ----
  a_msg_len :
  assert property (@(posedge clk) disable iff (rst || !armed)
      m_msg_valid |-> m_msg_len >= MOLD_MSG_LEN_W'(MOLD_MIN_LEN) &&
                      m_msg_len <= MOLD_MSG_LEN_W'(MOLD_MAX_MSG))
  else begin
    $error("mold_props: message length outside MOLD_MIN_LEN..MOLD_MAX_MSG");
  end

  a_msg_zero_tail :
  assert property (@(posedge clk) disable iff (rst || !armed)
      m_msg_valid |-> (m_msg_data >> (8 * int'(m_msg_len))) == '0)
  else begin
    $error("mold_props: message bytes after the length are not 0");
  end

  // In a packet the seq goes up: by 1, or more over skipped oversize blocks (count <= 0xFFFE).
  a_msg_seq_up :
  assert property (@(posedge clk) disable iff (rst || !armed)
      m_msg_valid && n_msgs != '0 |-> m_msg_seq - prev_seq inside {[64'd1 : 64'hFFFE]})
  else begin
    $error("mold_props: message seq does not go up within a packet");
  end

  a_msg_in_packet :
  assert property (@(posedge clk) disable iff (rst || !armed) m_msg_valid |-> pend != '0)
  else begin
    $error("mold_props: message with no input packet awaiting its event");
  end

  // ---- packet events: one per input packet, 3 cycles after its tlast ----
  a_pkt_after_tlast :
  assert property (@(posedge clk) disable iff (rst || !armed) m_pkt_valid |-> $past(
      s_tvalid && s_tlast, 3
  ))
  else begin
    $error("mold_props: packet event not 3 cycles after an input tlast");
  end

  a_tlast_gets_pkt :
  assert property (@(posedge clk) disable iff (rst || !armed) s_tvalid && s_tlast |-> ##3 m_pkt_valid)
  else begin
    $error("mold_props: input tlast without a packet event 3 cycles later");
  end

  a_pkt_msgs_count :
  assert property (@(posedge clk) disable iff (rst || !armed) m_pkt_valid |-> m_pkt_msgs == n_msgs)
  else begin
    $error("mold_props: m_pkt_msgs differs from the messages output since the last event");
  end

  // Packets that are never framed output nothing.
  a_unframed_no_msgs :
  assert property (@(posedge clk) disable iff (rst || !armed)
      m_pkt_valid && m_pkt_status inside {MOLD_DISABLED, MOLD_OTHER_FEED, MOLD_RUNT, MOLD_SESSION}
      |-> n_msgs == '0)
  else begin
    $error("mold_props: messages output for a DISABLED, OTHER_FEED, RUNT or SESSION packet");
  end

  a_status_legal :
  assert property (@(posedge clk) disable iff (rst || !armed) m_pkt_valid |-> m_pkt_status <= 4'(MOLD_MALFORMED))
  else begin
    $error("mold_props: packet status is not a mold_status_e code");
  end
endmodule
