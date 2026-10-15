// eth_ip_udp_rx: Ethernet/IPv4/UDP receive filter; streams the UDP payload realigned to byte 0.
// Clock domain : clk (net_clk)
// Latency      : first payload beat 7 cycles after the SOF beat (no tvalid gaps), unless it is
//                also the last beat and is held for the input tlast (padding: 09 §2.4);
//                payload tlast 1 cycle after input tlast (2 if the last input beat holds > 2
//                payload bytes); stat event 1 cycle after input tlast
// Throughput   : line rate, no backpressure (s_axis_rx_tready = 1, no m_axis_udp_tready)
// Config       : every cfg_* input is sampled in one cycle per frame (stage A); cfg_rx_en at SOF
// Reset        : rst with the MAC reset or between frames; the first beat after reset is a SOF
module eth_ip_udp_rx
  import hft_pkg::*;
#(
    parameter  int unsigned N_FILTERS = 8,
    localparam int unsigned FIDX_W    = (N_FILTERS > 1) ? $clog2(N_FILTERS) : 1
) (
    input  logic                             clk,
    input  logic                             rst,
    // MAC RX (AXI-Stream, byte 0 = tdata[7:0], tuser = bad frame, valid on the tlast beat)
    input  logic       [         63:0]       s_axis_rx_tdata,
    input  logic       [          7:0]       s_axis_rx_tkeep,
    input  logic                             s_axis_rx_tvalid,
    output logic                             s_axis_rx_tready,
    input  logic                             s_axis_rx_tlast,
    input  logic                             s_axis_rx_tuser,
    // Configuration (net_clk domain; from hft_csr_wrap in F5)
    input  logic                             cfg_rx_en,
    input  logic       [         47:0]       cfg_local_mac,
    input  logic                             cfg_accept_bcast,
    input  logic       [N_FILTERS-1:0][31:0] cfg_filter_ip,
    input  logic       [N_FILTERS-1:0][15:0] cfg_filter_port,
    input  logic       [N_FILTERS-1:0]       cfg_filter_en,
    // Free-running timestamp (timestamp_counter arrives in F8)
    input  logic       [         63:0]       ts_now,
    // UDP payload (byte 0 of the payload = tdata[7:0]; tuser = abort, valid on the tlast beat)
    output logic       [         63:0]       m_axis_udp_tdata,
    output logic       [          7:0]       m_axis_udp_tkeep,
    output logic                             m_axis_udp_tvalid,
    output logic                             m_axis_udp_tlast,
    output logic                             m_axis_udp_tuser,
    // Per-packet metadata: valid from the first payload beat through tlast
    output logic       [         63:0]       m_udp_sof_ts,
    output logic       [         31:0]       m_udp_src_ip,
    output logic       [         31:0]       m_udp_dst_ip,
    output logic       [         15:0]       m_udp_dst_port,
    output logic       [         15:0]       m_udp_len,          // payload bytes (UDP length - 8)
    output logic       [   FIDX_W-1:0]       m_udp_filter_idx,
    // One event per received frame (counters live in the CSR block, F5)
    output logic                             stat_valid,
    output rx_reason_e                       stat_reason,
    output logic       [         15:0]       stat_bytes          // frame length, saturating
);

  localparam int unsigned BEAT_W = 11;  // saturating beat counter (K4): 2047 beats
  localparam int unsigned CSUM_W = 20;  // sum of the 10 IPv4 header words
  localparam logic [15:0] FRAG_MASK = 16'h3FFF;  // MF + fragment offset (DF, reserved: ignored)

  assign s_axis_rx_tready = 1'b1;

  // ---- frame tracking (control: sync reset) ----
  logic              in_frame_q;  // a SOF has been seen, tlast not yet
  logic [BEAT_W-1:0] beat_cnt_q;  // index of the next beat in the frame, saturating (K4)
  logic [      15:0] bytes_q;  // bytes before the next beat, saturating
  logic              sof;  // first beat of a frame (beat 0)
  logic              eof;  // last beat of a frame
  logic [       4:1] at_beat;  // header beat k is accepted this cycle

  assign sof = s_axis_rx_tvalid && !in_frame_q;
  assign eof = s_axis_rx_tvalid && s_axis_rx_tlast;
  for (genvar k = 1; k <= 4; k++) begin : g_at_beat
    assign at_beat[k] = s_axis_rx_tvalid && beat_cnt_q == BEAT_W'(k);
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      in_frame_q <= 1'b0;
      beat_cnt_q <= '0;
      bytes_q    <= '0;
    end else if (s_axis_rx_tvalid) begin
      in_frame_q <= !s_axis_rx_tlast;
      if (s_axis_rx_tlast) begin
        beat_cnt_q <= '0;
        bytes_q    <= '0;
      end else begin
        if (beat_cnt_q != '1) begin
          beat_cnt_q <= beat_cnt_q + 1'b1;
        end
        bytes_q <= (bytes_q > 16'hFFF7) ? '1 : bytes_q + 16'd8;  // non-last beats are full
      end
    end
  end

  // ---- header fields (datapath, no reset; each written only on its own beat) ----
  logic [      47:0] dst_mac_q;
  logic [      63:0] sof_ts_q;
  logic              rx_en_q;
  logic [      15:0] ethertype_q;
  logic [       7:0] ver_ihl_q;
  logic [      15:0] ip_len_q;
  logic [      15:0] frag_q;  // flags + fragment offset
  logic [       7:0] proto_q;
  logic [      31:0] src_ip_q;
  logic [      15:0] dst_ip_hi_q;
  logic [CSUM_W-1:0] csum_q;  // header words of beats 1..3

  // Sum of the four 16-bit words of one beat.
  function automatic logic [17:0] sum4(input logic [63:0] d);
    return 18'(be16(d, 0)) + 18'(be16(d, 2)) + 18'(be16(d, 4)) + 18'(be16(d, 6));
  endfunction

  always_ff @(posedge clk) begin
    if (sof) begin
      dst_mac_q <= {be32(s_axis_rx_tdata, 0), be16(s_axis_rx_tdata, 4)};
      sof_ts_q  <= ts_now;
      rx_en_q   <= cfg_rx_en;
    end
    if (at_beat[1]) begin
      ethertype_q <= be16(s_axis_rx_tdata, 4);
      ver_ihl_q   <= s_axis_rx_tdata[8*6+:8];
      csum_q      <= CSUM_W'(be16(s_axis_rx_tdata, 6));  // loaded: no sum carried between frames
    end
    if (at_beat[2]) begin
      ip_len_q <= be16(s_axis_rx_tdata, 0);
      frag_q   <= be16(s_axis_rx_tdata, 4);
      proto_q  <= s_axis_rx_tdata[8*7+:8];
      csum_q   <= csum_q + CSUM_W'(sum4(s_axis_rx_tdata));
    end
    if (at_beat[3]) begin
      src_ip_q    <= be32(s_axis_rx_tdata, 2);
      dst_ip_hi_q <= be16(s_axis_rx_tdata, 6);
      csum_q      <= csum_q + CSUM_W'(sum4(s_axis_rx_tdata));
    end
  end

  // ---- stage A: the cycle beat 4 is accepted; the only cycle that reads cfg_* (except rx_en) ----
  logic                 stage_a;
  logic [         31:0] dst_ip_a;
  logic [         15:0] dst_port_a;
  logic [         15:0] udp_len_a;
  logic [N_FILTERS-1:0] hit_a;
  logic                 mac_ok_a;

  assign stage_a    = at_beat[4] && !s_axis_rx_tlast;  // a frame that ends here never reaches B
  assign dst_ip_a   = {dst_ip_hi_q, be16(s_axis_rx_tdata, 0)};
  assign dst_port_a = be16(s_axis_rx_tdata, 4);
  assign udp_len_a  = be16(s_axis_rx_tdata, 6);

  always_comb begin
    for (int i = 0; i < N_FILTERS; i++) begin
      hit_a[i] = cfg_filter_en[i] && cfg_filter_ip[i] == dst_ip_a && cfg_filter_port[i] == dst_port_a;
    end
  end

  // Own unicast address, broadcast if enabled, or the multicast address of dst IP (RFC 1112 §6.4).
  assign mac_ok_a = dst_mac_q == cfg_local_mac || (cfg_accept_bcast && dst_mac_q == '1) ||
      (dst_mac_q[47:23] == IPV4_MCAST_MAC_PREFIX && dst_ip_a[31:28] == 4'hE &&
       dst_mac_q[22:0] == dst_ip_a[22:0]);

  logic                 stage_b_q;  // control: stage B runs this cycle
  logic [N_FILTERS-1:0] hit_q;
  logic                 mac_ok_q;
  logic [   CSUM_W-1:0] csum_b_q;  // all 10 header words
  logic                 len_ok_q;
  logic [         31:0] dst_ip_q;
  logic [         15:0] dst_port_q;
  logic [         15:0] udp_len_q;

  always_ff @(posedge clk) begin
    if (rst) begin
      stage_b_q <= 1'b0;
    end else begin
      stage_b_q <= stage_a;
    end
  end

  always_ff @(posedge clk) begin
    if (stage_a) begin
      hit_q      <= hit_a;
      mac_ok_q   <= mac_ok_a;
      csum_b_q   <= csum_q + CSUM_W'(be16(s_axis_rx_tdata, 0));
      len_ok_q   <= 17'(udp_len_a) + 17'd20 == 17'(ip_len_q) && udp_len_a > 16'd8;
      dst_ip_q   <= dst_ip_a;
      dst_port_q <= dst_port_a;
      udp_len_q  <= udp_len_a;
    end
  end

  // ---- stage B: header verdict (independent of tvalid) ----
  logic       [      16:0] csum_fold1;
  logic       [      15:0] csum_fold2;
  rx_reason_e              hdr_reason_b;
  logic       [FIDX_W-1:0] filter_idx_b;

  assign csum_fold1 = 17'(csum_b_q[15:0]) + 17'(csum_b_q[CSUM_W-1:16]);
  assign csum_fold2 = csum_fold1[15:0] + 16'(csum_fold1[16]);

  // Priority order of 09 §2.2 (DISABLED, FCS and "headers incomplete" are decided at tlast).
  always_comb begin
    if (!mac_ok_q) begin
      hdr_reason_b = RX_DROP_MAC;
    end else if (ethertype_q != ETHERTYPE_IPV4) begin
      hdr_reason_b = RX_DROP_ETHERTYPE;
    end else if (ver_ihl_q != IPV4_VER_IHL || csum_fold2 != 16'hFFFF) begin
      hdr_reason_b = RX_DROP_IP_HDR;
    end else if ((frag_q & FRAG_MASK) != '0) begin
      hdr_reason_b = RX_DROP_FRAG;
    end else if (proto_q != IP_PROTO_UDP) begin
      hdr_reason_b = RX_DROP_PROTO;
    end else if (hit_q == '0) begin
      hdr_reason_b = RX_DROP_FILTER;
    end else if (!len_ok_q) begin
      hdr_reason_b = RX_DROP_LEN;
    end else begin
      hdr_reason_b = RX_OK;
    end
  end

  // The lowest matching filter entry wins.
  always_comb begin
    filter_idx_b = '0;
    for (int i = N_FILTERS - 1; i >= 0; i--) begin
      if (hit_q[i]) begin
        filter_idx_b = FIDX_W'(i);
      end
    end
  end

  logic       hdr_done_q;  // control: stage B ran for the current frame (K1: cleared at tlast)
  rx_reason_e hdr_reason_q;

  always_ff @(posedge clk) begin
    if (rst || eof) begin
      hdr_done_q <= 1'b0;
    end else if (stage_b_q) begin
      hdr_done_q <= 1'b1;
    end
  end

  // Metadata stays stable from stage B until the next frame's stage B.
  always_ff @(posedge clk) begin
    if (stage_b_q) begin
      hdr_reason_q     <= hdr_reason_b;
      m_udp_sof_ts     <= sof_ts_q;
      m_udp_src_ip     <= src_ip_q;
      m_udp_dst_ip     <= dst_ip_q;
      m_udp_dst_port   <= dst_port_q;
      m_udp_len        <= udp_len_q - 16'd8;
      m_udp_filter_idx <= filter_idx_b;
    end
  end

  // ---- end of frame: final reason and the stat event ----
  logic       [16:0] frame_len_w;  // bytes including this beat
  logic       [15:0] frame_len;  // saturating
  logic       [16:0] ip_end;  // 14 + IPv4 total length: end of the datagram
  rx_reason_e        hdr_reason;  // stage B verdict of this frame
  rx_reason_e        reason;

  assign frame_len_w = 17'(bytes_q) + 17'($countones(s_axis_rx_tkeep));
  assign frame_len   = frame_len_w[16] ? '1 : frame_len_w[15:0];
  assign ip_end      = 17'(ip_len_q) + 17'd14;
  assign hdr_reason  = hdr_done_q ? hdr_reason_q : hdr_reason_b;  // tlast may meet stage B

  always_comb begin
    if (!(sof ? cfg_rx_en : rx_en_q)) begin
      reason = RX_DROP_DISABLED;
    end else if (s_axis_rx_tuser) begin
      reason = RX_DROP_FCS;
    end else if (frame_len < 16'(UDP_PAYLOAD_OFS)) begin
      reason = RX_DROP_LEN;  // headers incomplete: no check on missing bytes
    end else if (hdr_reason != RX_OK) begin
      reason = hdr_reason;
    end else if (frame_len < 16'(ETH_MIN_FRAME) || 17'(frame_len) < ip_end ||
                 (17'(frame_len) > ip_end && frame_len != 16'(ETH_MIN_FRAME))) begin
      reason = RX_DROP_LEN;  // runt, truncated datagram, or bytes after it that are not padding
    end else begin
      reason = RX_OK;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      stat_valid <= 1'b0;
    end else begin
      stat_valid <= eof;
    end
  end

  always_ff @(posedge clk) begin
    if (eof) begin
      stat_reason <= reason;
      stat_bytes  <= frame_len;
    end
  end

  // ---- payload path (09 §2.4): 2-byte realign, trim to udp_len - 8, hold, tail, abort ----
  // The payload starts at byte 42 (beat 5, lane 2): output beat j = {beat 6+j [15:0],
  // beat 5+j [63:16]}, formed when beat 6+j is accepted. Nothing here is cleared at a SOF,
  // so a tail beat may share its cycle with the next frame's beat 0.
  logic        pass_b;  // stage B: headers pass and the frame was enabled at its SOF
  logic        pay_q;  // control: this frame's payload goes out (cleared at tlast)
  logic        pay;  // ... including the stage-B cycle
  logic [47:0] prev_q;  // lanes 2..7 of the previous input beat
  logic [15:0] remain_q;  // payload bytes still to emit (loaded in stage B)
  logic [ 3:0] n_cur;  // bytes in the current input beat
  logic [ 3:0] avail;  // payload bytes the current beat completes: 6 of prev + up to 2 of cur
  logic [ 3:0] emit_n;  // bytes in the formed output beat
  logic        form;  // an output beat is formed this cycle
  logic        done;  // ... and it holds the last payload byte
  logic        tail_set;  // input tlast leaves payload bytes beyond lane 1: tail beat next cycle
  logic        last_now;  // the beat formed at input tlast is the packet's last one
  logic        pend_q;  // control: last beat formed, waiting for input tlast (padding)
  logic        tail_q;  // control: emit the tail beat this cycle
  logic [ 3:0] tail_n_q;  // bytes after lane 1 of the last input beat
  logic [ 3:0] tail_emit;
  logic        out_valid_q;  // control
  logic [63:0] out_data_q;
  logic [ 7:0] out_keep_q;
  logic        out_last_q;
  logic        out_user_q;

  function automatic logic [7:0] keep_of(input logic [3:0] n);  // n low bytes, n <= 8
    return 8'((9'd1 << n) - 9'd1);
  endfunction

  assign pass_b    = rx_en_q && hdr_reason_b == RX_OK;
  assign pay       = pay_q || (stage_b_q && pass_b);
  assign n_cur     = 4'($countones(s_axis_rx_tkeep));
  assign avail     = 4'd6 + ((n_cur > 4'd2) ? 4'd2 : n_cur);
  assign form      = s_axis_rx_tvalid && pay && beat_cnt_q >= BEAT_W'(6) && remain_q != '0;
  assign done      = 16'(avail) >= remain_q;
  assign emit_n    = done ? remain_q[3:0] : avail;
  assign tail_set  = eof && pay && n_cur > 4'd2 && (beat_cnt_q == BEAT_W'(5) || (form && !done));
  assign last_now  = eof && !tail_set;
  assign tail_emit = (16'(tail_n_q) > remain_q) ? remain_q[3:0] : tail_n_q;

  always_ff @(posedge clk) begin
    if (rst || eof) begin
      pay_q <= 1'b0;
    end else if (stage_b_q) begin
      pay_q <= pass_b;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      out_valid_q <= 1'b0;
      pend_q      <= 1'b0;
      tail_q      <= 1'b0;
    end else begin
      out_valid_q <= tail_q || (form && (!done || eof)) || (pend_q && eof);
      tail_q      <= tail_set;
      if (eof) begin
        pend_q <= 1'b0;
      end else if (form && done) begin
        pend_q <= 1'b1;
      end
    end
  end

  always_ff @(posedge clk) begin
    if (s_axis_rx_tvalid && beat_cnt_q >= BEAT_W'(5)) begin
      prev_q <= s_axis_rx_tdata[63:16];
    end
    if (stage_b_q) begin
      remain_q <= udp_len_q - 16'd8;
    end else if (form) begin
      remain_q <= remain_q - 16'(emit_n);
    end
    if (tail_set) begin
      tail_n_q <= n_cur - 4'd2;
    end
    if (tail_q) begin
      out_data_q <= {16'h0, prev_q};
      out_keep_q <= keep_of(tail_emit);
      out_last_q <= 1'b1;
      out_user_q <= stat_reason != RX_OK;  // registered at the tlast beat
    end else if (form) begin
      out_data_q <= {s_axis_rx_tdata[15:0], prev_q};
      out_keep_q <= keep_of(emit_n);
      out_last_q <= done || last_now;
      out_user_q <= last_now && reason != RX_OK;
    end else if (pend_q && eof) begin
      out_user_q <= reason != RX_OK;  // the held beat goes out with this frame's verdict
    end
  end

  assign m_axis_udp_tdata  = out_data_q;
  assign m_axis_udp_tkeep  = out_keep_q;
  assign m_axis_udp_tvalid = out_valid_q;
  assign m_axis_udp_tlast  = out_last_q;
  assign m_axis_udp_tuser  = out_user_q;

endmodule
