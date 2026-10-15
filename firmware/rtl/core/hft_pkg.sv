// hft_pkg: protocol constants, byte-lane helpers and shared types for the core.
// F2 S0 holds only what the eth_ip_udp_rx port list needs; the constants and helpers
// of 09 §2.9 arrive with their first user in S1/S2.
package hft_pkg;
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
endpackage
