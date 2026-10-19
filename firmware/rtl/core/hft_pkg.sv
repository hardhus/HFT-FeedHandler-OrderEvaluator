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
endpackage
