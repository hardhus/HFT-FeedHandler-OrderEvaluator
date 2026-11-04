// event_packer: turns itch_decoder's output messages and packet events into 128-byte ring entries and
// delivers them whole per packet through a COMMIT=1 cdc_async_fifo.
// Clock domain : clk (net_clk)
// Latency      : s_ev_valid -> fifo_wr_en 1 cycle; s_pkt_valid -> fifo_commit or fifo_rollback 1 cycle,
//                2 for a packet with both a GAP and an EOS entry
// Throughput   : one message per cycle, no backpressure
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
// Capacity     : a packet has at most RING_MAX_PKT_ENTRIES entries: the FIFO must hold that many, or it
//                always drops.
// Counters     : CNT_W bits each, wrapping, cleared only by rst. Registers: a reader that samples them on
//                one clock edge gets the same cycle of all three.
// Reset        : rst clears everything. Reset the FIFO with it (entries of a packet cut by it are gone).
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

    // cdc_async_fifo COMMIT=1, write side
    output logic              fifo_wr_en,
    output logic [DESC_W-1:0] fifo_din,
    output logic              fifo_commit,
    output logic              fifo_rollback,
    input  logic              fifo_full,
    input  logic              fifo_wr_busy,

    output logic [CNT_W-1:0] cnt_commit_entries,
    output logic [CNT_W-1:0] cnt_drop_pkts,
    output logic [CNT_W-1:0] cnt_drop_entries
);
  localparam int unsigned PEND_W = $clog2(RING_MAX_PKT_ENTRIES + 1);

  logic pkt_accepted, msg_decoded;
  logic [7:0] flags_in;
  logic       empty_q;  // no entry of the current packet has come in yet
  logic       overflow_q;  // a drop whose flag has not gone out on a committed entry
  // A packet with GAP and EOS: the EOS entry goes in the cycle after the event.
  logic       eos_q;
  logic [63:0] eos_seq_q, eos_sof_ts_q;
  logic [CFG_EPOCH_W-1:0] eos_epoch_q;

  assign pkt_accepted = s_pkt_status inside {MOLD_OK, MOLD_HEARTBEAT, MOLD_EOS};
  assign msg_decoded  = s_ev_class inside {ITCH_ORDER, ITCH_TRADE};
  always_comb begin
    flags_in                                = '0;
    flags_in[DESC_FLAG_OVERFLOW_BEFORE_BIT] = overflow_q && empty_q;
  end

  // ===================== Stage 0 -> 1: which entry =====================
  logic s1_valid, s1_commit, s1_rollback;
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
      eos_q       <= 1'b0;
      empty_q     <= 1'b1;
    end else begin
      s1_valid    <= next_valid;
      s1_commit   <= next_commit;
      s1_rollback <= next_rollback;
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

  assign dropping      = drop_q || (s1_valid && (fifo_full || fifo_wr_busy));
  assign pend_now      = pend_q + PEND_W'(s1_valid);
  assign fifo_wr_en    = s1_valid && !dropping;
  assign fifo_din      = s1_din;
  assign fifo_commit   = s1_commit && !dropping;
  assign fifo_rollback = s1_rollback || (s1_commit && dropping);

  always_ff @(posedge clk) begin
    if (rst) begin
      drop_q             <= 1'b0;
      pend_q             <= '0;
      overflow_q         <= 1'b0;
      cnt_commit_entries <= '0;
      cnt_drop_pkts      <= '0;
      cnt_drop_entries   <= '0;
    end else begin
      if (s1_commit || s1_rollback) begin
        drop_q <= 1'b0;
        pend_q <= '0;
      end else begin
        drop_q <= dropping;
        pend_q <= pend_now;
      end
      if (s1_commit) begin
        if (dropping) begin
          overflow_q       <= 1'b1;
          cnt_drop_pkts    <= cnt_drop_pkts + CNT_W'(1);
          cnt_drop_entries <= cnt_drop_entries + CNT_W'(pend_now);
        end else begin
          // A commit without entries (a heartbeat) leaves a pending flag for the next entry.
          overflow_q         <= overflow_q && pend_now == '0;
          cnt_commit_entries <= cnt_commit_entries + CNT_W'(pend_now);
        end
      end
    end
  end
endmodule
