// axis_props: AXI-Stream source-side protocol checks (bind to a module's m_axis_* outputs).
// Simulation only. Disabled until the first reset has been seen, because flops start with
// random values (--x-initial unique).
module axis_props #(
    parameter int unsigned DATA_W = 64,
    parameter int unsigned USER_W = 1
) (
    input logic                clk,
    input logic                rst,
    input logic                tvalid,
    input logic                tready,
    input logic                tlast,
    input logic [  DATA_W-1:0] tdata,
    input logic [DATA_W/8-1:0] tkeep,
    input logic [  USER_W-1:0] tuser
);
  localparam int unsigned W = DATA_W + DATA_W / 8 + 1 + USER_W;

  logic [W-1:0] payload;
  logic         armed;  // sim-only: initial + always (not always_ff) on purpose

  assign payload = {tdata, tkeep, tlast, tuser};
  initial begin
    armed = 1'b0;
  end
  always @(posedge clk) begin
    if (rst) begin
      armed <= 1'b1;
    end
  end

  // Once valid, a beat stays valid and unchanged until it is accepted.
  a_stall_stable :
  assert property (@(posedge clk) disable iff (rst || !armed)
      tvalid && !tready |=> tvalid && $stable(
      payload
  ))
  else begin
    $error("axis_props: payload changed or tvalid dropped while stalled");
  end

  // The last beat of a packet carries at least one byte.
  a_last_keep :
  assert property (@(posedge clk) disable iff (rst || !armed) tvalid && tlast |-> tkeep != '0)
  else begin
    $error("axis_props: tlast beat with empty tkeep");
  end
endmodule
