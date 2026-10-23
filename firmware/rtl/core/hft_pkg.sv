// hft_pkg: protocol constants, byte-lane helpers and shared types for the core.
package hft_pkg;
  // ---- Ethernet / IPv4 / UDP (wire = big-endian, AXIS byte 0 = tdata[7:0]) ----
  localparam logic [15:0] ETHERTYPE_IPV4 = 16'h0800;
  localparam logic [7:0] IPV4_VER_IHL = 8'h45;  // version 4, IHL 5 (no options)
  localparam logic [7:0] IP_PROTO_UDP = 8'd17;
  localparam int unsigned ETH_MIN_FRAME = 60;  // bytes, without FCS
  localparam int unsigned UDP_PAYLOAD_OFS = 42;  // 14 + 20 + 8
  localparam logic [24:0] IPV4_MCAST_MAC_PREFIX = {24'h01005E, 1'b0};

  // Big-endian field from byte lanes [lane, lane+1(+2, +3)] of one 64-bit beat.
  function automatic logic [15:0] be16(input logic [63:0] d, input int unsigned lane);
    return {d[8*lane+:8], d[8*(lane+1)+:8]};
  endfunction
  function automatic logic [31:0] be32(input logic [63:0] d, input int unsigned lane);
    return {be16(d, lane), be16(d, lane + 2)};
  endfunction

  // Receive outcome = counter index (F5). Order of evaluation: see 09 §2.2.
  typedef enum logic [3:0] {
    RX_OK             = 4'd0,
    RX_DROP_DISABLED  = 4'd1,
    RX_DROP_FCS       = 4'd2,
    RX_DROP_MAC       = 4'd3,
    RX_DROP_ETHERTYPE = 4'd4,
    RX_DROP_IP_HDR    = 4'd5,
    RX_DROP_FRAG      = 4'd6,
    RX_DROP_PROTO     = 4'd7,
    RX_DROP_FILTER    = 4'd8,
    RX_DROP_LEN       = 4'd9
  } rx_reason_e;

  // Receive counters (rx_stats, STATS block of the CSR map): index RX_CNT_PKTS, RX_CNT_BYTES,
  // then one drop counter per reason at index 1 + reason (RX_DROP_LEN is the last reason).
  localparam int unsigned RX_CNT_PKTS = 0;
  localparam int unsigned RX_CNT_BYTES = 1;
  localparam int unsigned RX_N_CNT = 2 + int'(RX_DROP_LEN);

  // ---- MoldUDP64 (moldudp64_rx) ----
  localparam int unsigned MOLD_HDR_LEN = 20;  // session (10), sequence number (8), count (2)
  localparam logic [15:0] MOLD_COUNT_EOS = 16'hFFFF;  // count 0 = heartbeat, 0xFFFF = end of session
  // Shortest supported message: a block of MOLD_MIN_LEN + 2 >= 8 bytes keeps one block
  // boundary per 8-byte beat. ITCH 5.0 messages are 12..50 bytes.
  localparam int unsigned MOLD_MIN_LEN = 6;
  localparam int unsigned MOLD_MAX_MSG = 64;  // message vector width; longer ones are skipped
  localparam int unsigned MOLD_MSG_LEN_W = $clog2(MOLD_MAX_MSG + 1);
  // Packet outcome = counter index (mold_stats). When several apply, the first in this order is
  // reported: DISABLED, OTHER_FEED, ABORT, RUNT, SESSION, UNSUPPORTED, MALFORMED, DUP, then
  // OK/HEARTBEAT/EOS. Only OK, HEARTBEAT and EOS commit the packet's messages.
  typedef enum logic [3:0] {
    MOLD_OK          = 4'd0,
    MOLD_HEARTBEAT   = 4'd1,
    MOLD_EOS         = 4'd2,
    MOLD_DUP         = 4'd3,
    MOLD_DISABLED    = 4'd4,
    MOLD_OTHER_FEED  = 4'd5,
    MOLD_ABORT       = 4'd6,
    MOLD_RUNT        = 4'd7,
    MOLD_SESSION     = 4'd8,
    MOLD_UNSUPPORTED = 4'd9,
    MOLD_MALFORMED   = 4'd10
  } mold_status_e;

  // MoldUDP64 counters (mold_stats, FEED block of the CSR map): index = status (one per packet),
  // then the message and gap counters.
  localparam int unsigned MOLD_CNT_MSGS = 1 + int'(MOLD_MALFORMED);  // messages of accepted packets
  localparam int unsigned MOLD_CNT_DUP_MSGS = MOLD_CNT_MSGS + 1;
  localparam int unsigned MOLD_CNT_OVERSIZE_MSGS = MOLD_CNT_MSGS + 2;
  localparam int unsigned MOLD_CNT_GAP_EVENTS = MOLD_CNT_MSGS + 3;  // packets with a gap
  localparam int unsigned MOLD_CNT_GAP_MSGS = MOLD_CNT_MSGS + 4;  // messages skipped
  localparam int unsigned MOLD_N_CNT = MOLD_CNT_MSGS + 5;

  // ---- ITCH 5.0 (itch_decoder) ----
  // Message class = counter index (itch_stats). ORDER and TRADE messages are decoded into
  // fields; ADMIN, AUCTION and UNKNOWN pass with the common header and the raw vector only.
  // MALFORMED: a known type whose length is not the specification length (not decoded).
  typedef enum logic [2:0] {
    ITCH_ORDER     = 3'd0,  // A F E C X D U
    ITCH_TRADE     = 3'd1,  // P Q B
    ITCH_ADMIN     = 3'd2,  // S R H Y L V W K J h
    ITCH_AUCTION   = 3'd3,  // I N O
    ITCH_UNKNOWN   = 3'd4,  // any other type byte
    ITCH_MALFORMED = 3'd5
  } itch_class_e;
endpackage
