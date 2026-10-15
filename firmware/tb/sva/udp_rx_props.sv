// udp_rx_props: eth_ip_udp_rx checks (audit/09 S4); bind to the eth_ip_udp_rx instance.
// Simulation only. Disabled until the first reset has been seen, because flops start with
// random values (--x-initial unique). The count property "one stat event per frame" lives in
// the cocotb stat scoreboard; these check timing, framing and internal state.
module udp_rx_props #(
    parameter int unsigned BEAT_W = 11,
    parameter int unsigned META_W = 161
) (
    input logic              clk,
    input logic              rst,
    // MAC RX input (contract checks: a failure here is a testbench bug)
    input logic              s_tvalid,
    input logic              s_tlast,
    input logic [       7:0] s_tkeep,
    // UDP payload output
    input logic              m_tvalid,
    input logic              m_tlast,
    input logic [       7:0] m_tkeep,
    input logic              m_tuser,
    input logic [META_W-1:0] meta,        // every m_udp_* output
    input logic              stat_valid,
    // internal state
    input logic              in_frame_q,
    input logic [BEAT_W-1:0] beat_cnt_q,
    input logic              hdr_done_q,
    input logic              stage_b_q,
    input logic              pay_q,
    input logic              pay,
    input logic              tail_q
);
  logic armed;  // sim-only: initial + always (not always_ff) on purpose
  logic pkt_open;  // an output packet has started and has not ended yet
  logic sof;

  assign sof = s_tvalid && !in_frame_q;
  initial begin
    armed    = 1'b0;
    pkt_open = 1'b0;
  end
  always @(posedge clk) begin
    if (rst) begin
      armed    <= 1'b1;
      pkt_open <= 1'b0;
    end else if (m_tvalid) begin
      pkt_open <= !m_tlast;
    end
  end

  // tkeep = 1..8 low bytes: 01, 03, 07, ..., FF.
  function automatic logic keep_contig(input logic [7:0] keep);
    return keep != '0 && (keep & (keep + 8'd1)) == '0;
  endfunction

  // ---- input contract (MAC) ----
  a_in_keep_full :
  assert property (@(posedge clk) disable iff (rst || !armed) s_tvalid && !s_tlast |-> s_tkeep == 8'hFF)
  else begin
    $error("udp_rx_props: input beat before tlast without a full tkeep");
  end

  a_in_keep_contig :
  assert property (@(posedge clk) disable iff (rst || !armed) s_tvalid && s_tlast |-> keep_contig(
      s_tkeep
  ))
  else begin
    $error("udp_rx_props: input tlast beat with a non-contiguous tkeep");
  end

  // ---- payload output (09 §2.4) ----
  a_out_keep_full :
  assert property (@(posedge clk) disable iff (rst || !armed) m_tvalid && !m_tlast |-> m_tkeep == 8'hFF)
  else begin
    $error("udp_rx_props: payload beat before tlast without a full tkeep");
  end

  a_out_keep_contig :
  assert property (@(posedge clk) disable iff (rst || !armed) m_tvalid && m_tlast |-> keep_contig(
      m_tkeep
  ))
  else begin
    $error("udp_rx_props: payload tlast beat with an empty or non-contiguous tkeep");
  end

  a_out_user_last :
  assert property (@(posedge clk) disable iff (rst || !armed) m_tvalid && !m_tlast |-> !m_tuser)
  else begin
    $error("udp_rx_props: abort (tuser) before the payload tlast");
  end

  // A payload beat only for a frame whose headers passed (or its tail, decided at tlast).
  a_out_only_after_hdr :
  assert property (@(posedge clk) disable iff (rst || !armed) m_tvalid |-> $past(pay || tail_q))
  else begin
    $error("udp_rx_props: payload beat for a frame whose headers did not pass");
  end

  // F3 may read the metadata on any beat of the packet.
  a_meta_stable :
  assert property (@(posedge clk) disable iff (rst || !armed) pkt_open |-> $stable(meta))
  else begin
    $error("udp_rx_props: m_udp_* changed inside a payload packet");
  end

  // ---- frame state ----
  // K1: nothing of the previous frame's verdict survives into a new frame.
  a_sof_clean :
  assert property (@(posedge clk) disable iff (rst || !armed) sof |-> !hdr_done_q && !stage_b_q && !pay_q)
  else begin
    $error("udp_rx_props: header verdict of the previous frame still set at SOF (K1)");
  end

  // K4: the beat counter saturates instead of wrapping.
  a_beat_cnt_saturates :
  assert property (@(posedge clk) disable iff (rst || !armed)
      beat_cnt_q == '1 && s_tvalid && !s_tlast |=> beat_cnt_q == '1)
  else begin
    $error("udp_rx_props: beat counter wrapped (K4)");
  end

  a_stat_after_tlast :
  assert property (@(posedge clk) disable iff (rst || !armed) stat_valid |-> $past(
      s_tvalid && s_tlast
  ))
  else begin
    $error("udp_rx_props: stat event not 1 cycle after an input tlast");
  end
endmodule
