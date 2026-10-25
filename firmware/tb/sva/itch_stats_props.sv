// itch_stats_props: itch_stats input contract (audit/12 S6); bind to the itch_stats instance.
// Simulation only. Disabled until the first reset has been seen, because flops start with
// random values (--x-initial unique). The pending counts are 16 bits, as pkt_msgs: a packet has
// fewer than 2**16 messages (moldudp64_rx: at most 181 in a 1472-byte payload). A result that
// would take a pending count past 0xFFFF is a contract violation, not a defined wrap.
module itch_stats_props
  import hft_pkg::*;
#(
    parameter int unsigned PEND_W = 16
) (
    input logic                              clk,
    input logic                              rst,
    input logic                              pkt_valid,
    input logic [ITCH_N_CNT-1:0]             inc,        // this cycle's result, per counter
    input logic [ITCH_N_CNT-1:0][PEND_W-1:0] pend
);
  logic                  armed;  // sim-only: initial + always (not always_ff) on purpose
  logic [ITCH_N_CNT-1:0] full;

  initial begin
    armed = 1'b0;
  end
  always @(posedge clk) begin
    if (rst) begin
      armed <= 1'b1;
    end
  end

  always_comb begin
    for (int i = 0; i < ITCH_N_CNT; i++) begin
      full[i] = pend[i] == '1;
    end
  end

  // A result on a full pending count is legal only with a packet event (it starts the next packet).
  a_pend_limit :
  assert property (@(posedge clk) disable iff (rst || !armed) |(inc & full) |-> pkt_valid)
  else begin
    $error("itch_stats_props: pending count past 16 bits (a packet of more than 65535 results)");
  end
endmodule
