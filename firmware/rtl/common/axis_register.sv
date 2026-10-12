// axis_register: forward AXI-Stream pipeline register (tvalid/tdata/tkeep/tlast/tuser registered).
// Clock domain : clk
// Latency      : 1 cycle
// Throughput   : 1 beat/cycle
// Note         : s_axis_tready is combinational from m_axis_tready; use axis_skid_buffer to also
//                break the ready path.
module axis_register #(
    parameter int unsigned DATA_W = 64,
    parameter int unsigned USER_W = 1
) (
    input logic clk,
    input logic rst,

    input  logic [  DATA_W-1:0] s_axis_tdata,
    input  logic [DATA_W/8-1:0] s_axis_tkeep,
    input  logic                s_axis_tlast,
    input  logic [  USER_W-1:0] s_axis_tuser,
    input  logic                s_axis_tvalid,
    output logic                s_axis_tready,

    output logic [  DATA_W-1:0] m_axis_tdata,
    output logic [DATA_W/8-1:0] m_axis_tkeep,
    output logic                m_axis_tlast,
    output logic [  USER_W-1:0] m_axis_tuser,
    output logic                m_axis_tvalid,
    input  logic                m_axis_tready
);
  if (DATA_W == 0 || DATA_W % 8 != 0 || USER_W == 0) begin : g_check
    $error("axis_register: DATA_W must be a positive multiple of 8 and USER_W >= 1");
  end

  localparam int unsigned W = DATA_W + DATA_W / 8 + 1 + USER_W;

  logic [W-1:0] s_bus, data_q;
  logic valid_q;

  assign s_bus         = {s_axis_tuser, s_axis_tlast, s_axis_tkeep, s_axis_tdata};
  assign s_axis_tready = m_axis_tready || !valid_q;
  assign m_axis_tvalid = valid_q;

  // Control: synchronous reset.
  always_ff @(posedge clk) begin
    if (rst) begin
      valid_q <= 1'b0;
    end else if (s_axis_tready) begin
      valid_q <= s_axis_tvalid;
    end
  end

  // Datapath: no reset.
  always_ff @(posedge clk) begin
    if (s_axis_tready && s_axis_tvalid) begin
      data_q <= s_bus;
    end
  end

  assign {m_axis_tuser, m_axis_tlast, m_axis_tkeep, m_axis_tdata} = data_q;
endmodule
