// itch_decoder: ITCH 5.0 message classifier and field decoder for the moldudp64_rx output.
// Clock domain : clk (net_clk)
// Latency      : s_msg_valid -> m_ev_valid / m_res_valid 2 cycles; s_pkt_valid -> m_pkt_valid
//                2 cycles, so a packet's event still follows its messages
// Throughput   : one message per cycle, no backpressure; a message and a packet event may come
//                in the same cycle (the message then belongs to the next packet)
// Classes      : ORDER (A F E C X D U) and TRADE (P Q B) are decoded into the normalized m_ev_*
//                fields; ADMIN, AUCTION and UNKNOWN carry the common header and m_ev_raw only;
//                a known type whose length is not the specification length is MALFORMED (not
//                decoded). Fields that do not apply are 0. Values are not validated.
// Header       : stock locate, tracking number, timestamp for messages of >= ITCH_HDR_LEN bytes,
//                in every class; 0 for shorter ones
// Config       : s_msg_class_en comes with each message: moldudp64_rx samples it (cfg_msg_ctx)
//                on the packet's first beat, so one FEED commit applies to whole packets
// Reset        : a message or packet event in the pipeline when rst is high is dropped
module itch_decoder
  import hft_pkg::*;
(
    input logic clk,
    input logic rst,

    // Messages (moldudp64_rx): byte i = s_msg_data[8i+7:8i], bytes from s_msg_len on are 0
    input logic                      s_msg_valid,
    input logic [8*MOLD_MAX_MSG-1:0] s_msg_data,
    input logic [MOLD_MSG_LEN_W-1:0] s_msg_len,
    input logic [              63:0] s_msg_seq,
    input logic [              63:0] s_msg_sof_ts,
    input logic [  ITCH_N_CLASS-1:0] s_msg_class_en, // bit c: class c is output, else MASKED

    // Packet events (moldudp64_rx), passed through with the messages' latency
    input logic                s_pkt_valid,
    input mold_status_e        s_pkt_status,
    input logic         [63:0] s_pkt_seq,
    input logic         [15:0] s_pkt_count,
    input logic         [63:0] s_pkt_gap,
    input logic         [15:0] s_pkt_msgs,
    input logic         [15:0] s_pkt_dup_msgs,
    input logic         [15:0] s_pkt_oversize,
    input logic                s_pkt_synced_now,


    // One event per output message. Alpha fields (side, stock, attribution, printable,
    // cross_type) in wire order: byte 0 of the field in bits [7:0].
    output logic                             m_ev_valid,
    output itch_class_e                      m_ev_class,
    output logic        [               7:0] m_ev_type,
    output logic        [MOLD_MSG_LEN_W-1:0] m_ev_len,
    output logic        [              63:0] m_ev_seq,
    output logic        [              63:0] m_ev_sof_ts,
    output logic        [              15:0] m_ev_locate,
    output logic        [              15:0] m_ev_tracking,
    output logic        [              47:0] m_ev_ts,
    output logic        [              63:0] m_ev_order_ref,    // A F E C X D P; U: original
    output logic        [              63:0] m_ev_new_ref,      // U
    output logic        [              63:0] m_ev_shares,       // A F U P Q; E C X: exec/cancel
    output logic        [              31:0] m_ev_price,        // A F U P; C exec; Q cross
    output logic        [              63:0] m_ev_match,        // E C P Q B
    output logic        [               7:0] m_ev_side,         // A F P
    output logic        [              63:0] m_ev_stock,        // A F P Q
    output logic        [              31:0] m_ev_attribution,  // F
    output logic        [               7:0] m_ev_printable,    // C
    output logic        [               7:0] m_ev_cross_type,   // Q
    output logic        [8*MOLD_MAX_MSG-1:0] m_ev_raw,          // the input vector

    // One result per input message
    output logic          m_res_valid,
    output itch_class_e   m_res_class,
    output itch_outcome_e m_res_outcome,

    // Packet events, after the events of their messages
    output logic                m_pkt_valid,
    output mold_status_e        m_pkt_status,
    output logic         [63:0] m_pkt_seq,
    output logic         [15:0] m_pkt_count,
    output logic         [63:0] m_pkt_gap,
    output logic         [15:0] m_pkt_msgs,
    output logic         [15:0] m_pkt_dup_msgs,
    output logic         [15:0] m_pkt_oversize,
    output logic                m_pkt_synced_now
);
  // ===================== Stage 0 -> 1: class, length check =====================
  logic [7:0] typ;
  itch_class_e type_cls, cls;
  logic [MOLD_MSG_LEN_W-1:0] spec_len;  // 0: not a known type

  assign typ = s_msg_data[7:0];

  // Specification length (type byte included) and class of the known types.
  always_comb begin
    spec_len = '0;
    type_cls = ITCH_UNKNOWN;
    case (typ)
      ITCH_T_SYSTEM_EVENT: begin
        spec_len = 12;
        type_cls = ITCH_ADMIN;
      end
      ITCH_T_STOCK_DIRECTORY: begin
        spec_len = 39;
        type_cls = ITCH_ADMIN;
      end
      ITCH_T_TRADING_ACTION: begin
        spec_len = 25;
        type_cls = ITCH_ADMIN;
      end
      ITCH_T_REG_SHO: begin
        spec_len = 20;
        type_cls = ITCH_ADMIN;
      end
      ITCH_T_MP_POSITION: begin
        spec_len = 26;
        type_cls = ITCH_ADMIN;
      end
      ITCH_T_MWCB_DECLINE: begin
        spec_len = 35;
        type_cls = ITCH_ADMIN;
      end
      ITCH_T_MWCB_STATUS: begin
        spec_len = 12;
        type_cls = ITCH_ADMIN;
      end
      ITCH_T_IPO_QUOTING: begin
        spec_len = 28;
        type_cls = ITCH_ADMIN;
      end
      ITCH_T_LULD_COLLAR: begin
        spec_len = 35;
        type_cls = ITCH_ADMIN;
      end
      ITCH_T_OPERATIONAL_HALT: begin
        spec_len = 21;
        type_cls = ITCH_ADMIN;
      end
      ITCH_T_ADD_ORDER: begin
        spec_len = 36;
        type_cls = ITCH_ORDER;
      end
      ITCH_T_ADD_ORDER_MPID: begin
        spec_len = 40;
        type_cls = ITCH_ORDER;
      end
      ITCH_T_ORDER_EXECUTED: begin
        spec_len = 31;
        type_cls = ITCH_ORDER;
      end
      ITCH_T_ORDER_EXECUTED_PRICE: begin
        spec_len = 36;
        type_cls = ITCH_ORDER;
      end
      ITCH_T_ORDER_CANCEL: begin
        spec_len = 23;
        type_cls = ITCH_ORDER;
      end
      ITCH_T_ORDER_DELETE: begin
        spec_len = 19;
        type_cls = ITCH_ORDER;
      end
      ITCH_T_ORDER_REPLACE: begin
        spec_len = 35;
        type_cls = ITCH_ORDER;
      end
      ITCH_T_TRADE: begin
        spec_len = 44;
        type_cls = ITCH_TRADE;
      end
      ITCH_T_CROSS_TRADE: begin
        spec_len = 40;
        type_cls = ITCH_TRADE;
      end
      ITCH_T_BROKEN_TRADE: begin
        spec_len = 19;
        type_cls = ITCH_TRADE;
      end
      ITCH_T_NOII: begin
        spec_len = 50;
        type_cls = ITCH_AUCTION;
      end
      ITCH_T_RPII: begin
        spec_len = 20;
        type_cls = ITCH_AUCTION;
      end
      ITCH_T_DLCR: begin
        spec_len = 48;
        type_cls = ITCH_AUCTION;
      end
      default: ;
    endcase
  end
  assign cls = (type_cls != ITCH_UNKNOWN && s_msg_len != spec_len) ? ITCH_MALFORMED : type_cls;

  logic v1_q, en1_q, hdr1_q;
  itch_class_e                      cls1_q;
  logic        [               7:0] type1_q;
  logic        [MOLD_MSG_LEN_W-1:0] len1_q;
  logic        [8*MOLD_MAX_MSG-1:0] vec1_q;
  logic [63:0] seq1_q, sof_ts1_q;

  always_ff @(posedge clk) begin
    v1_q      <= rst ? 1'b0 : s_msg_valid;
    en1_q     <= s_msg_class_en[cls];
    hdr1_q    <= s_msg_len >= MOLD_MSG_LEN_W'(ITCH_HDR_LEN);
    cls1_q    <= cls;
    type1_q   <= typ;
    len1_q    <= s_msg_len;
    vec1_q    <= s_msg_data;
    seq1_q    <= s_msg_seq;
    sof_ts1_q <= s_msg_sof_ts;
  end

  // ===================== Stage 1 -> 2: pass decision, fields =====================
  logic decoded;
  logic [63:0] order_ref, new_ref, shares, match, stock;
  logic [31:0] price, attribution;
  logic [7:0] side, printable, cross_type;

  assign decoded = cls1_q == ITCH_ORDER || cls1_q == ITCH_TRADE;

  // Offsets from the TotalView-ITCH 5.0 message tables (type byte at 0).
  always_comb begin
    order_ref   = '0;
    new_ref     = '0;
    shares      = '0;
    match       = '0;
    stock       = '0;
    price       = '0;
    attribution = '0;
    side        = '0;
    printable   = '0;
    cross_type  = '0;
    if (decoded) begin
      case (type1_q)
        ITCH_T_ADD_ORDER, ITCH_T_ADD_ORDER_MPID: begin
          order_ref = msg_be(vec1_q, 11, 8);
          side      = vec1_q[8*19+:8];
          shares    = msg_be(vec1_q, 20, 4);
          stock     = vec1_q[8*24+:64];
          price     = 32'(msg_be(vec1_q, 32, 4));
          if (type1_q == ITCH_T_ADD_ORDER_MPID) begin
            attribution = vec1_q[8*36+:32];
          end
        end
        ITCH_T_ORDER_EXECUTED, ITCH_T_ORDER_EXECUTED_PRICE: begin
          order_ref = msg_be(vec1_q, 11, 8);
          shares    = msg_be(vec1_q, 19, 4);
          match     = msg_be(vec1_q, 23, 8);
          if (type1_q == ITCH_T_ORDER_EXECUTED_PRICE) begin
            printable = vec1_q[8*31+:8];
            price     = 32'(msg_be(vec1_q, 32, 4));
          end
        end
        ITCH_T_ORDER_CANCEL: begin
          order_ref = msg_be(vec1_q, 11, 8);
          shares    = msg_be(vec1_q, 19, 4);
        end
        ITCH_T_ORDER_DELETE: begin
          order_ref = msg_be(vec1_q, 11, 8);
        end
        ITCH_T_ORDER_REPLACE: begin
          order_ref = msg_be(vec1_q, 11, 8);
          new_ref   = msg_be(vec1_q, 19, 8);
          shares    = msg_be(vec1_q, 27, 4);
          price     = 32'(msg_be(vec1_q, 31, 4));
        end
        ITCH_T_TRADE: begin
          order_ref = msg_be(vec1_q, 11, 8);
          side      = vec1_q[8*19+:8];
          shares    = msg_be(vec1_q, 20, 4);
          stock     = vec1_q[8*24+:64];
          price     = 32'(msg_be(vec1_q, 32, 4));
          match     = msg_be(vec1_q, 36, 8);
        end
        ITCH_T_CROSS_TRADE: begin
          shares     = msg_be(vec1_q, 11, 8);
          stock      = vec1_q[8*19+:64];
          price      = 32'(msg_be(vec1_q, 27, 4));
          match      = msg_be(vec1_q, 31, 8);
          cross_type = vec1_q[8*39+:8];
        end
        ITCH_T_BROKEN_TRADE: begin
          match = msg_be(vec1_q, 11, 8);
        end
        default: ;
      endcase
    end
  end

  always_ff @(posedge clk) begin
    m_ev_valid       <= rst ? 1'b0 : v1_q && en1_q;
    m_res_valid      <= rst ? 1'b0 : v1_q;
    m_res_class      <= cls1_q;
    m_res_outcome    <= en1_q ? ITCH_RES_OUT : ITCH_RES_MASKED;
    m_ev_class       <= cls1_q;
    m_ev_type        <= type1_q;
    m_ev_len         <= len1_q;
    m_ev_seq         <= seq1_q;
    m_ev_sof_ts      <= sof_ts1_q;
    m_ev_locate      <= hdr1_q ? 16'(msg_be(vec1_q, 1, 2)) : '0;
    m_ev_tracking    <= hdr1_q ? 16'(msg_be(vec1_q, 3, 2)) : '0;
    m_ev_ts          <= hdr1_q ? 48'(msg_be(vec1_q, 5, 6)) : '0;
    m_ev_order_ref   <= order_ref;
    m_ev_new_ref     <= new_ref;
    m_ev_shares      <= shares;
    m_ev_price       <= price;
    m_ev_match       <= match;
    m_ev_side        <= side;
    m_ev_stock       <= stock;
    m_ev_attribution <= attribution;
    m_ev_printable   <= printable;
    m_ev_cross_type  <= cross_type;
    m_ev_raw         <= vec1_q;
  end

  // ===================== Packet events: the same two stages =====================
  logic         p1_valid_q;
  mold_status_e p1_status_q;
  logic [63:0] p1_seq_q, p1_gap_q;
  logic [15:0] p1_count_q, p1_msgs_q, p1_dup_q, p1_over_q;
  logic p1_synced_now_q;

  always_ff @(posedge clk) begin
    p1_valid_q       <= rst ? 1'b0 : s_pkt_valid;
    p1_status_q      <= s_pkt_status;
    p1_seq_q         <= s_pkt_seq;
    p1_count_q       <= s_pkt_count;
    p1_gap_q         <= s_pkt_gap;
    p1_msgs_q        <= s_pkt_msgs;
    p1_dup_q         <= s_pkt_dup_msgs;
    p1_over_q        <= s_pkt_oversize;
    p1_synced_now_q  <= s_pkt_synced_now;
    m_pkt_valid      <= rst ? 1'b0 : p1_valid_q;
    m_pkt_status     <= p1_status_q;
    m_pkt_seq        <= p1_seq_q;
    m_pkt_count      <= p1_count_q;
    m_pkt_gap        <= p1_gap_q;
    m_pkt_msgs       <= p1_msgs_q;
    m_pkt_dup_msgs   <= p1_dup_q;
    m_pkt_oversize   <= p1_over_q;
    m_pkt_synced_now <= p1_synced_now_q;
  end
endmodule
