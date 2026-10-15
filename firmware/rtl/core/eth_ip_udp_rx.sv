// eth_ip_udp_rx: Ethernet/IPv4/UDP receive filter; streams the UDP payload realigned to byte 0.
// Clock domain : clk (net_clk)
// Latency      : F2 S0 skeleton: accepts every beat, emits nothing (decision path in S2,
//                payload path in S3; see audit/09 §2.3 for the target latencies)
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

  assign s_axis_rx_tready  = 1'b1;

  // S0 skeleton: no decision or payload path yet; every output is idle.
  assign m_axis_udp_tdata  = '0;
  assign m_axis_udp_tkeep  = '0;
  assign m_axis_udp_tvalid = 1'b0;
  assign m_axis_udp_tlast  = 1'b0;
  assign m_axis_udp_tuser  = 1'b0;
  assign m_udp_sof_ts      = '0;
  assign m_udp_src_ip      = '0;
  assign m_udp_dst_ip      = '0;
  assign m_udp_dst_port    = '0;
  assign m_udp_len         = '0;
  assign m_udp_filter_idx  = '0;
  assign stat_valid        = 1'b0;
  assign stat_reason       = RX_OK;
  assign stat_bytes        = '0;

  // Inputs the skeleton does not read yet (removed in S2).
  logic unused_s0;
  assign unused_s0 = ^{
      clk,
      rst,
      s_axis_rx_tdata,
      s_axis_rx_tkeep,
      s_axis_rx_tvalid,
      s_axis_rx_tlast,
      s_axis_rx_tuser,
      cfg_rx_en,
      cfg_local_mac,
      cfg_accept_bcast,
      cfg_filter_ip,
      cfg_filter_port,
      cfg_filter_en,
      ts_now
  };

endmodule
