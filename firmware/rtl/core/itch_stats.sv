// itch_stats: ITCH counter bank for committed messages, fed by the itch_decoder results and its
// forwarded packet event.
// Clock domain : clk (net_clk)
// Latency      : cnt includes a packet's results 1 cycle after its pkt_valid
// Throughput   : one result and one packet event per cycle
// Counters     : cnt[class] += 1 and cnt[ITCH_CNT_OUT + outcome] += 1 per committed result. Results
//                are held in pending counts until their packet event: for MOLD_OK, MOLD_HEARTBEAT
//                and MOLD_EOS (the statuses whose messages are kept downstream, as MOLD_MSGS) they
//                are added, for every other status dropped. A result in the same cycle as a packet
//                event belongs to the next packet (itch_decoder forwards the event after its
//                messages). CNT_W bits each, wrapping; cleared only by rst (K5).
// Pending      : 16 bits per counter, as pkt_msgs. Contract: fewer than 2**16 results per packet
//                (moldudp64_rx: at most 181 in a 1472-byte payload); itch_stats_props checks it.
//                rst clears the pending counts with the counters.
// Snapshot     : cnt is a register; a reader that samples it on one clock edge gets every
//                counter of the same cycle, without the packet event of that cycle.
module itch_stats
  import hft_pkg::*;
#(
    parameter int unsigned CNT_W = 64
) (
    input logic clk,
    input logic rst,

    input logic          res_valid,
    input itch_class_e   res_class,
    input itch_outcome_e res_outcome,

    input logic         pkt_valid,
    input mold_status_e pkt_status,

    output logic [ITCH_N_CNT-1:0][CNT_W-1:0] cnt
);
  if (CNT_W == 0) begin : g_check
    $error("itch_stats: CNT_W must be >= 1");
  end

  localparam int unsigned PEND_W = 16;

  logic                              accepted;
  logic [ITCH_N_CNT-1:0]             inc;  // this cycle's result: one bit per counter
  logic [ITCH_N_CNT-1:0][PEND_W-1:0] pend;
  // The counters live in cnt_q, not in the output port: Verilator keeps a top-level port apart
  // from the register, and the CNT_W = 64 carry test writes the register directly (audit/12 S6).
  logic [ITCH_N_CNT-1:0][ CNT_W-1:0] cnt_q;

  assign cnt = cnt_q;

  assign accepted = pkt_status == MOLD_OK || pkt_status == MOLD_HEARTBEAT || pkt_status == MOLD_EOS;

  // Constant compares, one per counter (a variable index shares one adder behind a mux).
  always_comb begin
    inc = '0;
    for (int c = 0; c < ITCH_N_CLASS; c++) begin
      inc[c] = res_valid && res_class == itch_class_e'(c);
    end
    inc[ITCH_CNT_OUT]      = res_valid && res_outcome == ITCH_RES_OUT;
    inc[ITCH_CNT_FILTERED] = res_valid && res_outcome == ITCH_RES_FILTERED;
    inc[ITCH_CNT_MASKED]   = res_valid && res_outcome == ITCH_RES_MASKED;
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      cnt_q <= '0;
      pend  <= '0;
    end else begin
      for (int i = 0; i < ITCH_N_CNT; i++) begin
        if (pkt_valid) begin
          // The packet's results are complete; this cycle's result starts the next packet.
          if (accepted) begin
            cnt_q[i] <= cnt_q[i] + CNT_W'(pend[i]);
          end
          pend[i] <= PEND_W'(inc[i]);
        end else begin
          pend[i] <= pend[i] + PEND_W'(inc[i]);
        end
      end
    end
  end
endmodule
