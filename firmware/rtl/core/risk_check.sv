// risk_check: the single pre-trade gate for hardware and host orders, the TX queue and the UserRefNum counter
// (audit/14 §2.5, P6, P7; decisions 5, 7, 9, 10, 12, 13, 15, 20, 21, 22, 24, 25). Timing: the decision reads
// flags registered one cycle ahead (S5); the behaviour is the same, cycle for cycle.
// Clock domain : clk (net_clk)
// Latency      : an order taken at clock edge k (s_hw_valid, or s_host_valid && s_host_ready) is decided in the
//                cycle before edge k+3, with that cycle's ctl_kill and ctl_tx_en; after edge k+3 its RISK_REJECT
//                is on m_rej_*, or it is on m_tx_* (when nothing is ahead of it)
// Throughput   : one order per cycle; hardware orders are never held (no ready), host orders take free cycles
// Arbiter      : a hardware order goes first; s_host_ready = no hardware order, no cancel to write out, not reset
// Checks       : in this order, the first that holds is the reason (risk_reason_e): KILL (ctl_kill), TX_OFF
//                (!ctl_tx_en), INVALID (price 0 or > OUCH_PRICE_LIMIT_MAX, qty 0 or >= OUCH_QTY_LIMIT, side not
//                B/S, tif the core does not send, a host ClOrdID byte outside [0-9A-Za-z ], or no UserRefNum left),
//                LOCATE (its bit in the allow bitmap is 0), QTY (qty > max_qty), NOTIONAL (qty * price >
//                max_notional), BUDGET (budget 0), RATE (no token), TX_BUSY (8 live orders queued), RING_FULL (no
//                ring credit). Every check
//                but INVALID and LOCATE reads its state in the decision cycle, so a limits commit applies to
//                whole orders.
// Spend        : an accepted order spends one budget unit and one token; a rejected or cancelled one does not
//                get them back (decision 21). cmd_budget_add saturates at 2^32 - 1.
// Token bucket : cfg_lim_wr at cycle c applies from c+1 = s: the bucket holds rate_burst and the period restarts;
//                rate_refill tokens are added in cycles s + k * rate_period (k >= 1), up to rate_burst, before
//                that cycle's decision. rate_period 0: no refill (decision 15). Reset: limits 0, bucket empty.
// Cancel       : a kill rise or a tx_en fall (both: KILL) cancels the queued orders (decisions 7, 20). An order
//                that m_tx_* hands over in that cycle is not cancelled (its frame has started). A cancelled
//                order keeps one of the 16 slots until its RISK_REJECT is written, one per cycle in queue order,
//                in a cycle without a decision's reject; its reject_ts is the cancel's cycle. TX_BUSY counts the
//                live orders only, so a cancel never refuses a valid order after a quick release (decision 24).
//                Held + live never exceeds 8 (an accept's cycle writes a held one out; a reject's cycle changes
//                neither), so the 16 slots never overlap.
// Ring credits : one per order entry event_packer's order queue can take (RISK_RING_CREDITS), given back by
//                ring_credit_ret when the packer moves an entry on. An accepted order reserves one for its later
//                entry (ORDER_SENT, or its cancel's RISK_REJECT), so an order that leaves always gets its entry. A
//                decision's RISK_REJECT takes a credit if there is one; else it is not written (ev_rej_dropped,
//                counted as RING_DROP_ORDERS) and a cancelled order may use the free port (decision 25).
// UserRefNum   : m_tx_user_ref is the counter; each order handed over takes it and the counter adds 1. It stops
//                at 2^32: no number left is INVALID. cmd_userref_wr takes effect only while ctl_kill is 1, the
//                queue is empty and the value is not 0; otherwise ev_userref_ignored (decision 22).
// Allow bitmap : symbol_filter (bit i of word w = locate 32w + i); the lookup is in the cycle after the order is
//                taken. After rst every bit is 0 for 2048 cycles of sweep, then until the host writes it.
// Reset        : kill-like: queue emptied (no rejects), budget 0, limits 0, bucket empty, counter 1.
module risk_check
  import hft_pkg::*;
(
    input logic clk,
    input logic rst,

    input logic [           63:0] ts_now,
    input logic [CFG_EPOCH_W-1:0] epoch_now, // cfg_epoch of host orders

    // Hardware orders (strategy_engine m_ord_*)
    input logic                    s_hw_valid,
    input logic [STRAT_RULE_W-1:0] s_hw_rule,
    input logic [             7:0] s_hw_side,
    input logic [            31:0] s_hw_qty,
    input logic [            31:0] s_hw_price,
    input logic [            15:0] s_hw_locate,
    input logic [            63:0] s_hw_stock,
    input logic [             7:0] s_hw_tif,
    input logic [            63:0] s_hw_seq,
    input logic [            63:0] s_hw_sof_ts,
    input logic [ CFG_EPOCH_W-1:0] s_hw_epoch,

    // Host orders (mailbox)
    input  logic         s_host_valid,
    output logic         s_host_ready,
    input  logic [  7:0] s_host_side,
    input  logic [ 31:0] s_host_qty,
    input  logic [ 31:0] s_host_price,
    input  logic [ 15:0] s_host_locate,
    input  logic [ 63:0] s_host_stock,
    input  logic [  7:0] s_host_tif,
    input  logic [111:0] s_host_clordid,

    // Controls and configuration (net_clk)
    input  logic        ctl_kill,
    input  logic        ctl_tx_en,
    input  logic        cfg_lim_wr,
    input  logic [31:0] cfg_max_qty,
    input  logic [63:0] cfg_max_notional,
    input  logic [31:0] cfg_rate_refill,
    input  logic [31:0] cfg_rate_period,
    input  logic [31:0] cfg_rate_burst,
    input  logic        cmd_budget_add,
    input  logic [31:0] cmd_budget_amount,
    input  logic        cmd_userref_wr,
    input  logic [31:0] cmd_userref_value,
    output logic        ev_userref_ignored,

    // Allow bitmap commands (as symbol_filter)
    input  logic        sym_req_valid,
    output logic        sym_req_ready,
    input  logic        sym_req_wr,
    input  logic [10:0] sym_req_addr,
    input  logic [31:0] sym_req_wdata,
    input  logic [ 3:0] sym_req_wstrb,
    output logic        sym_rsp_valid,
    output logic [31:0] sym_rsp_rdata,

    // Accepted orders to order_tx_builder
    output logic                          m_tx_valid,
    input  logic                          m_tx_ready,
    output logic       [            31:0] m_tx_user_ref,
    output order_src_e                    m_tx_source,
    output logic       [STRAT_RULE_W-1:0] m_tx_rule,
    output logic       [             7:0] m_tx_side,
    output logic       [            31:0] m_tx_qty,
    output logic       [            31:0] m_tx_price,
    output logic       [            15:0] m_tx_locate,
    output logic       [            63:0] m_tx_stock,
    output logic       [             7:0] m_tx_tif,
    output logic       [           111:0] m_tx_clordid,
    output logic       [            63:0] m_tx_seq,
    output logic       [            63:0] m_tx_sof_ts,
    output logic       [ CFG_EPOCH_W-1:0] m_tx_epoch,

    // Rejected and cancelled orders (RISK_REJECT entries), one per cycle
    output logic                            m_rej_valid,
    output risk_reason_e                    m_rej_reason,
    output order_src_e                      m_rej_source,
    output logic         [STRAT_RULE_W-1:0] m_rej_rule,
    output logic         [             7:0] m_rej_side,
    output logic         [            31:0] m_rej_qty,
    output logic         [            31:0] m_rej_price,
    output logic         [            15:0] m_rej_locate,
    output logic         [            63:0] m_rej_stock,
    output logic         [             7:0] m_rej_tif,
    output logic         [           111:0] m_rej_clordid,
    output logic         [            63:0] m_rej_seq,
    output logic         [            63:0] m_rej_sof_ts,
    output logic         [ CFG_EPOCH_W-1:0] m_rej_epoch,
    output logic         [            63:0] m_rej_ts,

    // Ring credits (event_packer)
    input  logic ring_credit_ret,
    output logic ev_rej_dropped,

    output logic [31:0] st_budget
);
  localparam int unsigned QD = RISK_TX_QUEUE;  // live orders at most (TX_BUSY)
  localparam int unsigned QM = 2 * QD;  // slots: live orders and cancelled ones not written out yet
  localparam int unsigned QP = $clog2(QM) + 1;  // queue pointer, with the wrap bit
  localparam int unsigned CW = $clog2(RISK_RING_CREDITS + 1);  // ring credit count

  typedef struct packed {
    order_src_e              source;
    logic [STRAT_RULE_W-1:0] rule;
    logic [7:0]              side;
    logic [31:0]             qty;
    logic [31:0]             price;
    logic [15:0]             locate;
    logic [63:0]             stock;
    logic [7:0]              tif;
    logic [111:0]            clordid;
    logic [63:0]             seq;
    logic [63:0]             sof_ts;
    logic [CFG_EPOCH_W-1:0]  epoch;
  } rorder_t;

  function automatic logic clordid_ok(input logic [111:0] c);
    logic ok;
    ok = 1'b1;
    for (int unsigned i = 0; i < 14; i++) begin
      ok = ok && (c[8*i+:8] == 8'h20 || (c[8*i+:8] >= 8'h30 && c[8*i+:8] <= 8'h39) ||
                  (c[8*i+:8] >= 8'h41 && c[8*i+:8] <= 8'h5A) || (c[8*i+:8] >= 8'h61 && c[8*i+:8] <= 8'h7A));
    end
    return ok;
  endfunction

  // ===================== Stage 0: arbiter, stateless checks =====================
  logic drain;  // cancelled orders still to write out
  logic take_host, in_valid;
  rorder_t in_ord;
  logic    in_inv;

  assign s_host_ready = !s_hw_valid && !drain && !rst;
  assign take_host    = s_host_valid && s_host_ready;
  assign in_valid     = s_hw_valid || take_host;

  always_comb begin
    in_ord = '0;
    if (s_hw_valid) begin
      in_ord.source  = ORDER_SRC_RULE;
      in_ord.rule    = s_hw_rule;
      in_ord.side    = s_hw_side;
      in_ord.qty     = s_hw_qty;
      in_ord.price   = s_hw_price;
      in_ord.locate  = s_hw_locate;
      in_ord.stock   = s_hw_stock;
      in_ord.tif     = s_hw_tif;
      in_ord.clordid = {14{8'h20}};
      in_ord.seq     = s_hw_seq;
      in_ord.sof_ts  = s_hw_sof_ts;
      in_ord.epoch   = s_hw_epoch;
    end else begin
      in_ord.source  = ORDER_SRC_HOST;
      in_ord.side    = s_host_side;
      in_ord.qty     = s_host_qty;
      in_ord.price   = s_host_price;
      in_ord.locate  = s_host_locate;
      in_ord.stock   = s_host_stock;
      in_ord.tif     = s_host_tif;
      in_ord.clordid = s_host_clordid;
      in_ord.epoch   = epoch_now;
    end
    in_inv = in_ord.price == '0 || in_ord.price > OUCH_PRICE_LIMIT_MAX || in_ord.qty == '0 ||
        in_ord.qty >= OUCH_QTY_LIMIT || !(in_ord.side inside {OUCH_SIDE_BUY, OUCH_SIDE_SELL}) ||
        !ouch_tif_ok(in_ord.tif) || (!s_hw_valid && !clordid_ok(in_ord.clordid));
  end

  logic lk_hit;
  symbol_filter u_allow (
      .clk          (clk),
      .rst          (rst),
      .lk_locate    (in_ord.locate),
      .lk_hit       (lk_hit),
      .sym_req_valid(sym_req_valid),
      .sym_req_ready(sym_req_ready),
      .sym_req_wr   (sym_req_wr),
      .sym_req_addr (sym_req_addr),
      .sym_req_wdata(sym_req_wdata),
      .sym_req_wstrb(sym_req_wstrb),
      .sym_rsp_valid(sym_rsp_valid),
      .sym_rsp_rdata(sym_rsp_rdata)
  );

  // ===================== Stages 1, 2: the lookup result, the product =====================
  logic v1_q, v2_q, v3_q;
  rorder_t o1_q, o2_q, o3_q;
  logic inv1_q, inv2_q, inv3_q, hit2_q, hit3_q;
  logic [63:0] prod2_q;

  always_ff @(posedge clk) begin
    o1_q    <= in_ord;
    inv1_q  <= in_inv;
    o2_q    <= o1_q;
    inv2_q  <= inv1_q;
    hit2_q  <= lk_hit;
    prod2_q <= 64'(o1_q.qty) * 64'(o1_q.price);
    o3_q    <= o2_q;
    inv3_q  <= inv2_q;
    hit3_q  <= hit2_q;
    if (rst) begin
      v1_q <= 1'b0;
      v2_q <= 1'b0;
      v3_q <= 1'b0;
    end else begin
      v1_q <= in_valid;
      v2_q <= v1_q;
      v3_q <= v2_q;
    end
  end

  // ===================== Stage 3: the decision =====================
  logic [31:0] max_qty_q, rate_refill_q, rate_period_q, rate_burst_q, budget_q, tokens_q, pc_q;
  logic [63:0] max_notional_q;
  logic kill_d_q, tx_en_d_q;
  logic [32:0] ref_next_q;  // 2^32: no number left
  logic [QP-1:0] wr_q;
  (* max_fanout = 16 *) logic [QP-1:0] rd_q;  // the queue's read address drives every LUTRAM column (S5 timing)
  logic [QP-1:0] held_q;  // cancelled, not written out yet: the slots from rd on
  logic [QP-1:0] live_q;  // queued, not cancelled
  logic [CW-1:0] credits_q;
  logic userref_ok;  // a TX_USERREF_NEXT write takes effect (decision 22)

  // Predicates the decision reads, each registered from next-state values one cycle ahead (timing, S5): the
  // decision is then a priority over flags. Where the decision's own accept changes the next state, both
  // candidates are computed and accept only selects. Each flag equals its definition in every cycle
  // (risk_props a_flags).
  logic          tick_q;  // = rate_period_q != 0 && pc_q == 0, not in the cycle after a limits commit or reset
  logic tok_nz_q;  // = tokens_q != 0
  logic budget_zero_q;  // = budget_q == 0
  logic refs_zero_q;  // = no UserRefNum left: 2^32 - ref_next_q - live_q == 0
  logic qty_gt_q;  // = o3_q.qty > max_qty_q
  logic notional_gt_q;  // = qty * price of the order in stage 3 > max_notional_q

  logic [31:0] tok_sum;  // mod 2^32: a sum past it is above burst (sat1)
  logic [31:0] tokens_eff;
  logic rate_ok;
  logic full, cancel, cancel_kill;
  risk_reason_e        reason;
  logic                accept;

  // Token bucket, per case in parallel (timing, S5): tokens_eff = the refilled (tick) or unchanged count,
  // saturated at burst. tokens + refill > burst is compared as tokens > burst - refill (room_q, set with the
  // limits), and each case's value minus one (an accept) is formed beside it, so accept only selects.
  logic signed  [33:0] room_q;  // burst - refill
  logic [31:0] burst_m1_q, refill_m1_q;  // burst - 1, refill - 1 (mod 2^32)
  logic sat1, sat0;  // with a tick, without one: the count is above burst
  logic [31:0] tok_sum_m1, tok_m1, tokens_n;
  logic eff_nz, eff_gt1;  // tokens_eff != 0, > 1
  assign tok_sum    = tokens_q + rate_refill_q;
  assign tok_sum_m1 = tokens_q + refill_m1_q;
  assign tok_m1     = tokens_q - 32'd1;
  assign sat1       = $signed({2'b00, tokens_q}) > room_q;
  assign sat0       = tokens_q > rate_burst_q;
  always_comb begin
    if (tick_q ? sat1 : sat0) begin
      tokens_eff = rate_burst_q;
      tokens_n   = accept ? burst_m1_q : rate_burst_q;
    end else if (tick_q) begin
      tokens_eff = tok_sum;
      tokens_n   = accept ? tok_sum_m1 : tok_sum;
    end else begin
      tokens_eff = tokens_q;
      tokens_n   = accept ? tok_m1 : tokens_q;
    end
    eff_nz  = |tokens_eff;
    eff_gt1 = |tokens_eff[31:1];
  end
  // tokens_eff != 0, from flags: the bucket can hold a token and has one, or gets one now
  assign rate_ok = rate_burst_q != '0 && (tok_nz_q || (tick_q && rate_refill_q != '0));
  assign full        = live_q == QP'(QD);  // held + live <= QD always (risk_props), so QM slots never run out
  assign cancel_kill = ctl_kill && !kill_d_q;
  assign cancel = cancel_kill || (!ctl_tx_en && tx_en_d_q);

  always_comb begin
    if (ctl_kill) begin
      reason = RISK_KILL;
    end else if (!ctl_tx_en) begin
      reason = RISK_TX_OFF;
    end else if (inv3_q || refs_zero_q) begin
      reason = RISK_INVALID;
    end else if (!hit3_q) begin
      reason = RISK_LOCATE;
    end else if (qty_gt_q) begin
      reason = RISK_QTY;
    end else if (notional_gt_q) begin
      reason = RISK_NOTIONAL;
    end else if (budget_zero_q) begin
      reason = RISK_BUDGET;
    end else if (!rate_ok) begin
      reason = RISK_RATE;
    end else if (full) begin
      reason = RISK_TX_BUSY;
    end else if (credits_q == '0) begin
      reason = RISK_RING_FULL;
    end else begin
      reason = RISK_NONE;
    end
  end
  assign accept = v3_q && reason == RISK_NONE;

  // Budget: spend, then add, saturating at 2^32 - 1. Both candidates (no accept, accept), then accept selects.
  logic [32:0] bsum0, bsum1;
  logic [31:0] budget0, budget1, budget_n;
  assign bsum0    = 33'(budget_q) + (cmd_budget_add ? 33'(cmd_budget_amount) : 33'd0);
  assign bsum1    = bsum0 - 33'd1;  // accept: budget_q > 0, so no underflow
  assign budget0  = bsum0[32] ? 32'hFFFF_FFFF : bsum0[31:0];
  assign budget1  = bsum1[32] ? 32'hFFFF_FFFF : bsum1[31:0];
  assign budget_n = accept ? budget1 : budget0;

  // Next-state values for the flags (as the state registers below take them).
  logic [31:0] max_qty_n, rate_period_n, pc_n;
  logic [63:0] max_notional_n;
  logic        started_n;
  assign max_qty_n = rst ? '0 : cfg_lim_wr ? cfg_max_qty : max_qty_q;
  assign max_notional_n = rst ? '0 : cfg_lim_wr ? cfg_max_notional : max_notional_q;
  assign rate_period_n = rst ? '0 : cfg_lim_wr ? cfg_rate_period : rate_period_q;
  assign started_n = !rst && !cfg_lim_wr;
  assign pc_n = (rst || cfg_lim_wr) ? '0 : (pc_q == rate_period_q - 32'd1) ? '0 : pc_q + 32'd1;

  // refs_zero_q next (rst aside): with x = the next ref_next_q (handover: +1, else a TX_USERREF_NEXT write that
  // takes effect: the value, else the same) and l = the next live_q without this cycle's accept (cancel: 0, else
  // live_q - handover), no UserRefNum is left iff x + l == 2^32, or 2^32 - 1 with an accept (it takes one more).
  // The sum is formed per case, so no select sits in front of an adder: a handover alone keeps ref_next + live.
  logic uwr;  // a TX_USERREF_NEXT write takes effect
  logic [32:0] sum_rl, sum_vl;
  logic refs_z, refs_o;  // x + l == 2^32, == 2^32 - 1
  assign uwr    = cmd_userref_wr && userref_ok;
  assign sum_rl = ref_next_q + 33'(live_q);
  assign sum_vl = 33'(cmd_userref_value) + 33'(live_q);
  always_comb begin
    if (cancel) begin
      if (handover) begin  // x = ref_next_q + 1, l = 0
        refs_z = ref_next_q == 33'hFFFF_FFFF;
        refs_o = ref_next_q == 33'hFFFF_FFFE;
      end else if (uwr) begin  // x = the value (< 2^32), l = 0
        refs_z = 1'b0;
        refs_o = cmd_userref_value == 32'hFFFF_FFFF;
      end else begin  // x = ref_next_q, l = 0
        refs_z = ref_next_q == 33'h1_0000_0000;
        refs_o = ref_next_q == 33'hFFFF_FFFF;
      end
    end else if (handover || !uwr) begin  // x + l = ref_next_q + live_q
      refs_z = sum_rl == 33'h1_0000_0000;
      refs_o = sum_rl == 33'hFFFF_FFFF;
    end else begin  // x = the value, l = live_q
      refs_z = sum_vl == 33'h1_0000_0000;
      refs_o = sum_vl == 33'hFFFF_FFFF;
    end
  end

  // ===================== Queue =====================
  rorder_t        q_mem [QM];
  logic           q_kill[QM];  // the cancel's reason: KILL, else TX_OFF
  logic    [63:0] q_ts  [QM];  // the cancel's cycle
  logic handover, drain_out, rej_dec;
  logic [QP-2:0] cstart;  // the first live slot after this cycle's handover
  logic [QP-1:0] cancelled;  // live orders this cycle's cancel takes

  assign drain      = held_q != '0;
  assign m_tx_valid = !drain && live_q != '0;
  assign handover   = m_tx_valid && m_tx_ready;
  assign rej_dec    = v3_q && reason != RISK_NONE && credits_q != '0;  // written: it has a credit
  assign drain_out  = drain && !rej_dec;
  // live orders follow the held ones; a handover only happens with none held
  assign cstart     = rd_q[QP-2:0] + held_q[QP-2:0] + (QP - 1)'(handover);
  assign cancelled  = cancel ? live_q - QP'(handover) : '0;

  // The slots of the live orders that this cycle's cancel takes: offsets [0, cancelled) from cstart, modulo QM.
  // The offset is kept in QP-1 bits so that it wraps (a wider expression would not).
  logic [QM-1:0] cmark;
  always_comb begin
    logic [QP-2:0] off;
    for (int unsigned i = 0; i < QM; i++) begin
      off      = (QP - 1)'(i) - cstart;
      cmark[i] = QP'(off) < cancelled;
    end
  end

  always_ff @(posedge clk) begin
    if (accept) begin
      q_mem[wr_q[QP-2:0]] <= o3_q;
    end
    for (int unsigned i = 0; i < QM; i++) begin
      if (cmark[i]) begin
        q_kill[i] <= cancel_kill;
        q_ts[i]   <= ts_now;
      end
    end
  end

  // ===================== Outputs and state =====================
  rorder_t rej_ord;
  assign rej_ord = rej_dec ? o3_q : q_mem[rd_q[QP-2:0]];

  always_ff @(posedge clk) begin
    if (rej_dec || drain_out) begin
      m_rej_reason  <= rej_dec ? reason : (q_kill[rd_q[QP-2:0]] ? RISK_KILL : RISK_TX_OFF);
      m_rej_source  <= rej_ord.source;
      m_rej_rule    <= rej_ord.rule;
      m_rej_side    <= rej_ord.side;
      m_rej_qty     <= rej_ord.qty;
      m_rej_price   <= rej_ord.price;
      m_rej_locate  <= rej_ord.locate;
      m_rej_stock   <= rej_ord.stock;
      m_rej_tif     <= rej_ord.tif;
      m_rej_clordid <= rej_ord.clordid;
      m_rej_seq     <= rej_ord.seq;
      m_rej_sof_ts  <= rej_ord.sof_ts;
      m_rej_epoch   <= rej_ord.epoch;
      m_rej_ts      <= rej_dec ? ts_now : q_ts[rd_q[QP-2:0]];
    end
    if (cfg_lim_wr) begin
      max_qty_q      <= cfg_max_qty;
      max_notional_q <= cfg_max_notional;
      rate_refill_q  <= cfg_rate_refill;
      rate_period_q  <= cfg_rate_period;
      rate_burst_q   <= cfg_rate_burst;
      room_q         <= $signed({2'b00, cfg_rate_burst}) - $signed({2'b00, cfg_rate_refill});
      burst_m1_q     <= cfg_rate_burst - 32'd1;
      refill_m1_q    <= cfg_rate_refill - 32'd1;
    end
    if (rst) begin
      m_rej_valid        <= 1'b0;
      ev_userref_ignored <= 1'b0;
      max_qty_q          <= '0;
      max_notional_q     <= '0;
      rate_refill_q      <= '0;
      rate_period_q      <= '0;
      rate_burst_q       <= '0;
      room_q             <= '0;
      burst_m1_q         <= '1;
      refill_m1_q        <= '1;
      budget_q           <= '0;
      tokens_q           <= '0;
      pc_q               <= '0;
      kill_d_q           <= 1'b1;
      tx_en_d_q          <= 1'b0;
      ref_next_q         <= 33'd1;
      wr_q               <= '0;
      rd_q               <= '0;
      held_q             <= '0;
      live_q             <= '0;
      credits_q          <= CW'(RISK_RING_CREDITS);
      ev_rej_dropped     <= 1'b0;
    end else begin
      m_rej_valid <= rej_dec || drain_out;
      kill_d_q    <= ctl_kill;
      tx_en_d_q   <= ctl_tx_en;

      budget_q    <= budget_n;

      // token bucket
      if (cfg_lim_wr) begin
        tokens_q <= cfg_rate_burst;
        pc_q     <= '0;
      end else begin
        tokens_q <= tokens_n;
        pc_q     <= (pc_q == rate_period_q - 32'd1) ? '0 : pc_q + 32'd1;
      end

      // queue pointers
      wr_q <= wr_q + QP'(accept);
      if (drain_out) begin
        rd_q <= rd_q + 1'b1;
      end else if (handover) begin
        rd_q <= rd_q + 1'b1;
      end
      held_q         <= held_q - QP'(drain_out) + cancelled;
      credits_q      <= credits_q - CW'(v3_q && credits_q != '0) + CW'(ring_credit_ret);
      ev_rej_dropped <= v3_q && reason != RISK_NONE && credits_q == '0;
      if (cancel) begin
        live_q <= '0;
      end else begin
        live_q <= live_q + QP'(accept) - QP'(handover);
      end

      // UserRefNum. A write that takes effect needs an empty queue, so no handover in its cycle.
      ev_userref_ignored <= cmd_userref_wr && !userref_ok;
      if (handover) begin
        ref_next_q <= ref_next_q + 33'd1;
      end else if (cmd_userref_wr && userref_ok) begin
        ref_next_q <= 33'(cmd_userref_value);
      end
    end
  end

  always_ff @(posedge clk) begin
    qty_gt_q      <= o2_q.qty > max_qty_n;
    notional_gt_q <= prod2_q > max_notional_n;
    tick_q        <= started_n && rate_period_n != '0 && pc_n == '0;
    if (rst) begin
      tok_nz_q      <= 1'b0;
      budget_zero_q <= 1'b1;
      refs_zero_q   <= 1'b0;
    end else begin
      // an accept leaves tokens_eff - 1; it needs tokens_eff != 0
      tok_nz_q      <= cfg_lim_wr ? cfg_rate_burst != '0 : accept ? eff_gt1 : eff_nz;
      budget_zero_q <= accept ? budget1 == '0 : budget0 == '0;
      refs_zero_q   <= accept ? refs_o : refs_z;  // an accept is never in a cancel's cycle
    end
  end

  rorder_t head;
  assign userref_ok    = ctl_kill && wr_q == rd_q && cmd_userref_value != '0;
  assign head          = q_mem[rd_q[QP-2:0]];
  assign m_tx_user_ref = ref_next_q[31:0];
  assign m_tx_source   = head.source;
  assign m_tx_rule     = head.rule;
  assign m_tx_side     = head.side;
  assign m_tx_qty      = head.qty;
  assign m_tx_price    = head.price;
  assign m_tx_locate   = head.locate;
  assign m_tx_stock    = head.stock;
  assign m_tx_tif      = head.tif;
  assign m_tx_clordid  = head.clordid;
  assign m_tx_seq      = head.seq;
  assign m_tx_sof_ts   = head.sof_ts;
  assign m_tx_epoch    = head.epoch;
  assign st_budget     = budget_q;
endmodule
