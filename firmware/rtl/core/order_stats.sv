// order_stats: order path counter bank (audit/14 §2.8, decision 30): RULE_FIRED_<n>, ORDERS_SENT_HW/HOST,
// RISK_REJECT_<reason> and RING_DROP_ORDERS.
// Clock domain : clk (net_clk)
// Latency      : an event counts on the edge that samples it (cnt includes it in the next cycle)
// Throughput   : one released order, one ORDER_SENT, one RISK_REJECT and one lost reject per cycle
// Inputs       : the ORDER_SENT and RISK_REJECT inputs must be the very signals event_packer takes
//                (s_sent_*, s_rej_*, s_rej_lost), with no register between: a packer entry counted here
//                then counts on the same edge as event_packer's cnt_ord_queued, and RING_COMMIT_ENTRIES +
//                RING_DROP_ENTRIES + RING_DROP_ORDERS + RING_ORDER_QUEUED = ITCH_OUT + MOLD_GAP_EVENTS +
//                MOLD_EOS + ORDERS_SENT_HW + ORDERS_SENT_HOST + the sum of RISK_REJECT_<reason> holds in every
//                snapshot (decision 30; audit/14 S7 A5: no STATS_DELAY change).
// Counters     : cnt[n] += 1 per strategy order of rule n (an order released by its accepted packet:
//                RULE_FIRED_<n>); cnt[ORD_CNT_SENT_HW / HOST] per ORDER_SENT by source; cnt[ORD_CNT_REJ +
//                reason - 1] per RISK_REJECT on rej_* and per lost reject (lost_reason), so one reason can
//                add 2 in a cycle; cnt[ORD_CNT_DROP] per lost reject. CNT_W bits each, wrapping; cleared only
//                by rst (K5). A source or reason outside the enums counts nowhere.
// Snapshot     : cnt is a register; a reader that samples it on one clock edge gets every counter of the
//                same cycle.
module order_stats
  import hft_pkg::*;
#(
    parameter int unsigned CNT_W = 64
) (
    input logic clk,
    input logic rst,

    // strategy_engine m_ord_*
    input logic                    fire_valid,
    input logic [STRAT_RULE_W-1:0] fire_rule,

    // order_tx_builder m_sent_* (= event_packer s_sent_*)
    input logic       sent_valid,
    input order_src_e sent_source,

    // risk_check m_rej_* and ev_rej_dropped* (= event_packer s_rej_*, s_rej_lost)
    input logic         rej_valid,
    input risk_reason_e rej_reason,
    input logic         lost_valid,
    input risk_reason_e lost_reason,

    output logic [ORD_N_CNT-1:0][CNT_W-1:0] cnt
);
  if (CNT_W < 2) begin : g_check
    $error("order_stats: CNT_W must be >= 2 (a reason can add 2 in a cycle)");
  end

  logic [ORD_N_CNT-1:0][      1:0] inc;  // this cycle's events: 0..2 per counter
  // The counters live in cnt_q, not in the output port (as itch_stats: the carry test writes the register).
  logic [ORD_N_CNT-1:0][CNT_W-1:0] cnt_q;

  assign cnt = cnt_q;

  // Constant compares, one per counter (a variable index shares one adder behind a mux).
  always_comb begin
    inc = '0;
    for (int n = 0; n < STRAT_N_RULES; n++) begin
      inc[n] = 2'(fire_valid && fire_rule == STRAT_RULE_W'(n));
    end
    inc[ORD_CNT_SENT_HW]   = 2'(sent_valid && sent_source == ORDER_SRC_RULE);
    inc[ORD_CNT_SENT_HOST] = 2'(sent_valid && sent_source == ORDER_SRC_HOST);
    for (int r = 0; r < ORD_N_REASONS; r++) begin
      inc[ORD_CNT_REJ+r] = 2'(rej_valid && rej_reason == risk_reason_e'(r + 1)) +
          2'(lost_valid && lost_reason == risk_reason_e'(r + 1));
    end
    inc[ORD_CNT_DROP] = 2'(lost_valid);
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      cnt_q <= '0;
    end else begin
      for (int i = 0; i < ORD_N_CNT; i++) begin
        cnt_q[i] <= cnt_q[i] + CNT_W'(inc[i]);
      end
    end
  end
endmodule
