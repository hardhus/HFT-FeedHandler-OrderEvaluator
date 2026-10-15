// eth_ip_udp_rx_ooc: OOC synthesis harness for eth_ip_udp_rx. Every input goes through one
// register first, as from the MAC's output register and the F5 config registers, so the
// header, filter and checksum paths are register to register and inside the timing gate.
// Every output is a port, so synthesis keeps (and the gate checks) all of the logic.
// Not used in simulation.
// Clock domain : clk
module eth_ip_udp_rx_ooc
  import hft_pkg::*;
#(
    parameter  int unsigned N_FILTERS = 8,
    localparam int unsigned FIDX_W    = (N_FILTERS > 1) ? $clog2(N_FILTERS) : 1
) (
    input  logic                             clk,
    input  logic                             rst,
    input  logic       [         63:0]       s_axis_rx_tdata,
    input  logic       [          7:0]       s_axis_rx_tkeep,
    input  logic                             s_axis_rx_tvalid,
    output logic                             s_axis_rx_tready,
    input  logic                             s_axis_rx_tlast,
    input  logic                             s_axis_rx_tuser,
    input  logic                             cfg_rx_en,
    input  logic       [         47:0]       cfg_local_mac,
    input  logic                             cfg_accept_bcast,
    input  logic       [N_FILTERS-1:0][31:0] cfg_filter_ip,
    input  logic       [N_FILTERS-1:0][15:0] cfg_filter_port,
    input  logic       [N_FILTERS-1:0]       cfg_filter_en,
    input  logic       [         63:0]       ts_now,
    output logic       [         63:0]       m_axis_udp_tdata,
    output logic       [          7:0]       m_axis_udp_tkeep,
    output logic                             m_axis_udp_tvalid,
    output logic                             m_axis_udp_tlast,
    output logic                             m_axis_udp_tuser,
    output logic       [         63:0]       m_udp_sof_ts,
    output logic       [         31:0]       m_udp_src_ip,
    output logic       [         31:0]       m_udp_dst_ip,
    output logic       [         15:0]       m_udp_dst_port,
    output logic       [         15:0]       m_udp_len,
    output logic       [   FIDX_W-1:0]       m_udp_filter_idx,
    output logic                             stat_valid,
    output rx_reason_e                       stat_reason,
    output logic       [         15:0]       stat_bytes
);
  logic                       rst_q;
  logic [         63:0]       tdata_q;
  logic [          7:0]       tkeep_q;
  logic                       tvalid_q;
  logic                       tlast_q;
  logic                       tuser_q;
  logic                       rx_en_q;
  logic [         47:0]       local_mac_q;
  logic                       accept_bcast_q;
  logic [N_FILTERS-1:0][31:0] filter_ip_q;
  logic [N_FILTERS-1:0][15:0] filter_port_q;
  logic [N_FILTERS-1:0]       filter_en_q;
  logic [         63:0]       ts_q;

  always_ff @(posedge clk) begin
    rst_q          <= rst;
    tdata_q        <= s_axis_rx_tdata;
    tkeep_q        <= s_axis_rx_tkeep;
    tvalid_q       <= s_axis_rx_tvalid;
    tlast_q        <= s_axis_rx_tlast;
    tuser_q        <= s_axis_rx_tuser;
    rx_en_q        <= cfg_rx_en;
    local_mac_q    <= cfg_local_mac;
    accept_bcast_q <= cfg_accept_bcast;
    filter_ip_q    <= cfg_filter_ip;
    filter_port_q  <= cfg_filter_port;
    filter_en_q    <= cfg_filter_en;
    ts_q           <= ts_now;
  end

  eth_ip_udp_rx #(
      .N_FILTERS(N_FILTERS)
  ) u_rx (
      .clk              (clk),
      .rst              (rst_q),
      .s_axis_rx_tdata  (tdata_q),
      .s_axis_rx_tkeep  (tkeep_q),
      .s_axis_rx_tvalid (tvalid_q),
      .s_axis_rx_tready (s_axis_rx_tready),
      .s_axis_rx_tlast  (tlast_q),
      .s_axis_rx_tuser  (tuser_q),
      .cfg_rx_en        (rx_en_q),
      .cfg_local_mac    (local_mac_q),
      .cfg_accept_bcast (accept_bcast_q),
      .cfg_filter_ip    (filter_ip_q),
      .cfg_filter_port  (filter_port_q),
      .cfg_filter_en    (filter_en_q),
      .ts_now           (ts_q),
      .m_axis_udp_tdata (m_axis_udp_tdata),
      .m_axis_udp_tkeep (m_axis_udp_tkeep),
      .m_axis_udp_tvalid(m_axis_udp_tvalid),
      .m_axis_udp_tlast (m_axis_udp_tlast),
      .m_axis_udp_tuser (m_axis_udp_tuser),
      .m_udp_sof_ts     (m_udp_sof_ts),
      .m_udp_src_ip     (m_udp_src_ip),
      .m_udp_dst_ip     (m_udp_dst_ip),
      .m_udp_dst_port   (m_udp_dst_port),
      .m_udp_len        (m_udp_len),
      .m_udp_filter_idx (m_udp_filter_idx),
      .stat_valid       (stat_valid),
      .stat_reason      (stat_reason),
      .stat_bytes       (stat_bytes)
  );
endmodule
