// mold_stats: MoldUDP64 counter bank fed by the moldudp64_rx packet event.
// Clock domain : clk (net_clk)
// Latency      : cnt includes an event 1 cycle after its pkt_valid
// Throughput   : one event per cycle
// Counters     : cnt[status] += 1 for every event; cnt[MOLD_CNT_MSGS] += pkt_msgs for MOLD_OK,
//                MOLD_HEARTBEAT and MOLD_EOS (the messages kept downstream); DUP_MSGS, OVERSIZE_MSGS
//                and GAP_MSGS add their field (moldudp64_rx already zeroes them where they do not
//                apply); GAP_EVENTS += 1 when the gap is not 0. CNT_W bits each, wrapping;
//                cleared only by rst (no software clear: no host/hardware write race, K5).
// Snapshot     : cnt is a register; a reader that samples it on one clock edge (the CDC handshake
//                in hft_csr_wrap) gets every counter of the same cycle, without the event of
//                that cycle.
module mold_stats
  import hft_pkg::*;
#(
    parameter int unsigned CNT_W = 64
) (
    input logic clk,
    input logic rst,

    input logic                pkt_valid,
    input mold_status_e        pkt_status,
    input logic         [63:0] pkt_gap,
    input logic         [15:0] pkt_msgs,
    input logic         [15:0] pkt_dup_msgs,
    input logic         [15:0] pkt_oversize,

    output logic [MOLD_N_CNT-1:0][CNT_W-1:0] cnt
);
  if (CNT_W == 0) begin : g_check
    $error("mold_stats: CNT_W must be >= 1");
  end

  logic accepted;
  assign accepted = pkt_status == MOLD_OK || pkt_status == MOLD_HEARTBEAT || pkt_status == MOLD_EOS;

  always_ff @(posedge clk) begin
    if (rst) begin
      cnt <= '0;
    end else if (pkt_valid) begin
      // One incrementer per status counter, selected by a constant compare (a variable index
      // shares one adder behind a mux: no slack on the A7, see rx_stats).
      for (int s = 0; s <= int'(MOLD_MALFORMED); s++) begin
        if (pkt_status == mold_status_e'(s)) begin
          cnt[s] <= cnt[s] + CNT_W'(1);
        end
      end
      if (accepted) begin
        cnt[MOLD_CNT_MSGS] <= cnt[MOLD_CNT_MSGS] + CNT_W'(pkt_msgs);
      end
      cnt[MOLD_CNT_DUP_MSGS]      <= cnt[MOLD_CNT_DUP_MSGS] + CNT_W'(pkt_dup_msgs);
      cnt[MOLD_CNT_OVERSIZE_MSGS] <= cnt[MOLD_CNT_OVERSIZE_MSGS] + CNT_W'(pkt_oversize);
      cnt[MOLD_CNT_GAP_EVENTS]    <= cnt[MOLD_CNT_GAP_EVENTS] + CNT_W'(pkt_gap != '0);
      cnt[MOLD_CNT_GAP_MSGS]      <= cnt[MOLD_CNT_GAP_MSGS] + CNT_W'(pkt_gap);
    end
  end
endmodule
