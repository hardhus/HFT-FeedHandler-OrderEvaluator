// strategy_engine: 8 host-programmed rules on ITCH A/F/P messages; a fired order waits for its packet to be
// accepted (audit/14 §2.3, §2.4, P4; decisions 1, 2, 6, 8, 11, 14, 16, 18, 19, 23).
// Clock domain : clk (net_clk)
// Latency      : s_pkt_valid (accepted) -> the packet's first m_ord_valid 2 cycles, then one order per cycle
//                in fire order
// Throughput   : one message per cycle, no backpressure (the consumer takes an order every cycle)
// Input        : itch_decoder m_ev_* and m_pkt_*. A packet's messages come before its event; a message in the
//                cycle of a packet event belongs to the next packet.
// Match        : a rule matches a message when it is enabled, armed, has no static error and has not fired in
//                this packet; the message is decoded (ORDER or TRADE class), its type is in msg_mask (A F P),
//                the locate is equal, the side is in side_match, price <= or >= price_threshold and shares >=
//                min_shares. Per message the lowest matching index fires (P4); a rule fires at most once per
//                packet.
// Order        : side from action_side (BUY, SELL, or the event's opposite: B<->S, else 0); qty = qty, or
//                min(shares, qty); price = the event price, or threshold + offset (computed at the commit);
//                locate, stock, seq, sof_ts, epoch from the event; tif from the rule. Built in the fire cycle,
//                so a later commit does not change it.
// Packet event : accepted (MOLD_OK, HEARTBEAT, EOS): the packet's orders leave in fire order, and a fired rule
//                is disarmed unless a host command reached it after its fire (decision 18). Any other status:
//                the orders are dropped and the rules stay armed (decision 1).
// Commands     : cfg_rule_wr replaces the rule and disarms it (decision 8); cmd_disarm, cmd_arm take masks. A
//                command at cycle c applies from c+1, except that a disarm or commit of a rule also keeps it
//                from firing on the message of cycle c. An order already fired still leaves (decision 14).
// Ties         : in one cycle the packet event comes first, then the message, then the commands; when
//                disarming and arming effects meet (disarm and arm, commit and arm, the acceptance's disarm
//                and an arm), the rule is disarmed. An arm in the cycle of a fire is not after the fire, so
//                the acceptance still disarms that rule (decision 19).
// Order path   : while it is closed (ctl_kill, or !ctl_tx_en) no rule fires, so none is used up; the cycle in
//                which ctl_kill rises disarms every rule (an arm in that cycle loses). Orders fired before still
//                leave (risk_check rejects them). Decision 23.
// Static error : at cfg_rule_wr: locate 0, a tif the core does not send, action_side 3, qty 0, qty >= 1,000,000
//                with qty_min = 0, or threshold + offset outside 1..OUCH_PRICE_LIMIT_MAX with
//                price_threshold_mode = 1. The rule never fires; st_error shows it (decisions 6, 11, 16).
// Capacity     : the order queue holds 16: at most 8 waiting for their packet and 8 leaving.
// Reset        : rst disarms every rule and sets every error bit (a reset rule has locate 0); the queue is
//                emptied. Rules must be written again.
module strategy_engine
  import hft_pkg::*;
(
    input logic clk,
    input logic rst,

    // Output messages (itch_decoder m_ev_*); alpha fields in wire order
    input logic                          s_ev_valid,
    input itch_class_e                   s_ev_class,
    input logic        [            7:0] s_ev_type,
    input logic        [           63:0] s_ev_seq,
    input logic        [           63:0] s_ev_sof_ts,
    input logic        [CFG_EPOCH_W-1:0] s_ev_epoch,
    input logic        [           15:0] s_ev_locate,
    input logic        [           63:0] s_ev_shares,
    input logic        [           31:0] s_ev_price,
    input logic        [            7:0] s_ev_side,
    input logic        [           63:0] s_ev_stock,

    // Packet events (itch_decoder m_pkt_*)
    input logic         s_pkt_valid,
    input mold_status_e s_pkt_status,

    // Order path controls (net_clk levels, the same as risk_check's)
    input logic ctl_kill,
    input logic ctl_tx_en,

    // Configuration and commands (net_clk; from hft_csr_wrap)
    input logic                            cfg_rule_wr,
    input logic        [ STRAT_RULE_W-1:0] cfg_rule_idx,
    input strat_rule_t                     cfg_rule,
    input logic                            cmd_disarm,
    input logic        [STRAT_N_RULES-1:0] cmd_disarm_mask,
    input logic                            cmd_arm,
    input logic        [STRAT_N_RULES-1:0] cmd_arm_mask,

    // Orders to risk_check, one per cycle at most
    output logic                    m_ord_valid,
    output logic [STRAT_RULE_W-1:0] m_ord_rule,
    output logic [             7:0] m_ord_side,
    output logic [            31:0] m_ord_qty,
    output logic [            31:0] m_ord_price,
    output logic [            15:0] m_ord_locate,
    output logic [            63:0] m_ord_stock,
    output logic [             7:0] m_ord_tif,
    output logic [            63:0] m_ord_seq,
    output logic [            63:0] m_ord_sof_ts,
    output logic [ CFG_EPOCH_W-1:0] m_ord_epoch,

    // Status
    output logic [STRAT_N_RULES-1:0] st_armed,
    output logic [STRAT_N_RULES-1:0] st_error
);
  localparam int unsigned N = STRAT_N_RULES;
  localparam int unsigned DEPTH = 2 * N;
  localparam int unsigned PTR_W = $clog2(DEPTH) + 1;

  typedef struct packed {
    logic [STRAT_RULE_W-1:0] rule;
    logic [7:0]              side;
    logic [31:0]             qty;
    logic [31:0]             price;
    logic [15:0]             locate;
    logic [63:0]             stock;
    logic [7:0]              tif;
    logic [63:0]             seq;
    logic [63:0]             sof_ts;
    logic [CFG_EPOCH_W-1:0]  epoch;
  } order_t;

  // ===================== Rules =====================
  strat_rule_t        rule_q     [N];
  logic        [31:0] thr_price_q[N];  // threshold + offset, valid when the rule has no error
  logic [N-1:0] err_q, armed_q, fired_q, doa_q;
  logic        kill_d_q;  // ctl_kill of the previous cycle  // doa: disarm on accept (decision 18)

  // Static error of the rule being written, and its threshold + offset.
  logic [33:0] wr_sum;
  logic        wr_err;
  always_comb begin
    wr_sum = {2'b00, cfg_rule.price_threshold} + {{2{cfg_rule.price_offset[31]}}, cfg_rule.price_offset};
    wr_err = cfg_rule.locate == '0 || !ouch_tif_ok(cfg_rule.tif) || cfg_rule.action_side == 2'd3 ||
        cfg_rule.qty == '0 || (!cfg_rule.qty_min && cfg_rule.qty >= OUCH_QTY_LIMIT) ||
        (cfg_rule.price_threshold_mode &&
         ($signed(wr_sum) < 34'sd1 || $signed(wr_sum) > $signed({2'b00, OUCH_PRICE_LIMIT_MAX})));
  end

  // ===================== Packet event, then the message =====================
  logic pkt_acc, ev_decoded;
  logic [N-1:0] pkt_off, armed_p, fired_p, doa_p;  // after this cycle's packet event
  logic [N-1:0] block, arm_set;  // this cycle's commands (decision 19)
  assign pkt_acc    = s_pkt_status inside {MOLD_OK, MOLD_HEARTBEAT, MOLD_EOS};
  assign ev_decoded = s_ev_class inside {ITCH_ORDER, ITCH_TRADE};
  assign pkt_off    = (s_pkt_valid && pkt_acc) ? doa_q : '0;
  assign armed_p    = armed_q & ~pkt_off;
  assign fired_p    = s_pkt_valid ? '0 : fired_q;
  assign doa_p      = s_pkt_valid ? '0 : doa_q;

  logic [N-1:0] hit, fire;
  order_t ord;
  always_comb begin
    logic type_ok, side_ok, price_ok;
    logic [31:0] qty;
    logic [ 7:0] side;
    hit = '0;
    for (int unsigned r = 0; r < N; r++) begin
      type_ok = (s_ev_type == ITCH_T_ADD_ORDER && rule_q[r].msg_mask[0]) ||
          (s_ev_type == ITCH_T_ADD_ORDER_MPID && rule_q[r].msg_mask[1]) ||
          (s_ev_type == ITCH_T_TRADE && rule_q[r].msg_mask[2]);
      side_ok = (s_ev_side == OUCH_SIDE_BUY && rule_q[r].side_match[0]) ||
          (s_ev_side == OUCH_SIDE_SELL && rule_q[r].side_match[1]);
      price_ok = rule_q[r].price_ge ? s_ev_price >= rule_q[r].price_threshold :
          s_ev_price <= rule_q[r].price_threshold;
      hit[r] = s_ev_valid && ev_decoded && type_ok && side_ok && price_ok &&
          s_ev_locate == rule_q[r].locate && s_ev_shares >= {32'd0, rule_q[r].min_shares} &&
          rule_q[r].enable && armed_p[r] && !block[r] && !fired_p[r] && !err_q[r] &&
          !ctl_kill && ctl_tx_en;
    end
    fire       = hit & (~hit + 1'b1);  // the lowest index

    // The order of the fired rule: each rule's fields in parallel, then a one-hot OR.
    ord        = '0;
    ord.locate = s_ev_locate;
    ord.stock  = s_ev_stock;
    ord.seq    = s_ev_seq;
    ord.sof_ts = s_ev_sof_ts;
    ord.epoch  = s_ev_epoch;
    for (int unsigned r = 0; r < N; r++) begin
      qty = rule_q[r].qty;
      if (rule_q[r].qty_min && s_ev_shares < {32'd0, rule_q[r].qty}) begin
        qty = s_ev_shares[31:0];
      end
      unique case (strat_action_e'(rule_q[r].action_side))
        STRAT_ACT_BUY:  side = OUCH_SIDE_BUY;
        STRAT_ACT_SELL: side = OUCH_SIDE_SELL;
        default: begin  // OPPOSITE (3 never fires)
          side = s_ev_side == OUCH_SIDE_BUY ? OUCH_SIDE_SELL : s_ev_side == OUCH_SIDE_SELL ? OUCH_SIDE_BUY : 8'h00;
        end
      endcase
      if (fire[r]) begin
        ord.rule  = ord.rule | STRAT_RULE_W'(r);
        ord.side  = ord.side | side;
        ord.qty   = ord.qty | qty;
        ord.price = ord.price | (rule_q[r].price_threshold_mode ? thr_price_q[r] : s_ev_price);
        ord.tif   = ord.tif | rule_q[r].tif;
      end
    end
  end

  // ===================== Commands =====================
  always_comb begin
    block = cmd_disarm ? cmd_disarm_mask : '0;
    if (ctl_kill && !kill_d_q) begin
      block = '1;
    end
    if (cfg_rule_wr) begin
      block[cfg_rule_idx] = 1'b1;
    end
  end
  assign arm_set = cmd_arm ? cmd_arm_mask : '0;

  // A tie goes to the disarming side; a fire of this cycle stays marked for the acceptance.
  logic [N-1:0] armed_n, doa_n;
  assign armed_n = (armed_p | arm_set) & ~(block | pkt_off);
  assign doa_n   = (doa_p & ~(block | arm_set)) | fire;

  always_ff @(posedge clk) begin
    if (cfg_rule_wr) begin
      rule_q[cfg_rule_idx]      <= cfg_rule;
      thr_price_q[cfg_rule_idx] <= wr_sum[31:0];
    end
    if (rst) begin
      err_q    <= '1;
      armed_q  <= '0;
      kill_d_q <= 1'b1;
      fired_q  <= '0;
      doa_q    <= '0;
    end else begin
      if (cfg_rule_wr) begin
        err_q[cfg_rule_idx] <= wr_err;
      end
      armed_q  <= armed_n;
      kill_d_q <= ctl_kill;
      fired_q  <= fired_p | fire;
      doa_q    <= doa_n;
    end
  end

  assign st_armed = armed_q;
  assign st_error = err_q;

  // ===================== Order queue (commit per packet) =====================
  logic fire_q, pkt_q, pkt_acc_q;
  order_t ord_q;
  order_t mem   [DEPTH];
  logic [PTR_W-1:0] wr_ptr_q, commit_ptr_q, rd_ptr_q;
  logic [PTR_W-1:0] base, commit_n;

  always_ff @(posedge clk) begin
    ord_q <= ord;
    if (rst) begin
      fire_q    <= 1'b0;
      pkt_q     <= 1'b0;
      pkt_acc_q <= 1'b0;
    end else begin
      fire_q    <= |fire;
      pkt_q     <= s_pkt_valid;
      pkt_acc_q <= pkt_acc;
    end
  end

  // The packet event registered with its last message's fire; a fire in its cycle is the next packet's.
  assign base     = (pkt_q && !pkt_acc_q) ? commit_ptr_q : wr_ptr_q;
  assign commit_n = (pkt_q && pkt_acc_q) ? wr_ptr_q : commit_ptr_q;

  always_ff @(posedge clk) begin
    if (fire_q) begin
      mem[base[PTR_W-2:0]] <= ord_q;
    end
  end

  order_t out_q;
  always_ff @(posedge clk) begin
    if (commit_n != rd_ptr_q) begin
      out_q <= mem[rd_ptr_q[PTR_W-2:0]];
    end
    if (rst) begin
      wr_ptr_q     <= '0;
      commit_ptr_q <= '0;
      rd_ptr_q     <= '0;
      m_ord_valid  <= 1'b0;
    end else begin
      wr_ptr_q     <= base + PTR_W'(fire_q);
      commit_ptr_q <= commit_n;
      m_ord_valid  <= commit_n != rd_ptr_q;
      if (commit_n != rd_ptr_q) begin
        rd_ptr_q <= rd_ptr_q + 1'b1;
      end
    end
  end

  assign m_ord_rule   = out_q.rule;
  assign m_ord_side   = out_q.side;
  assign m_ord_qty    = out_q.qty;
  assign m_ord_price  = out_q.price;
  assign m_ord_locate = out_q.locate;
  assign m_ord_stock  = out_q.stock;
  assign m_ord_tif    = out_q.tif;
  assign m_ord_seq    = out_q.seq;
  assign m_ord_sof_ts = out_q.sof_ts;
  assign m_ord_epoch  = out_q.epoch;
endmodule
