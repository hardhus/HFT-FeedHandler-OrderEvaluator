// axis_skid_buffer: AXI-Stream register slice that registers both directions.
// Clock domain : clk
// Latency      : 1 cycle
// Throughput   : 1 beat/cycle
// Note         : s_axis_tready comes straight from a flop (no combinational path from
//                m_axis_tready). A second "skid" register catches the beat accepted in the
//                cycle the output stalls.
module axis_skid_buffer #(
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
    $error("axis_skid_buffer: DATA_W must be a positive multiple of 8 and USER_W >= 1");
  end

  localparam int unsigned W = DATA_W + DATA_W / 8 + 1 + USER_W;

  logic [W-1:0] s_bus, out_q, skid_q;
  logic out_valid_q, skid_valid_q;
  logic out_ready, load_out, load_skid;

  assign s_bus         = {s_axis_tuser, s_axis_tlast, s_axis_tkeep, s_axis_tdata};
  assign s_axis_tready = !skid_valid_q;  // registered: no path from m_axis_tready
  assign out_ready     = m_axis_tready || !out_valid_q;
  assign load_out      = out_ready && (skid_valid_q || s_axis_tvalid);
  assign load_skid     = !out_ready && s_axis_tvalid && !skid_valid_q;
  assign m_axis_tvalid = out_valid_q;

  // Control: synchronous reset.
  always_ff @(posedge clk) begin
    if (rst) begin
      out_valid_q  <= 1'b0;
      skid_valid_q <= 1'b0;
    end else if (out_ready) begin
      out_valid_q  <= skid_valid_q || s_axis_tvalid;
      skid_valid_q <= 1'b0;
    end else if (load_skid) begin
      skid_valid_q <= 1'b1;
    end
  end

  // Datapath: no reset.
  always_ff @(posedge clk) begin
    if (load_out) begin
      out_q <= skid_valid_q ? skid_q : s_bus;
    end
    if (load_skid) begin
      skid_q <= s_bus;
    end
  end

  assign {m_axis_tuser, m_axis_tlast, m_axis_tkeep, m_axis_tdata} = out_q;
endmodule
