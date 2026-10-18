// rx_stats: receive counter bank fed by the eth_ip_udp_rx stat_* event.
// Clock domain : clk (net_clk)
// Latency      : cnt includes an event 1 cycle after its stat_valid
// Throughput   : one event per cycle
// Counters     : cnt[RX_CNT_PKTS] += 1 and cnt[RX_CNT_BYTES] += stat_bytes for every event;
//                cnt[1 + reason] += 1 for every drop (reason != RX_OK). CNT_W bits each, wrapping;
//                cleared only by rst (no software clear: no host/hardware write race, K5).
// Snapshot     : cnt is a register; a reader that samples it on one clock edge (the CDC handshake
//                in hft_csr_wrap) gets every counter of the same cycle, without the event of
//                that cycle.
module rx_stats
  import hft_pkg::*;
#(
    parameter int unsigned CNT_W = 64
) (
    input logic clk,
    input logic rst,

    input logic              stat_valid,
    input rx_reason_e        stat_reason,
    input logic       [15:0] stat_bytes,

    output logic [RX_N_CNT-1:0][CNT_W-1:0] cnt
);
  if (CNT_W == 0) begin : g_check
    $error("rx_stats: CNT_W must be >= 1");
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      cnt <= '0;
    end else if (stat_valid) begin
      cnt[RX_CNT_PKTS]  <= cnt[RX_CNT_PKTS] + CNT_W'(1);
      cnt[RX_CNT_BYTES] <= cnt[RX_CNT_BYTES] + CNT_W'(stat_bytes);
      if (stat_reason != RX_OK) begin
        cnt[1+int'(stat_reason)] <= cnt[1+int'(stat_reason)] + CNT_W'(1);
      end
    end
  end
endmodule
