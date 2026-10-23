// moldudp64_rx: MoldUDP64 de-framer: splits each UDP payload into messages, one vector each,
// and tracks the feed's sequence numbers (gap, duplicate, heartbeat, end of session, resync).
// Clock domain : clk (net_clk)
// Latency      : input beat holding a message's last byte -> m_msg_valid 2 cycles;
//                input tlast -> m_pkt_valid 3 cycles, after every message of the packet
// Throughput   : line rate, IPG 0, no backpressure. Blocks of >= MOLD_MIN_LEN + 2 bytes keep one
//                block boundary per beat; a shorter one makes the packet UNSUPPORTED
// Input        : the eth_ip_udp_rx payload stream: byte 0 = tdata[7:0], tkeep full before tlast
//                and contiguous on it, tuser on the tlast beat only, s_udp_* held from the first
//                beat through tlast. cfg_* except cfg_resync* are sampled on the first beat
// Context      : cfg_msg_ctx is opaque here (the ITCH decoder's per-packet config): sampled on
//                the first beat with the other cfg_* and output with every message of the packet,
//                so one FEED commit applies to a whole packet, framing and decoding alike
// Reset        : rst between packets or with the source; a packet cut by it gets no event, and
//                the feed state (expected seq, synced, eos) goes back to 0
module moldudp64_rx
  import hft_pkg::*;
#(
    parameter  int unsigned N_FILTERS = 8,
    parameter  int unsigned CTX_W     = 1,
    localparam int unsigned FIDX_W    = (N_FILTERS > 1) ? $clog2(N_FILTERS) : 1
) (
    input logic clk,
    input logic rst,

    // UDP payload (from eth_ip_udp_rx)
    input logic [      63:0] s_axis_udp_tdata,
    input logic [       7:0] s_axis_udp_tkeep,
    input logic              s_axis_udp_tvalid,
    input logic              s_axis_udp_tlast,
    input logic              s_axis_udp_tuser,   // on tlast: abort (FCS error, truncated)
    input logic [      15:0] s_udp_len,          // payload bytes
    input logic [FIDX_W-1:0] s_udp_filter_idx,
    input logic [      63:0] s_udp_sof_ts,

    // Feed configuration (hft_csr_wrap)
    input logic                 cfg_feed_en,
    input logic                 cfg_session_check,
    input logic [         79:0] cfg_session,        // byte 0 = [79:72], as on the wire
    input logic [N_FILTERS-1:0] cfg_filter_mask,    // bit i: filter entry i belongs to this feed
    input logic                 cfg_resync,         // pulse
    input logic                 cfg_resync_learn,   // 1: learn the next packet's seq
    input logic [         63:0] cfg_resync_seq,     // otherwise: expected seq = this
    input logic [    CTX_W-1:0] cfg_msg_ctx,        // passed to m_msg_ctx, per packet

    // One message per cycle: byte i = m_msg_data[8i+7:8i], bytes from m_msg_len on are 0
    output logic                      m_msg_valid,
    output logic [8*MOLD_MAX_MSG-1:0] m_msg_data,
    output logic [MOLD_MSG_LEN_W-1:0] m_msg_len,
    output logic [              63:0] m_msg_seq,
    output logic [              63:0] m_msg_sof_ts,
    output logic [         CTX_W-1:0] m_msg_ctx,     // cfg_msg_ctx at the packet's first beat

    // One event per input packet, after its messages. Downstream keeps the packet's messages
    // only for MOLD_OK, MOLD_HEARTBEAT and MOLD_EOS.
    output logic                m_pkt_valid,
    output mold_status_e        m_pkt_status,
    output logic         [63:0] m_pkt_seq,        // header fields (undefined for RUNT)
    output logic         [15:0] m_pkt_count,
    output logic         [63:0] m_pkt_gap,        // messages skipped; accepted in sync only
    output logic         [15:0] m_pkt_msgs,       // messages output for this packet
    output logic         [15:0] m_pkt_dup_msgs,   // seq below expected; 0 unless accepted or DUP
    output logic         [15:0] m_pkt_oversize,   // > MOLD_MAX_MSG; 0 unless accepted or DUP
    output logic                m_pkt_synced_now, // this packet synchronized the feed

    // Feed state (for the CSR snapshot)
    output logic [63:0] feed_expected_seq,
    output logic        feed_synced,
    output logic        feed_eos
);
  // Header layout (MoldUDP64): beat 0 = session[0..7]; beat 1 = session[8..9], seq[0..5];
  // beat 2 = seq[6..7], count, then the first block's length field.
  localparam int unsigned HDR_BEAT = MOLD_HDR_LEN / 8;  // beat of the first length field
  localparam int unsigned LP_W = 17;  // offsets up to 7 + 2 + 65535
  localparam int unsigned W_W = LP_W - 3;  // offset / 8
  // Offset of the first length field's low byte from lane 0 of beat HDR_BEAT.
  localparam logic [LP_W-1:0] LP_FIRST = LP_W'(MOLD_HDR_LEN + 1 - 8 * HDR_BEAT);

  if (HDR_BEAT != 2 || LP_FIRST >= 8 || MOLD_MIN_LEN + 2 < 8 || MOLD_MAX_MSG % 8 != 0)
  begin : g_check
    $error("moldudp64_rx: header beats, one boundary per beat or vector width do not hold");
  end

  // Byte r of the result = byte (r + s) mod 8 of d.
  function automatic logic [63:0] rotate(input logic [63:0] d, input logic [2:0] s);
    return 64'({d, d} >> (8 * s));
  endfunction

  // ===================== Stage 0: framer (on the input beat) =====================
  logic [7:0][7:0] lane;  // lane[i] = byte i of the beat
  logic [7:0]      vld;  // lane holds a payload byte
  logic beat, last;
  logic [1:0] bcnt_q;  // beat index in the packet, saturating at 3
  logic hdr1, hdr2, body;

  assign lane = s_axis_udp_tdata;
  assign beat = s_axis_udp_tvalid;
  assign last = beat && s_axis_udp_tlast;
  assign vld  = s_axis_udp_tlast ? s_axis_udp_tkeep : 8'hFF;
  assign hdr1 = beat && bcnt_q == 2'd1;
  assign hdr2 = beat && bcnt_q == 2'(HDR_BEAT);
  assign body = beat && bcnt_q >= 2'(HDR_BEAT);

  // ---- header (datapath) ----
  logic [47:0] seq_hi_q;  // seq[0..5] from beat 1
  logic [63:0] seq_q, seq_now, pkt_seq;
  logic [15:0] cnt_q, cnt_now, pkt_cnt;

  assign seq_now = {seq_hi_q, lane[0], lane[1]};
  assign cnt_now = {lane[2], lane[3]};
  assign pkt_seq = hdr2 ? seq_now : seq_q;
  assign pkt_cnt = hdr2 ? cnt_now : cnt_q;

  always_ff @(posedge clk) begin
    if (hdr1) begin
      seq_hi_q <= {lane[2], lane[3], lane[4], lane[5], lane[6], lane[7]};
    end
    if (hdr2) begin
      seq_q <= seq_now;
      cnt_q <= cnt_now;
    end
  end

  // ---- packet gates: config sampled on the first beat; the session is known after beat 1 ----
  logic first;
  logic dis_q, oth_q, chk_q, sess0_ok_q, sess_bad_q;  // disabled, other feed, session check
  logic [     15:0] sess_tail_q;  // cfg_session bytes 8..9
  logic [CTX_W-1:0] ctx_q;  // cfg_msg_ctx of the packet (see m_msg_ctx)
  logic sess0_ok, sess_bad, cur_dis, cur_oth, cur_sess, skip;

  assign first = beat && bcnt_q == '0;
  always_comb begin
    sess0_ok = 1'b1;
    for (int b = 0; b < 8; b++) begin
      if (lane[b] != cfg_session[79-8*b-:8]) begin
        sess0_ok = 1'b0;
      end
    end
  end
  assign sess_bad = chk_q && !(sess0_ok_q && {lane[0], lane[1]} == sess_tail_q);
  // the packet's flags on its tlast beat (a 1-beat packet has only the sampled values)
  assign cur_dis  = first ? !cfg_feed_en : dis_q;
  assign cur_oth  = first ? !cfg_filter_mask[s_udp_filter_idx] : oth_q;
  assign cur_sess = hdr1 ? sess_bad : sess_bad_q;
  assign skip     = dis_q || oth_q || sess_bad_q;  // no framing: no message comes out

  always_ff @(posedge clk) begin
    if (first) begin
      dis_q       <= !cfg_feed_en;
      oth_q       <= !cfg_filter_mask[s_udp_filter_idx];
      chk_q       <= cfg_session_check;
      sess0_ok_q  <= sess0_ok;
      sess_tail_q <= cfg_session[15:0];
      ctx_q       <= cfg_msg_ctx;
    end
    if (hdr1) begin
      sess_bad_q <= sess_bad;
    end
  end

  // ---- sequence numbers against the expected one (E), from the header beat ----
  // D = S - E as a signed 64-bit number (serial arithmetic). Blocks with index < -D are
  // duplicates; dup_n = min(-D, 2^16) (count <= 0xFFFE), one cycle after the header beat, in
  // time for the first block's end (beat 3 at the earliest). In the same cycle: is the whole
  // normal packet below E (S + C <= E, i.e. -D >= C)?
  logic [63:0] e_q;  // expected seq
  logic synced_q, eos_q;
  logic [63:0] d_q, nd_q;  // S - E, E - S
  logic        hdr2_d;
  logic [16:0] dup_n_q;
  logic        old_nb_q;

  always_ff @(posedge clk) begin
    hdr2_d <= hdr2;
    if (hdr2) begin
      d_q  <= seq_now - e_q;
      nd_q <= e_q - seq_now;
    end
    if (hdr2_d) begin
      dup_n_q  <= !d_q[63] ? '0 : (nd_q[63:16] != '0) ? 17'h10000 : nd_q[16:0];
      old_nb_q <= d_q[63] && (nd_q[63:16] != '0 || nd_q[16:0] >= {1'b0, cnt_q});
    end
  end

  // ---- block framing ----
  // The next length field's low byte is at offset 8 * w + o from lane 0 of this beat. The block
  // before it (the current block) ends at that offset - 2; a length field at offset 0 has its
  // high byte in lane 7 of the previous beat (hi_q). Since every block has >= 8 bytes, a beat
  // holds at most one length field, one message start and one message end.
  // Timing: only o and the flags w == 0 / w == 1 are in the one-cycle loop through the length
  // decode. After a block the beat count w is loaded one framing beat later, from th_q.
  logic [2:0] o_q, o, o_next;
  logic wz_q, wz, wz_next;  // w == 0: the length field is in this beat
  logic w1_q, w1, w1_next;  // w == 1
  logic ld_q, ld;  // w = th_q - 1 (a block was decoded in the previous framing beat)
  logic [W_W-1:0] th_q, wc_q, wc_next;  // offset after that block / 8; w when !ld
  logic [W_W-1:0] th;  // (offset after the block decoded in this beat) / 8
  logic [15:0] blk_left_q, blk, blk_after;  // blocks whose length field is still to come
  logic [7:0] hi_q;  // lane 7 of the previous beat
  logic unsup_q;  // a block shorter than MOLD_MIN_LEN: framing stopped
  logic blk_act_q;  // the current block's data started in an earlier beat
  logic pend_q;  // the current block's data starts in lane 0 of this beat
  logic [2:0] cb_s_q;  // current block: start lane, vector length (0 if oversize), seq
  logic [MOLD_MSG_LEN_W-1:0] cb_len_q;
  logic cb_over_q;
  logic [63:0] cb_seq_q;
  logic [15:0] cb_idx_q;  // index in the packet of the current block
  logic [15:0] bi_q, dec_idx;  // index in the packet of the next block
  logic [63:0] dec_seq;  // its seq: S + index, from registers (not through the decode)

  logic        live;  // framing beat
  logic [15:0] blen;  // length field decoded in this beat
  logic blk_nz, blk_one;  // blk != 0, blk == 1
  logic dec, unsup_now, newblk, nb_over, nb_end;
  logic [MOLD_MSG_LEN_W-1:0] nb_len;
  logic [               2:0] cur_e;
  logic in_win, cur_end, end_ev, end_new, end_over, emit, start;
  logic [2:0] s_new;
  logic exact, runt, unsup_any;

  // Every length-dependent decision is made for each lane i as if the length field's low byte
  // were there (o == i), and then selected by o: the compares run in parallel with o, and the
  // loop through o_q is a select of single bits.
  logic [7:0][15:0] l_len;
  logic [7:0][ 5:0] l_t;  // i + length[4:0] + 2: offset after the block if length[15:5] == 0
  logic [7:0][ 3:0] l_e;  // i + length[2:0]: lane of the block's last byte if length < 8
  logic [7:0] l_short, l_over, l_end, l_wz, l_w1, l_exact;

  always_comb begin
    for (int i = 0; i < 8; i++) begin
      l_len[i]   = {(i == 0) ? hi_q : lane[(i+7)%8], lane[i]};
      l_t[i]     = 6'(i) + 6'(l_len[i][4:0]) + 6'd2;
      l_e[i]     = 4'(i) + 4'(l_len[i][2:0]);
      l_short[i] = l_len[i] < 16'(MOLD_MIN_LEN);
      l_over[i]  = l_len[i] > 16'(MOLD_MAX_MSG);
      // the block ends in this beat (only from i <= 1 with a 6..7-byte block)
      l_end[i]   = l_len[i] < 16'd8 && l_e[i] < 4'd8 && vld[l_e[i][2:0]];
      l_wz[i]    = l_len[i][15:5] == '0 && l_t[i] < 6'd16;
      l_w1[i]    = l_len[i][15:5] == '0 && l_t[i] >= 6'd16 && l_t[i] < 6'd24;
      // ... on the last byte
      l_exact[i] = l_end[i] && (l_e[i] == 4'd7 || !vld[l_e[i][2:0]+3'd1]);
    end
  end

  assign live = body && !unsup_q && !skip;
  assign o = hdr2 ? LP_FIRST[2:0] : o_q;
  assign wz = hdr2 || wz_q;
  assign w1 = !hdr2 && w1_q;
  assign ld = !hdr2 && ld_q;
  assign blk = hdr2 ? ((cnt_now == '0 || cnt_now == MOLD_COUNT_EOS) ? '0 : cnt_now) : blk_left_q;
  assign blk_nz = hdr2 ? cnt_now != '0 && cnt_now != MOLD_COUNT_EOS : blk_left_q != '0;
  assign blk_one = blk_left_q == 16'd1;  // used once a block ends in its beat: never in hdr2
  assign blen = l_len[o];

  assign dec = live && blk_nz && wz && vld[o];
  assign unsup_now = dec && l_short[o];
  assign newblk = dec && !unsup_now;
  assign nb_over = l_over[o];
  assign nb_len = nb_over ? '0 : blen[MOLD_MSG_LEN_W-1:0];
  assign dec_seq = hdr2 ? seq_now : seq_q + 64'(bi_q);
  assign dec_idx = hdr2 ? '0 : bi_q;

  assign nb_end = newblk && l_end[o];
  // The current block ends in this beat (offset 2..9) at lane o - 2.
  assign in_win = (wz && o >= 3'd2) || (w1 && o <= 3'd1);
  assign cur_e = o - 3'd2;
  assign cur_end = live && (blk_act_q || pend_q) && in_win && vld[cur_e];

  assign start = live && (pend_q || (newblk && o != 3'd7));
  assign s_new = pend_q ? 3'd0 : o + 3'd1;
  assign end_ev = cur_end || nb_end;
  assign end_new = nb_end || (cur_end && pend_q);  // the ending message starts in this beat
  assign end_over = nb_end ? nb_over : cb_over_q;
  assign emit = end_ev && !end_over;

  // Next offset. A decoded length moves it by length + 2 - 8; without one, by -8 while w > 0,
  // and to 0 when no length field is left. After an UNSUPPORTED length framing stops, so dec
  // (not newblk) is enough here.
  assign th = W_W'((LP_W'(o) + LP_W'(blen) + LP_W'(2)) >> 3);
  always_comb begin
    o_next  = o;
    wz_next = 1'b0;
    w1_next = 1'b0;
    wc_next = ld ? th_q - W_W'(2) : wc_q - W_W'(1);
    if (wz && dec) begin
      o_next  = l_t[o][2:0];
      wz_next = l_wz[o];
      w1_next = l_w1[o];
    end else if (wz) begin
      o_next  = '0;
      wz_next = 1'b1;
    end else begin
      wz_next = ld ? th_q == W_W'(2) : wc_q == W_W'(1);
      w1_next = ld ? th_q == W_W'(3) : wc_q == W_W'(2);
    end
  end

  assign blk_after = dec ? blk - 16'd1 : blk;
  // At tlast: the last block ends exactly on the last byte (offset after it - 2, which must be
  // 2..9). A new block can do so only when it ends in this beat. Bytes after the last block in an
  // earlier beat need no flag: the offset is then 0 or 1, outside 2..9.
  assign exact = body && (newblk ? blk_one && l_exact[o] :
      !blk_nz && in_win && vld[cur_e] && (cur_e == 3'd7 || !vld[cur_e+3'd1]));

  assign runt = s_udp_len < 16'(MOLD_HDR_LEN);
  assign unsup_any = unsup_q || unsup_now;

  always_ff @(posedge clk) begin
    if (rst) begin
      bcnt_q    <= '0;
      unsup_q   <= 1'b0;
      blk_act_q <= 1'b0;
      pend_q    <= 1'b0;
    end else if (beat) begin
      if (last) begin
        bcnt_q    <= '0;
        unsup_q   <= 1'b0;
        blk_act_q <= 1'b0;
        pend_q    <= 1'b0;
      end else begin
        if (bcnt_q != 2'd3) begin
          bcnt_q <= bcnt_q + 2'd1;
        end
        unsup_q   <= unsup_any;
        blk_act_q <= newblk ? (o != 3'd7 && !nb_end) : ((blk_act_q || pend_q) && !cur_end);
        pend_q    <= newblk && o == 3'd7;
      end
    end
  end

  always_ff @(posedge clk) begin
    if (beat) begin
      hi_q <= lane[7];
    end
    if (live) begin
      o_q        <= o_next;
      wz_q       <= wz_next;
      w1_q       <= w1_next;
      ld_q       <= wz && dec;
      wc_q       <= wc_next;
      blk_left_q <= blk_after;
    end
    if (wz && dec) begin
      th_q <= th;
    end
    // dec, not newblk: after an UNSUPPORTED length nothing in this packet reads these
    if (dec) begin
      cb_s_q    <= s_new;  // 0 when the data starts in the next beat (pend_q)
      cb_len_q  <= nb_len;
      cb_over_q <= nb_over;
      cb_seq_q  <= dec_seq;
      cb_idx_q  <= dec_idx;
    end
    if (hdr2 || dec) begin
      bi_q <= dec ? dec_idx + 16'd1 : dec_idx;
    end
  end

  // ===================== Stage 1 (registered framer decisions) =====================
  logic s1_old, s1_start, s1_end, s1_end_new, s1_emit, s1_over, s1_pkt;
  logic s1_dis, s1_oth, s1_abort, s1_runt, s1_sess, s1_unsup, s1_exact;
  logic [               2:0] s1_s;
  logic [MOLD_MSG_LEN_W-1:0] s1_len;
  logic [63:0] s1_seq, s1_ts, s1_rot_old, s1_rot_new;
  logic [15:0] s1_idx;
  logic [63:0] s1_pkt_seq;
  logic [15:0] s1_pkt_cnt;

  always_ff @(posedge clk) begin
    if (rst) begin
      s1_old   <= 1'b0;
      s1_start <= 1'b0;
      s1_end   <= 1'b0;
      s1_pkt   <= 1'b0;
    end else begin
      s1_old   <= live && blk_act_q;  // bytes of a message started in an earlier beat
      s1_start <= start;
      s1_end   <= end_ev;
      s1_pkt   <= last;
    end
  end

  always_ff @(posedge clk) begin
    s1_end_new <= end_new;
    s1_emit    <= emit;
    s1_over    <= end_over;
    s1_s       <= s_new;
    s1_len     <= pend_q ? cb_len_q : nb_len;
    s1_seq     <= pend_q ? cb_seq_q : dec_seq;
    s1_idx     <= pend_q ? cb_idx_q : dec_idx;
    s1_ts      <= s_udp_sof_ts;
    s1_rot_old <= rotate(s_axis_udp_tdata, cb_s_q);
    s1_rot_new <= rotate(s_axis_udp_tdata, s_new);
    s1_dis     <= cur_dis;
    s1_oth     <= cur_oth;
    s1_abort   <= s_axis_udp_tuser;
    s1_runt    <= runt;
    s1_sess    <= cur_sess;
    s1_unsup   <= unsup_any;
    s1_exact   <= exact;
    s1_pkt_seq <= pkt_seq;
    s1_pkt_cnt <= pkt_cnt;
  end

  // ---- ending message: duplicate? (seq below E, in sync: index < dup_n) ----
  logic [15:0] cur_idx_q, end_idx;  // stage 2 register of the message in the accumulator
  logic dup;

  assign end_idx = s1_end_new ? s1_idx : cur_idx_q;
  assign dup     = synced_q && {1'b0, end_idx} < dup_n_q;

  // ---- packet status and counts ----
  // On s1_pkt the packet's d_q, dup_n_q and old_nb_q are ready: the header beat is at least
  // one cycle before tlast for a packet with blocks (two before its stage 1), and a heartbeat
  // or end of session only needs d_q.
  logic [15:0] msgs_q, over_q, dups_q;  // per packet: output, oversize, duplicate messages
  logic [15:0] msgs, over, dups;
  logic hbeos, old;
  mold_status_e status;
  logic accepted, checked;

  assign hbeos = s1_pkt_cnt == '0 || s1_pkt_cnt == MOLD_COUNT_EOS;
  // the whole packet is below E: a heartbeat or end of session with S < E, or S + C <= E
  assign old   = synced_q && (hbeos ? d_q[63] : old_nb_q);
  always_comb begin
    if (s1_dis) begin
      status = MOLD_DISABLED;
    end else if (s1_oth) begin
      status = MOLD_OTHER_FEED;
    end else if (s1_abort) begin
      status = MOLD_ABORT;
    end else if (s1_runt) begin
      status = MOLD_RUNT;
    end else if (s1_sess) begin
      status = MOLD_SESSION;
    end else if (s1_unsup) begin
      status = MOLD_UNSUPPORTED;
    end else if (!s1_exact) begin
      status = MOLD_MALFORMED;
    end else if (old) begin
      status = MOLD_DUP;
    end else if (s1_pkt_cnt == '0) begin
      status = MOLD_HEARTBEAT;
    end else if (s1_pkt_cnt == MOLD_COUNT_EOS) begin
      status = MOLD_EOS;
    end else begin
      status = MOLD_OK;
    end
  end
  assign accepted = status == MOLD_OK || status == MOLD_HEARTBEAT || status == MOLD_EOS;
  assign checked  = accepted || status == MOLD_DUP;  // framing checked: dup/oversize counts valid
  // a duplicate is not counted as oversize (it was counted when first seen)
  assign msgs     = msgs_q + 16'(s1_end && s1_emit && !dup);
  assign over     = over_q + 16'(s1_end && s1_over && !dup);
  assign dups     = dups_q + 16'(s1_end && dup);

  always_ff @(posedge clk) begin
    if (rst || s1_pkt) begin
      msgs_q <= '0;
      over_q <= '0;
      dups_q <= '0;
    end else begin
      msgs_q <= msgs;
      over_q <= over;
      dups_q <= dups;
    end
  end

  // ---- feed state: updated on the accounting cycle of an accepted packet (s1_pkt) ----
  // E = S + C after a normal packet, S after a heartbeat or end of session: both are S + the
  // number of blocks decoded (bi_q; 0 without blocks).
  // Resync, like a double-buffered control register: a pulse after a packet's first beat and up
  // to its s1_pkt waits and is applied after that packet's update; a pulse between packets or
  // on a first beat is applied at once (before that packet, whose config is sampled there).
  // A later pulse replaces a waiting one (both belong to the same packet); a pulse in the cycle
  // where a waiting request is applied (s1_pkt) acts after it.
  logic flight_q;  // a packet is past its first beat and not yet accounted (through s1_pkt)
  logic rs_pend_q, rs_learn_q;  // waiting request
  logic [63:0] rs_seq_q;
  logic rs_now, rs_apply;

  assign rs_now   = s1_pkt || !flight_q;  // no packet holds a request back
  assign rs_apply = (rs_pend_q || cfg_resync) && rs_now;

  always_ff @(posedge clk) begin
    if (rst) begin
      flight_q  <= 1'b0;
      rs_pend_q <= 1'b0;
      e_q       <= '0;
      synced_q  <= 1'b0;
      eos_q     <= 1'b0;
    end else begin
      if (first) begin
        flight_q <= 1'b1;
      end else if (s1_pkt) begin
        flight_q <= 1'b0;
      end
      if (s1_pkt && accepted) begin
        e_q      <= seq_q + 64'(bi_q);
        synced_q <= 1'b1;
        if (status == MOLD_EOS) begin
          eos_q <= 1'b1;
        end else if (status == MOLD_OK) begin
          eos_q <= 1'b0;
        end
      end
      if (rs_apply) begin  // after the packet's update: learn keeps E, set replaces it
        if (rs_pend_q && !rs_learn_q) begin
          e_q <= rs_seq_q;
        end
        if (cfg_resync && !cfg_resync_learn) begin
          e_q <= cfg_resync_seq;
        end
        synced_q <= cfg_resync ? !cfg_resync_learn : !rs_learn_q;
        eos_q    <= 1'b0;
      end
      if (cfg_resync && !rs_now) begin
        rs_pend_q <= 1'b1;
      end else if (rs_apply) begin
        rs_pend_q <= 1'b0;
      end
    end
  end

  always_ff @(posedge clk) begin
    if (cfg_resync) begin
      rs_learn_q <= cfg_resync_learn;
      rs_seq_q   <= cfg_resync_seq;
    end
  end

  assign feed_expected_seq = e_q;
  assign feed_synced       = synced_q;
  assign feed_eos          = eos_q;

  // ===================== Stage 2: realigner and outputs =====================
  // A message that starts in lane s is rotated by s; rotated byte r goes to byte r of the
  // current word (r < 8 - s) or of the previous one (r >= 8 - s). Bytes at or past the
  // message length are never written, and the accumulator is cleared when a message starts.
  localparam int unsigned N_WORDS = MOLD_MAX_MSG / 8;
  logic [               2:0] cur_s_q;
  logic [MOLD_MSG_LEN_W-1:0] cur_len_q;
  logic [              63:0] cur_seq_q;
  logic [               3:0] cur_w_q;  // word of this beat's bytes r < 8 - s, saturating
  logic [N_WORDS-1:0][7:0][7:0] acc_q, merged, fresh;

  always_comb begin
    for (int w = 0; w < N_WORDS; w++) begin
      for (int r = 0; r < 8; r++) begin
        // continuing message
        merged[w][r] = acc_q[w][r];
        if (s1_old && 32'(cur_len_q) > 8 * w + r &&
            ((r + 32'(cur_s_q) < 8) ? 32'(cur_w_q) == w : 32'(cur_w_q) == w + 1)) begin
          merged[w][r] = s1_rot_old[8*r+:8];
        end
        // message starting in this beat: word 0
        fresh[w][r] = '0;
        if (w == 0 && r + 32'(s1_s) < 8 && 32'(s1_len) > r) begin
          fresh[w][r] = s1_rot_new[8*r+:8];
        end
      end
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      m_msg_valid <= 1'b0;
    end else begin
      m_msg_valid <= s1_end && s1_emit && !dup;
    end
  end

  always_ff @(posedge clk) begin
    acc_q <= s1_start ? fresh : merged;
    if (s1_start) begin
      cur_s_q   <= s1_s;
      cur_len_q <= s1_len;
      cur_seq_q <= s1_seq;
      cur_idx_q <= s1_idx;
      cur_w_q   <= 4'd1;
    end else if (s1_old && cur_w_q != 4'hF) begin
      cur_w_q <= cur_w_q + 4'd1;
    end
    if (s1_end) begin
      m_msg_data   <= s1_end_new ? fresh : merged;
      m_msg_len    <= s1_end_new ? s1_len : cur_len_q;
      m_msg_seq    <= s1_end_new ? s1_seq : cur_seq_q;
      m_msg_sof_ts <= s1_ts;
      // ctx_q needs no pipeline: no message ends on a first beat (header), and the next
      // packet's first beat updates ctx_q in the edge that registers this packet's last
      // message at the latest, so it still holds this packet's value (audit/12 S2).
      m_msg_ctx    <= ctx_q;
    end
  end

  // ---- packet event: one more register, so it follows the packet's last message ----
  // The gap and the dup/oversize counts are zeroed there, from flags made on s1_pkt.
  logic         p2_valid;
  mold_status_e p2_status;
  logic [63:0] p2_seq, p2_gap;
  logic [15:0] p2_cnt, p2_msgs, p2_dups, p2_over;
  logic p2_gap_ok, p2_checked, p2_synced_now;

  always_ff @(posedge clk) begin
    if (rst) begin
      p2_valid    <= 1'b0;
      m_pkt_valid <= 1'b0;
    end else begin
      p2_valid    <= s1_pkt;
      m_pkt_valid <= p2_valid;
    end
  end

  always_ff @(posedge clk) begin
    p2_status        <= status;
    p2_seq           <= s1_pkt_seq;
    p2_cnt           <= s1_pkt_cnt;
    p2_gap           <= d_q;
    p2_gap_ok        <= accepted && synced_q && !d_q[63];
    p2_msgs          <= msgs;
    p2_dups          <= dups;
    p2_over          <= over;
    p2_checked       <= checked;
    p2_synced_now    <= accepted && !synced_q;
    m_pkt_status     <= p2_status;
    m_pkt_seq        <= p2_seq;
    m_pkt_count      <= p2_cnt;
    m_pkt_gap        <= p2_gap_ok ? p2_gap : '0;
    m_pkt_msgs       <= p2_msgs;
    m_pkt_dup_msgs   <= p2_checked ? p2_dups : '0;
    m_pkt_oversize   <= p2_checked ? p2_over : '0;
    m_pkt_synced_now <= p2_synced_now;
  end
endmodule
