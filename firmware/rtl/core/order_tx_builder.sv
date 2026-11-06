// order_tx_builder: one order from risk_check to one 92-byte frame on the TX AXI-Stream: the host's Ethernet/
// IPv4/UDP template, the SoupBinTCP 'U' envelope and the OUCH 5.0 Enter Order (docs/protocol/order_msg.md;
// audit/14 §2.6; decisions 12, 26).
// Clock domain : clk (net_clk)
// Latency      : an order taken at edge k (s_ord_valid && s_ord_ready) has its first beat on m_axis_tx_* after
//                edge k; 12 beats, the last with tkeep 0x0F and tlast. m_sent_* (the ORDER_SENT entry) is valid
//                after the edge that takes the first beat (tvalid && tready), with that cycle's ts_now as tx_ts
// Throughput   : back to back: s_ord_ready is 1 when idle or in the cycle the last beat is taken
// Frame        : bytes 0..41 the template, 42..43 Packet Length 48, 44 'U', 45 'O', 46..49 UserRefNum, 50 side,
//                51..54 quantity, 55..62 stock, 63..70 price (Price(4) zero-extended to 8 bytes), 71 tif, 72
//                display, 73 capacity, 74 ise, 75 cross type, 76..89 ClOrdID, 90..91 Appendage Length 0; all
//                big-endian; byte 0 = tdata[7:0] of the first beat. A hardware order's ClOrdID: "HW", the rule
//                digit, the UserRefNum as 8 upper-case hex digits, 3 spaces.
// Config       : cfg_wr is checked (decision 26): display Y/N/A, capacity A/P/R/O, ise N, cross type N; the
//                template's EtherType 0x0800, 0x45, total length 78, flags/fragment 0x4000, protocol 17, IPv4
//                header checksum, UDP length 58, UDP checksum 0. The check takes two cycles (A24): a write at
//                edge k (cfg_wr and the cfg_* of that cycle) is decided at edge k+2. A good one is then in force,
//                for the frames taken after edge k+2 (one taken at edge k+2 or before uses the previous one); a
//                bad one is refused (ev_cfg_refused after edge k+2) and the previous stays. Writes on
//                consecutive cycles are each checked and decided in order. st_cfg_valid: a good config has been
//                decided since reset (the effective tx_en needs it).
// Reset        : idle, no config (st_cfg_valid 0); a write still being checked is dropped. A frame cut by rst is
//                not finished.
module order_tx_builder
  import hft_pkg::*;
(
    input logic        clk,
    input logic        rst,
    input logic [63:0] ts_now,

    // Configuration (net_clk; from hft_csr_wrap). Template byte i = cfg_template[8i+7:8i].
    input  logic            cfg_wr,
    input  logic [8*42-1:0] cfg_template,
    input  logic [     7:0] cfg_display,
    input  logic [     7:0] cfg_capacity,
    input  logic [     7:0] cfg_ise,
    input  logic [     7:0] cfg_cross_type,
    output logic            st_cfg_valid,
    output logic            ev_cfg_refused,

    // Orders (risk_check m_tx_*)
    input  logic                          s_ord_valid,
    output logic                          s_ord_ready,
    input  logic       [            31:0] s_ord_user_ref,
    input  order_src_e                    s_ord_source,
    input  logic       [STRAT_RULE_W-1:0] s_ord_rule,
    input  logic       [             7:0] s_ord_side,
    input  logic       [            31:0] s_ord_qty,
    input  logic       [            31:0] s_ord_price,
    input  logic       [            15:0] s_ord_locate,
    input  logic       [            63:0] s_ord_stock,
    input  logic       [             7:0] s_ord_tif,
    input  logic       [           111:0] s_ord_clordid,
    input  logic       [            63:0] s_ord_seq,
    input  logic       [            63:0] s_ord_sof_ts,
    input  logic       [ CFG_EPOCH_W-1:0] s_ord_epoch,

    // TX AXI-Stream to the MAC
    output logic [63:0] m_axis_tx_tdata,
    output logic [ 7:0] m_axis_tx_tkeep,
    output logic        m_axis_tx_tlast,
    output logic        m_axis_tx_tvalid,
    input  logic        m_axis_tx_tready,

    // ORDER_SENT (to event_packer; its ring credit was reserved by risk_check)
    output logic                          m_sent_valid,
    output logic       [            31:0] m_sent_user_ref,
    output order_src_e                    m_sent_source,
    output logic       [STRAT_RULE_W-1:0] m_sent_rule,
    output logic       [             7:0] m_sent_side,
    output logic       [            31:0] m_sent_qty,
    output logic       [            31:0] m_sent_price,
    output logic       [            15:0] m_sent_locate,
    output logic       [            63:0] m_sent_stock,
    output logic       [             7:0] m_sent_tif,
    output logic       [           111:0] m_sent_clordid,
    output logic       [            63:0] m_sent_seq,
    output logic       [            63:0] m_sent_sof_ts,
    output logic       [ CFG_EPOCH_W-1:0] m_sent_epoch,
    output logic       [            63:0] m_sent_tx_ts
);
  localparam int unsigned BEATS = 12;  // 92 bytes in 8-byte beats
  localparam int unsigned FB = 8 * BEATS;  // frame buffer bytes

  // ===================== Configuration =====================
  function automatic logic [7:0] tbyte(input logic [8*42-1:0] t, input int unsigned i);
    return t[8*i+:8];
  endfunction

  function automatic logic [17:0] hword(input logic [8*42-1:0] t, input int unsigned w);
    return 18'({tbyte(t, 14 + 2 * w), tbyte(t, 15 + 2 * w)});  // IPv4 header word w
  endfunction

  // The check takes two cycles (A24): stage 1 the field checks and three partial sums of the ten IPv4 header
  // words, stage 2 the checksum. Ones' complement: the folded sum is FFFF iff the 20-bit sum S (<= 10 * FFFF)
  // is a nonzero multiple of FFFF.
  logic wr1_q, fok1_q, wr2_q, ok2_q;
  logic [17:0] ps1_q[3];
  logic [8*42-1:0] t1_q, t2_q;
  logic [7:0] display1_q, capacity1_q, ise1_q, cross1_q, display2_q, capacity2_q, ise2_q, cross2_q;
  logic [19:0] csum;
  logic        csum_ok;
  always_comb begin
    csum    = 20'(ps1_q[0]) + 20'(ps1_q[1]) + 20'(ps1_q[2]);
    csum_ok = 1'b0;
    for (int unsigned m = 1; m <= 10; m++) begin
      csum_ok = csum_ok || csum == 20'(m * 16'hFFFF);
    end
  end

  always_ff @(posedge clk) begin
    t1_q <= cfg_template;
    display1_q <= cfg_display;
    capacity1_q <= cfg_capacity;
    ise1_q <= cfg_ise;
    cross1_q <= cfg_cross_type;
    fok1_q       <= cfg_display inside {8'h59, 8'h4E, 8'h41} && cfg_capacity inside {8'h41, 8'h50, 8'h52, 8'h4F} &&
        cfg_ise == 8'h4E && cfg_cross_type == 8'h4E &&
        {tbyte(
        cfg_template, 12
    ), tbyte(
        cfg_template, 13
    )} == 16'h0800 && tbyte(
        cfg_template, 14
    ) == 8'h45 && {tbyte(
        cfg_template, 16
    ), tbyte(
        cfg_template, 17
    )} == 16'd78 && {tbyte(
        cfg_template, 20
    ), tbyte(
        cfg_template, 21
    )} == 16'h4000 && tbyte(
        cfg_template, 23
    ) == 8'd17 && {tbyte(
        cfg_template, 38
    ), tbyte(
        cfg_template, 39
    )} == 16'd58 && {tbyte(
        cfg_template, 40
    ), tbyte(
        cfg_template, 41
    )} == 16'h0000;
    ps1_q[0] <= hword(
        cfg_template, 0
    ) + hword(
        cfg_template, 1
    ) + hword(
        cfg_template, 2
    ) + hword(
        cfg_template, 3
    );
    ps1_q[1] <= hword(cfg_template, 4) + hword(cfg_template, 5) + hword(cfg_template, 6);
    ps1_q[2] <= hword(cfg_template, 7) + hword(cfg_template, 8) + hword(cfg_template, 9);
    t2_q <= t1_q;
    display2_q <= display1_q;
    capacity2_q <= capacity1_q;
    ise2_q <= ise1_q;
    cross2_q <= cross1_q;
    ok2_q <= fok1_q && csum_ok;
    if (rst) begin
      wr1_q <= 1'b0;
      wr2_q <= 1'b0;
    end else begin
      wr1_q <= cfg_wr;
      wr2_q <= wr1_q;
    end
  end

  logic [8*42-1:0] tmpl_q;
  logic [7:0] display_q, capacity_q, ise_q, cross_q;
  always_ff @(posedge clk) begin
    if (wr2_q && ok2_q) begin
      tmpl_q     <= t2_q;
      display_q  <= display2_q;
      capacity_q <= capacity2_q;
      ise_q      <= ise2_q;
      cross_q    <= cross2_q;
    end
    if (rst) begin
      st_cfg_valid   <= 1'b0;
      ev_cfg_refused <= 1'b0;
    end else begin
      st_cfg_valid   <= st_cfg_valid || (wr2_q && ok2_q);
      ev_cfg_refused <= wr2_q && !ok2_q;
    end
  end

  // ===================== The frame of the order taken =====================
  function automatic logic [7:0] hex_digit(input logic [3:0] n);
    return n < 4'd10 ? 8'h30 + 8'(n) : 8'h41 + 8'(n - 4'd10);
  endfunction

  logic [111:0] clordid;  // byte i = [8i+7:8i]
  always_comb begin
    if (s_ord_source == ORDER_SRC_HOST) begin
      clordid = s_ord_clordid;
    end else begin
      clordid        = {14{8'h20}};
      clordid[7:0]   = 8'h48;  // "H"
      clordid[15:8]  = 8'h57;  // "W"
      clordid[23:16] = 8'h30 + 8'(s_ord_rule);
      for (int unsigned i = 0; i < 8; i++) begin
        clordid[8*(3+i)+:8] = hex_digit(s_ord_user_ref[4*(7-i)+:4]);
      end
    end
  end

  logic [8*FB-1:0] frame;
  always_comb begin
    frame           = '0;
    frame[8*42-1:0] = tmpl_q;
    frame[8*42+:8]  = 8'h00;
    frame[8*43+:8]  = 8'h30;  // Packet Length 48
    frame[8*44+:8]  = 8'h55;  // "U"
    frame[8*45+:8]  = 8'h4F;  // "O"
    for (int unsigned i = 0; i < 4; i++) begin
      frame[8*(46+i)+:8] = s_ord_user_ref[8*(3-i)+:8];
      frame[8*(51+i)+:8] = s_ord_qty[8*(3-i)+:8];
      frame[8*(67+i)+:8] = s_ord_price[8*(3-i)+:8];  // the low 4 of the 8 price bytes
    end
    frame[8*50+:8]   = s_ord_side;
    frame[8*55+:64]  = s_ord_stock;  // wire order
    frame[8*71+:8]   = s_ord_tif;
    frame[8*72+:8]   = display_q;
    frame[8*73+:8]   = capacity_q;
    frame[8*74+:8]   = ise_q;
    frame[8*75+:8]   = cross_q;
    frame[8*76+:112] = clordid;
  end

  // ===================== Beats =====================
  logic [8*FB-1:0] frame_q;
  logic [     3:0] beat_q;
  logic busy_q, take, first, last;

  assign first            = m_axis_tx_tvalid && m_axis_tx_tready && beat_q == '0;
  assign last             = m_axis_tx_tvalid && m_axis_tx_tready && beat_q == 4'(BEATS - 1);
  assign s_ord_ready      = (!busy_q || last) && !rst;
  assign take             = s_ord_valid && s_ord_ready;

  assign m_axis_tx_tvalid = busy_q;
  assign m_axis_tx_tdata  = frame_q[64*beat_q+:64];
  assign m_axis_tx_tlast  = beat_q == 4'(BEATS - 1);
  assign m_axis_tx_tkeep  = m_axis_tx_tlast ? 8'h0F : 8'hFF;

  // The order of the frame on the wire, for its ORDER_SENT.
  logic [31:0] o_user_ref_q, o_qty_q, o_price_q;
  order_src_e                    o_source_q;
  logic       [STRAT_RULE_W-1:0] o_rule_q;
  logic [7:0] o_side_q, o_tif_q;
  logic [15:0] o_locate_q;
  logic [63:0] o_stock_q, o_seq_q, o_sof_ts_q;
  logic [          111:0] o_clordid_q;
  logic [CFG_EPOCH_W-1:0] o_epoch_q;

  always_ff @(posedge clk) begin
    if (take) begin
      frame_q      <= frame;
      o_user_ref_q <= s_ord_user_ref;
      o_source_q   <= s_ord_source;
      o_rule_q     <= s_ord_rule;
      o_side_q     <= s_ord_side;
      o_qty_q      <= s_ord_qty;
      o_price_q    <= s_ord_price;
      o_locate_q   <= s_ord_locate;
      o_stock_q    <= s_ord_stock;
      o_tif_q      <= s_ord_tif;
      o_clordid_q  <= clordid;
      o_seq_q      <= s_ord_seq;
      o_sof_ts_q   <= s_ord_sof_ts;
      o_epoch_q    <= s_ord_epoch;
    end
    if (first) begin
      m_sent_user_ref <= o_user_ref_q;
      m_sent_source   <= o_source_q;
      m_sent_rule     <= o_rule_q;
      m_sent_side     <= o_side_q;
      m_sent_qty      <= o_qty_q;
      m_sent_price    <= o_price_q;
      m_sent_locate   <= o_locate_q;
      m_sent_stock    <= o_stock_q;
      m_sent_tif      <= o_tif_q;
      m_sent_clordid  <= o_clordid_q;
      m_sent_seq      <= o_seq_q;
      m_sent_sof_ts   <= o_sof_ts_q;
      m_sent_epoch    <= o_epoch_q;
      m_sent_tx_ts    <= ts_now;
    end
    if (rst) begin
      busy_q       <= 1'b0;
      beat_q       <= '0;
      m_sent_valid <= 1'b0;
    end else begin
      m_sent_valid <= first;
      if (take) begin
        busy_q <= 1'b1;
        beat_q <= '0;
      end else if (last) begin
        busy_q <= 1'b0;
        beat_q <= '0;
      end else if (m_axis_tx_tvalid && m_axis_tx_tready) begin
        beat_q <= beat_q + 4'd1;
      end
    end
  end
endmodule
