// moldudp64_rx_ooc: OOC synthesis harness for moldudp64_rx. Every input goes through one
// register first, as from eth_ip_udp_rx's output registers, so the framer, sequence and
// realigner paths are register to register and inside the timing gate. Every output is a
// port, so synthesis keeps (and the gate checks) all of the logic.
// Not used in simulation.
// Clock domain : clk
module moldudp64_rx_ooc
  import hft_pkg::*;
(
    input  logic                              clk,
    input  logic                              rst,
    input  logic         [              63:0] s_axis_udp_tdata,
    input  logic         [               7:0] s_axis_udp_tkeep,
    input  logic                              s_axis_udp_tvalid,
    input  logic                              s_axis_udp_tlast,
    input  logic         [              15:0] s_udp_len,
    input  logic         [              63:0] s_udp_sof_ts,
    output logic                              m_msg_valid,
    output logic         [8*MOLD_MAX_MSG-1:0] m_msg_data,
    output logic         [MOLD_MSG_LEN_W-1:0] m_msg_len,
    output logic         [              63:0] m_msg_seq,
    output logic         [              63:0] m_msg_sof_ts,
    output logic                              m_pkt_valid,
    output mold_status_e                      m_pkt_status,
    output logic         [              63:0] m_pkt_seq,
    output logic         [              15:0] m_pkt_count,
    output logic         [              15:0] m_pkt_msgs,
    output logic         [              15:0] m_pkt_oversize
);
  logic        rst_q;
  logic [63:0] tdata_q;
  logic [ 7:0] tkeep_q;
  logic        tvalid_q;
  logic        tlast_q;
  logic [15:0] len_q;
  logic [63:0] sof_ts_q;

  always_ff @(posedge clk) begin
    rst_q    <= rst;
    tdata_q  <= s_axis_udp_tdata;
    tkeep_q  <= s_axis_udp_tkeep;
    tvalid_q <= s_axis_udp_tvalid;
    tlast_q  <= s_axis_udp_tlast;
    len_q    <= s_udp_len;
    sof_ts_q <= s_udp_sof_ts;
  end

  moldudp64_rx u_rx (
      .clk              (clk),
      .rst              (rst_q),
      .s_axis_udp_tdata (tdata_q),
      .s_axis_udp_tkeep (tkeep_q),
      .s_axis_udp_tvalid(tvalid_q),
      .s_axis_udp_tlast (tlast_q),
      .s_udp_len        (len_q),
      .s_udp_sof_ts     (sof_ts_q),
      .m_msg_valid      (m_msg_valid),
      .m_msg_data       (m_msg_data),
      .m_msg_len        (m_msg_len),
      .m_msg_seq        (m_msg_seq),
      .m_msg_sof_ts     (m_msg_sof_ts),
      .m_pkt_valid      (m_pkt_valid),
      .m_pkt_status     (m_pkt_status),
      .m_pkt_seq        (m_pkt_seq),
      .m_pkt_count      (m_pkt_count),
      .m_pkt_msgs       (m_pkt_msgs),
      .m_pkt_oversize   (m_pkt_oversize)
  );
endmodule
