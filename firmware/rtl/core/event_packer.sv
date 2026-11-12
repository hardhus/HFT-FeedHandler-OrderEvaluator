// event_packer: turns itch_decoder's output messages and packet events into 128-byte ring entries and
// delivers them whole per packet through a COMMIT=1 cdc_async_fifo; writes the order entries (ORDER_SENT,
// RISK_REJECT) between packets (audit/14 P8; decisions 25, 28-31).
// Clock domain : clk (net_clk)
// Latency      : s_ev_valid -> fifo_wr_en 1 cycle; s_pkt_valid -> fifo_commit or fifo_rollback 1 cycle,
//                2 for a packet with both a GAP and an EOS entry. An order entry taken at edge k is written
//                (fifo_wr_en and fifo_commit together) in the cycle after edge k+1 at the earliest.
// Throughput   : one message per cycle, no backpressure; one order entry per cycle in free cycles
// Input        : the itch_decoder outputs. A packet's messages come before its event. After a packet event
//                no message for 2 cycles and no accepted event for 2 cycles (moldudp64_rx: a packet has
//                at least 3 beats before its first message or to be accepted): room for GAP and EOS,
//                written with no stall. A rejected event in the cycle of a pending EOS entry
//                has no message (at most 2 beats) and is ignored: it would roll back nothing.
// Commit       : accepted packet (MOLD_OK, HEARTBEAT, EOS): its message entries, then GAP if it has a gap,
//                then EOS, then fifo_commit. Any other status: fifo_rollback, nothing is counted.
// Drop         : a write that finds the FIFO full or busy drops the packet: nothing more of it is written and
//                it rolls back at its end. An accepted packet is then counted (cnt_drop_pkts,
//                cnt_drop_entries: all its entries, GAP and EOS included) and the next committed entry has
//                overflow_before. A rejected one is no loss: not counted, no flag.
// Order entries: s_sent_* (order_tx_builder) and s_rej_* (risk_check) go into a queue of RISK_RING_CREDITS;
//                in one cycle the ORDER_SENT first (decision 28). The head is taken in a cycle with no pending
//                EOS, no message, no packet event and no message of an open packet (so never inside a packet's
//                unit), and written with its own fifo_commit. A full or busy FIFO keeps it queued (it waits,
//                never drops; decision 25); the entry taken behind it is cancelled, so nothing is written
//                twice. Each written entry gives its ring credit back on ring_credit_ret, in the next cycle.
//                The producers never send more than the queue holds: risk_check counts the credits.
// Lost reject  : s_rej_lost (risk_check ev_rej_dropped) is a decision's reject that had no credit. It stands
//                after that cycle's ORDER_SENT and before a reject on s_rej_* in the same cycle; the next
//                committed entry after its place (a packet's or an order's) has overflow_before (decision 29).
//                It is kept as a mark on the queue entry ahead of it, or, with none, as a pending one taken in
//                the first cycle where an order entry could be taken. A dropped packet's flag goes on an
//                order entry too, when that is the next committed one (decision 31).
// Capacity     : a packet has at most RING_MAX_PKT_ENTRIES entries: the FIFO must hold that many, or it
//                always drops.
// Counters     : CNT_W bits each, wrapping, cleared only by rst. Registers: a reader that samples them on
//                one clock edge gets the same cycle of all three. Packet entries count always 3 cycles after
//                s_pkt_valid, with or without a pending EOS entry, so hft_csr_wrap can hold its ITCH and MOLD
//                counters to the same packet events (audit/13 S6). An order entry counts on the edge that
//                writes it; cnt_ord_queued (the entries in the queue, decision 30) rises on the edge that takes
//                one and falls on that same writing edge.
// Reset        : rst clears everything, the order queue too: reset risk_check (its credits) and the FIFO with
//                it (entries of a packet cut by it are gone).
module event_packer
  import hft_pkg::*;
  import event_desc_pkg::*;
#(
    parameter int unsigned CNT_W = 64
) (
    input logic clk,
    input logic rst,

    // Output messages (itch_decoder m_ev_*); alpha fields in wire order
    input logic                             s_ev_valid,
    input itch_class_e                      s_ev_class,
    input logic        [               7:0] s_ev_type,
    input logic        [MOLD_MSG_LEN_W-1:0] s_ev_len,
    input logic        [              63:0] s_ev_seq,
    input logic        [              63:0] s_ev_sof_ts,
    input logic        [   CFG_EPOCH_W-1:0] s_ev_epoch,
    input logic        [              15:0] s_ev_locate,
    input logic        [              15:0] s_ev_tracking,
    input logic        [              47:0] s_ev_ts,
    input logic        [              63:0] s_ev_order_ref,
    input logic        [              63:0] s_ev_new_ref,
    input logic        [              63:0] s_ev_shares,
    input logic        [              31:0] s_ev_price,
    input logic        [              63:0] s_ev_match,
    input logic        [               7:0] s_ev_side,
    input logic        [              63:0] s_ev_stock,
    input logic        [              31:0] s_ev_attribution,
    input logic        [               7:0] s_ev_printable,
    input logic        [               7:0] s_ev_cross_type,
    input logic        [8*MOLD_MAX_MSG-1:0] s_ev_raw,

    // Packet events (itch_decoder m_pkt_*)
    input logic                           s_pkt_valid,
    input mold_status_e                   s_pkt_status,
    input logic         [           63:0] s_pkt_seq,
    input logic         [           63:0] s_pkt_gap,
    input logic         [           63:0] s_pkt_sof_ts,
    input logic         [CFG_EPOCH_W-1:0] s_pkt_epoch,

    // ORDER_SENT entries (order_tx_builder m_sent_*)
    input logic                          s_sent_valid,
    input logic       [            31:0] s_sent_user_ref,
    input order_src_e                    s_sent_source,
    input logic       [STRAT_RULE_W-1:0] s_sent_rule,
    input logic       [             7:0] s_sent_side,
    input logic       [            31:0] s_sent_qty,
    input logic       [            31:0] s_sent_price,
    input logic       [            15:0] s_sent_locate,
    input logic       [            63:0] s_sent_stock,
    input logic       [             7:0] s_sent_tif,
    input logic       [           111:0] s_sent_clordid,
    input logic       [            63:0] s_sent_seq,
    input logic       [            63:0] s_sent_sof_ts,
    input logic       [ CFG_EPOCH_W-1:0] s_sent_epoch,
    input logic       [            63:0] s_sent_tx_ts,

    // RISK_REJECT entries (risk_check m_rej_*, ev_rej_dropped)
    input logic                            s_rej_valid,
    input risk_reason_e                    s_rej_reason,
    input order_src_e                      s_rej_source,
    input logic         [STRAT_RULE_W-1:0] s_rej_rule,
    input logic         [             7:0] s_rej_side,
    input logic         [            31:0] s_rej_qty,
    input logic         [            31:0] s_rej_price,
    input logic         [            15:0] s_rej_locate,
    input logic         [            63:0] s_rej_stock,
    input logic         [             7:0] s_rej_tif,
    input logic         [           111:0] s_rej_clordid,
    input logic         [            63:0] s_rej_seq,
    input logic         [            63:0] s_rej_sof_ts,
    input logic         [ CFG_EPOCH_W-1:0] s_rej_epoch,
    input logic         [            63:0] s_rej_ts,
    input logic                            s_rej_lost,

    output logic ring_credit_ret,

    // cdc_async_fifo COMMIT=1, write side
    output logic              fifo_wr_en,
    output logic [DESC_W-1:0] fifo_din,
    output logic              fifo_commit,
    output logic              fifo_rollback,
    input  logic              fifo_full,
    input  logic              fifo_wr_busy,

    output logic [            CNT_W-1:0] cnt_commit_entries,
    output logic [            CNT_W-1:0] cnt_drop_pkts,
    output logic [            CNT_W-1:0] cnt_drop_entries,
    output logic [RING_ORD_QUEUED_W-1:0] cnt_ord_queued
);
  localparam int unsigned PEND_W = $clog2(RING_MAX_PKT_ENTRIES + 1);
  localparam int unsigned OQ = RISK_RING_CREDITS;  // order queue slots
  localparam int unsigned OQ_W = $clog2(OQ);
  localparam int unsigned OVF_BIT = 8 * DESC_FLAGS_OFS + DESC_FLAG_OVERFLOW_BEFORE_BIT;

  logic pkt_accepted, msg_decoded;
  logic [7:0] flags_in;
  logic       empty_q;  // no entry of the current packet has come in yet
  logic       overflow_q;  // a loss whose flag has not gone out on a committed entry
  // A packet with GAP and EOS: the EOS entry goes in the cycle after the event.
  logic       eos_q;
  logic [63:0] eos_seq_q, eos_sof_ts_q;
  logic [CFG_EPOCH_W-1:0] eos_epoch_q;

  // ===================== Order queue =====================
  // Two banks of OQ/2 (even and odd slots): the two entries of one cycle go to consecutive slots, so each bank
  // has one write port and stays distributed RAM. The packing is wires, at the read.
  typedef struct packed {
    logic                    rej;      // 0 ORDER_SENT, 1 RISK_REJECT
    logic [31:0]             word;     // user_ref, or the reason
    logic [1:0]              source;
    logic [STRAT_RULE_W-1:0] rule;
    logic [7:0]              side;
    logic [7:0]              tif;
    logic [15:0]             locate;
    logic [31:0]             qty;
    logic [31:0]             price;
    logic [63:0]             stock;
    logic [63:0]             ts;
    logic [111:0]            clordid;
    logic [63:0]             seq;
    logic [63:0]             sof_ts;
    logic [CFG_EPOCH_W-1:0]  epoch;
  } ord_slot_t;

  (* ram_style = "distributed" *)ord_slot_t          bank0                                                         [OQ/2];
  (* ram_style = "distributed" *)ord_slot_t          bank1                                                         [OQ/2];
  logic      [OQ-1:0] lost_q;  // a lost reject stands right after this slot's entry
  logic [OQ_W-1:0] wp_q, rp_q, wp_rej, pick_slot;
  logic [RING_ORD_QUEUED_W-1:0] oq_n_q;
  logic lost_pend_q;  // a lost reject at the head of the queue (no entry ahead of it)
  ord_slot_t sent_in, rej_in, ord_rd;
  logic [DESC_W-1:0] ord_desc;
  logic s1_ord_q, ord_ok, ord_fail, ord_free, ord_pick, lost_head, lost_take, mark_sent, mark_tail;

  assign sent_in = '{
          rej: 1'b0,
          word: s_sent_user_ref,
          source: s_sent_source,
          rule: s_sent_rule,
          side: s_sent_side,
          tif: s_sent_tif,
          locate: s_sent_locate,
          qty: s_sent_qty,
          price: s_sent_price,
          stock: s_sent_stock,
          ts: s_sent_tx_ts,
          clordid: s_sent_clordid,
          seq: s_sent_seq,
          sof_ts: s_sent_sof_ts,
          epoch: s_sent_epoch
      };
  assign rej_in = '{
          rej: 1'b1,
          word: 32'(s_rej_reason),
          source: s_rej_source,
          rule: s_rej_rule,
          side: s_rej_side,
          tif: s_rej_tif,
          locate: s_rej_locate,
          qty: s_rej_qty,
          price: s_rej_price,
          stock: s_rej_stock,
          ts: s_rej_ts,
          clordid: s_rej_clordid,
          seq: s_rej_seq,
          sof_ts: s_rej_sof_ts,
          epoch: s_rej_epoch
      };
  assign wp_rej = wp_q + OQ_W'(s_sent_valid);

  always_ff @(posedge clk) begin
    if (s_sent_valid && !wp_q[0]) begin
      bank0[wp_q[OQ_W-1:1]] <= sent_in;
    end
    if (s_sent_valid && wp_q[0]) begin
      bank1[wp_q[OQ_W-1:1]] <= sent_in;
    end
    if (s_rej_valid && !wp_rej[0]) begin
      bank0[wp_rej[OQ_W-1:1]] <= rej_in;
    end
    if (s_rej_valid && wp_rej[0]) begin
      bank1[wp_rej[OQ_W-1:1]] <= rej_in;
    end
  end

  // The entry in stage 1 is always the oldest (rp_q); the one taken now is the next: rp_q + s1_ord_q, kept in
  // its own register (pick_q, set from both next values) so the queue's read address comes from a flop. The
  // adder after s1_ord_q (fanout 130 into the LUTRAM address) failed the A7 at 156.25 MHz (-0.052 ns, S9).
  logic [OQ_W-1:0] pick_q;
  assign pick_slot = pick_q;
  assign ord_rd = pick_slot[0] ? bank1[pick_slot[OQ_W-1:1]] : bank0[pick_slot[OQ_W-1:1]];
  assign ord_desc = ord_rd.rej ? desc_pack_risk_reject(
      ord_rd.seq,
      ord_rd.sof_ts,
      8'h00,
      8'(ord_rd.epoch),
      ord_rd.word[7:0],
      8'(ord_rd.source),
      8'(ord_rd.rule),
      ord_rd.side,
      ord_rd.tif,
      ord_rd.locate,
      ord_rd.qty,
      ord_rd.price,
      ord_rd.stock,
      ord_rd.ts,
      ord_rd.clordid
  ) : desc_pack_order_sent(
      ord_rd.seq,
      ord_rd.sof_ts,
      8'h00,
      8'(ord_rd.epoch),
      ord_rd.word,
      8'(ord_rd.source),
      8'(ord_rd.rule),
      ord_rd.side,
      ord_rd.tif,
      ord_rd.locate,
      ord_rd.qty,
      ord_rd.price,
      ord_rd.stock,
      ord_rd.ts,
      ord_rd.clordid
  );

  assign ord_ok = s1_ord_q && !fifo_full && !fifo_wr_busy;
  assign ord_fail = s1_ord_q && !ord_ok;
  // A cycle where an order entry could be taken: nothing of a packet in stage 0 and no packet open.
  assign ord_free = !eos_q && !s_ev_valid && !s_pkt_valid && empty_q;
  assign ord_pick = ord_free && oq_n_q > RING_ORD_QUEUED_W'(s1_ord_q);
  // A lost reject reaches the head: pending, or right behind the entry written now. In a free cycle it is
  // "taken": the next committed entry has the flag.
  assign lost_head = lost_pend_q || (ord_ok && lost_q[rp_q]);
  assign lost_take = lost_head && ord_free;
  // Where a lost reject of this cycle stands: behind this cycle's ORDER_SENT, behind the queue's last entry
  // (unless that one is written now), or at the head.
  assign mark_sent = s_rej_lost && s_sent_valid;
  assign mark_tail = s_rej_lost && !s_sent_valid && oq_n_q != '0 && !(oq_n_q == RING_ORD_QUEUED_W'(1) && ord_ok);

  assign pkt_accepted = s_pkt_status inside {MOLD_OK, MOLD_HEARTBEAT, MOLD_EOS};
  assign msg_decoded = s_ev_class inside {ITCH_ORDER, ITCH_TRADE};
  always_comb begin
    flags_in                                = '0;
    // An order entry written now takes a pending flag before this message's packet does.
    flags_in[DESC_FLAG_OVERFLOW_BEFORE_BIT] = overflow_q && empty_q && !ord_ok;
  end

  // ===================== Stage 0 -> 1: which entry =====================
  logic s1_valid, s1_commit, s1_rollback, s1_late;
  logic [DESC_W-1:0] s1_din;

  logic next_valid, next_commit, next_rollback, next_eos;
  logic [DESC_W-1:0] next_din;

  always_comb begin
    next_valid    = 1'b0;
    next_commit   = 1'b0;
    next_rollback = 1'b0;
    next_eos      = 1'b0;
    next_din      = desc_pack_eos(eos_seq_q, eos_sof_ts_q, 8'h00, eos_epoch_q);
    if (eos_q) begin
      next_valid  = 1'b1;
      next_commit = 1'b1;
    end else if (s_ev_valid) begin
      next_valid = 1'b1;
      next_din = msg_decoded ? desc_pack_itch(
        s_ev_seq,
        s_ev_sof_ts,
        flags_in,
        s_ev_epoch,
        8'(s_ev_class),
        s_ev_type,
        8'(s_ev_len),
        s_ev_locate,
        s_ev_tracking,
        s_ev_ts,
        s_ev_side,
        s_ev_printable,
        s_ev_cross_type,
        s_ev_price,
        s_ev_attribution,
        s_ev_order_ref,
        s_ev_new_ref,
        s_ev_shares,
        s_ev_match,
        s_ev_stock
      ) : desc_pack_itch_raw(
        s_ev_seq,
        s_ev_sof_ts,
        flags_in,
        s_ev_epoch,
        8'(s_ev_class),
        8'(s_ev_len),
        s_ev_raw[8*DESC_ITCH_RAW_RAW_LO_SIZE-1:0],
        s_ev_raw[8*MOLD_MAX_MSG-1:8*DESC_ITCH_RAW_RAW_LO_SIZE]
      );
    end else if (s_pkt_valid) begin
      if (!pkt_accepted) begin
        next_rollback = 1'b1;
      end else if (s_pkt_gap != '0) begin
        next_valid = 1'b1;
        next_eos = s_pkt_status == MOLD_EOS;
        next_commit = !next_eos;
        next_din = desc_pack_gap(s_pkt_seq - s_pkt_gap, s_pkt_sof_ts, flags_in, s_pkt_epoch,
                                 s_pkt_seq, s_pkt_gap);
      end else if (s_pkt_status == MOLD_EOS) begin
        next_valid  = 1'b1;
        next_commit = 1'b1;
        next_din    = desc_pack_eos(s_pkt_seq, s_pkt_sof_ts, flags_in, s_pkt_epoch);
      end else begin
        next_commit = 1'b1;
      end
    end else if (ord_pick) begin
      next_din = ord_desc;  // its flag is added in stage 1
    end
  end

  always_ff @(posedge clk) begin
    s1_din <= next_din;
    if (s_pkt_valid) begin
      eos_seq_q    <= s_pkt_seq;
      eos_sof_ts_q <= s_pkt_sof_ts;
      eos_epoch_q  <= s_pkt_epoch;
    end
    if (rst) begin
      s1_valid    <= 1'b0;
      s1_commit   <= 1'b0;
      s1_rollback <= 1'b0;
      s1_late     <= 1'b0;
      s1_ord_q    <= 1'b0;
      eos_q       <= 1'b0;
      empty_q     <= 1'b1;
    end else begin
      s1_valid    <= next_valid;
      s1_commit   <= next_commit;
      s1_rollback <= next_rollback;
      s1_late     <= eos_q;
      s1_ord_q    <= ord_pick && !ord_fail;
      eos_q       <= next_eos;
      if (s_pkt_valid) begin
        empty_q <= 1'b1;
      end else if (s_ev_valid) begin
        empty_q <= 1'b0;
      end
    end
  end

  // ===================== Stage 1: the FIFO =====================
  logic dropping, drop_q;
  logic [PEND_W-1:0] pend_q, pend_now;  // entries of the current packet, the dropped ones included
  // What the counters take: a commit that came with the event waits 1 cycle for one that came after it.
  logic count_q, count_drop_q, count_now, count_drop;
  logic [PEND_W-1:0] count_n_q, count_n, commit_n;
  logic overflow_n;

  assign dropping       = drop_q || (s1_valid && (fifo_full || fifo_wr_busy));
  assign pend_now       = pend_q + PEND_W'(s1_valid);
  assign fifo_wr_en     = (s1_valid && !dropping) || ord_ok;
  assign fifo_din       = s1_din | (DESC_W'(s1_ord_q && overflow_q) << OVF_BIT);
  assign fifo_commit    = (s1_commit && !dropping) || ord_ok;
  assign fifo_rollback  = s1_rollback || (s1_commit && dropping);
  assign count_now      = s1_commit && s1_late;
  assign count_drop     = count_now ? dropping : count_drop_q;
  assign count_n        = count_now ? pend_now : count_n_q;
  assign commit_n       = ((count_q || count_now) && !count_drop) ? count_n : '0;
  assign cnt_ord_queued = oq_n_q;

  always_comb begin
    overflow_n = overflow_q;
    if (s1_commit) begin
      // A commit without entries (a heartbeat) leaves a pending flag for the next entry.
      overflow_n = dropping || (overflow_q && pend_now == '0);
    end
    if (ord_ok) begin
      overflow_n = 1'b0;  // the order entry has taken it
    end
    if (lost_take) begin
      overflow_n = 1'b1;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      drop_q             <= 1'b0;
      pend_q             <= '0;
      overflow_q         <= 1'b0;
      count_q            <= 1'b0;
      cnt_commit_entries <= '0;
      cnt_drop_pkts      <= '0;
      cnt_drop_entries   <= '0;
      wp_q               <= '0;
      rp_q               <= '0;
      pick_q             <= '0;
      oq_n_q             <= '0;
      lost_pend_q        <= 1'b0;
      ring_credit_ret    <= 1'b0;
    end else begin
      if (s1_commit || s1_rollback) begin
        drop_q <= 1'b0;
        pend_q <= '0;
      end else begin
        drop_q <= dropping;
        pend_q <= pend_now;
      end
      overflow_q         <= overflow_n;
      count_q            <= s1_commit && !s1_late;
      cnt_commit_entries <= cnt_commit_entries + CNT_W'(commit_n) + CNT_W'(ord_ok);
      if ((count_q || count_now) && count_drop) begin
        cnt_drop_pkts    <= cnt_drop_pkts + CNT_W'(1);
        cnt_drop_entries <= cnt_drop_entries + CNT_W'(count_n);
      end
      wp_q <= wp_rej + OQ_W'(s_rej_valid);
      rp_q <= rp_q + OQ_W'(ord_ok);
      pick_q <= rp_q + OQ_W'(ord_ok) + OQ_W'(ord_pick && !ord_fail);  // next rp_q + next s1_ord_q
      oq_n_q          <= oq_n_q + RING_ORD_QUEUED_W'(s_sent_valid) + RING_ORD_QUEUED_W'(s_rej_valid) - RING_ORD_QUEUED_W'(ord_ok);
      lost_pend_q <= (lost_head && !lost_take) || (s_rej_lost && !mark_sent && !mark_tail);
      ring_credit_ret <= ord_ok;
    end
  end

  // Lost marks: set with the ORDER_SENT they follow or on the last queued entry; cleared when a slot is reused.
  always_ff @(posedge clk) begin
    if (s_sent_valid) begin
      lost_q[wp_q] <= mark_sent;
    end
    if (s_rej_valid) begin
      lost_q[wp_rej] <= 1'b0;
    end
    if (mark_tail) begin
      lost_q[wp_q-OQ_W'(1)] <= 1'b1;
    end
  end

  // Datapath: no reset (only used after count_q has been set).
  always_ff @(posedge clk) begin
    count_drop_q <= dropping;
    count_n_q    <= pend_now;
  end
endmodule
