// hft_csr_wrap: host CSR block (generated hft_csr) and its transfers to the net clock domain.
// Clock domain : host_clk (s_axil_*, hft_csr, host_ring_*), net_clk (cfg_*, stat_*, mpkt_*, mres_*, feed_*,
//                sym_*, cnt_ring_*, rx_stats, mold_stats, itch_stats, ctl_*, cmd_*, loc_*, m_host_*, ord_*,
//                st_*, ev_*, order_stats)
// Config commit: the NET registers are a shadow. A NET_COMMIT pulse copies the whole shadow into a
//                commit buffer and requests one cdc_handshake transfer; the receiver's cfg_* then
//                change together in one net_clk cycle (eth_ip_udp_rx samples them per frame).
//                A commit while a transfer is in flight is not lost: the buffer takes the newer
//                shadow and is sent when the handshake is free (commits coalesce, the newest wins,
//                always as a whole: writes after a commit never reach cfg_* without a new commit).
//                NET_STATUS.commit_busy = commit pulse, request pending or handshake busy; when it
//                reads 0, the last commit is in cfg_*.
// Latency      : NET_COMMIT pulse -> handshake acceptance 2 host_clk edges (handshake free);
//                acceptance -> cfg_* changed STAGES+1 net_clk edges (+1 with CDC jitter): cfg_* are
//                the handshake's destination register, no extra stage
// Snapshot     : a STATS_CTRL.snapshot pulse sends a request to the net domain (cdc_handshake); there
//                the live rx_stats counters are handed to a second cdc_handshake, whose acceptance
//                edge takes all of them in one net_clk cycle (an event sampled on that edge is in
//                the next snapshot). When they land in host_clk, STATS_SEQ increments and the STATS
//                counter registers read them (no copy: the destination register holds them until
//                the next snapshot). Requests while one is in flight coalesce; none is lost: a
//                snapshot taken after the last request always lands. Counters read 0 until the
//                first snapshot.
// Latency      : snapshot pulse -> request accepted 1 host_clk edge; request at net (dst_valid) ->
//                counters taken 1 net_clk edge (handshakes free)
// Feed commit  : the FEED registers work like NET, over their own cdc_handshake (moldudp64_rx samples
//                cfg_* at each packet's first beat). FEED_COMMIT.resync also commits and sets the
//                buffer's resync bit; the bit stays set until a transfer carries it, so a resync
//                requested while one is pending or in flight merges with it and is never lost. At
//                net, cfg_resync is a 1-cycle pulse in the cycle the transfer lands. The resync
//                request is the rising edge of its singlepulse field: a pulse held one more cycle by
//                regblock must not make a second transfer resync again (a resync is not idempotent).
//                FEED_STATUS.commit_busy as NET_STATUS. ITCH_CTRL is part of the FEED transfer
//                (cfg_class_en, cfg_class_filter change with the other FEED cfg_*). The snapshot
//                also takes the mold_stats and itch_stats counters and the feed state in the same
//                net_clk cycle. Both counter banks count the packet events itch_decoder forwards
//                (mpkt_*, with its results mres_*). They go through STATS_DELAY = 2 registers first,
//                so that a packet event moves those banks in the same cycle as cnt_ring_* (3 cycles
//                after it, event_packer). feed_* go through FEED_DELAY = 6 registers, so that the
//                state belongs to the same packet events as the counters: moldudp64_rx shows a
//                packet's feed update 1 cycle before its own packet event, itch_decoder forwards
//                that event 2 cycles later, and the banks count it 3 cycles after that.
//                The registers add no delay to any entry: only to the counters.
// Epoch        : each FEED_COMMIT write with commit or resync set adds 1 to an 8-bit count, kept in the
//                FEED configuration, so cfg_epoch changes with the other FEED cfg_* (like them, undefined
//                until the first commit lands, when no message is output). The host keeps the same
//                count, so writes count one by one: the cycle after a write with byte 0 enabled
//                (swmod), with a request field high. Writes in consecutive cycles count each; a pulse
//                held one more cycle by a write with byte 0 disabled does not.
// Ring         : the RING registers are event_ring_writer's: host_clk, no transfer, no commit.
//                RING_WRITTEN and RING_BRESP_ERR are live (the host reads HI, LO, HI); the
//                cnt_ring_* counters are in the snapshot.
// Symbols      : SYMBOL (regblock external mem) accesses go to itch_decoder's command port: the
//                request (write flag, word address, data, byte strobes) over one cdc_handshake,
//                the response (read data) back over another. rd_ack/wr_ack come when the response
//                lands, so BRESP means the write is in the bitmap. regblock keeps one access in
//                flight. During the bitmap sweep after reset the command waits at net
//                (sym_req_ready = 0), and so does the bus.
// Order cmds   : RULE_COMMIT, RULE_ARM, RULE_DISARM, RISK_COMMIT, BUDGET_ADD, TX_COMMIT, TX_USERREF_NEXT,
//                TX_USERREF (read) and LOCATE_ALLOW are regblock external registers: one command channel in bus order (decision
//                33). regblock holds every other access while one is in flight, and the write (or read)
//                response comes when the command's response lands back in host_clk. A write with the commit
//                bit 0 (RULE/RISK/TX_COMMIT) is answered at once and sends nothing. The fence (decision 36):
//                the host side sends a command only when the mailbox FIFO has been read empty (every
//                MBOX_SEND before it taken by risk_check); the net side applies it only when no host order
//                was taken in the last 3 cycles (each one decided) and the synchronized kill and tx_en
//                equal the host's values the command carries (written before it, so in effect first). It
//                then pulses one cfg_*/cmd_* (or does the LOCATE_ALLOW access on loc_*) and answers after
//                CMD_SETTLE more cycles, with the events of those cycles (ev_cfg_refused: decided 2 edges
//                after the write, decision 27; ev_userref_ignored) and st_error, st_tx_cfg_valid and
//                st_userref. TX_COMMIT is refused (no cfg_tx_wr) unless the path is closed: ctl_kill or
//                !ctl_tx_en (decision 37). The fence never waits for the TX queue: with tready stuck the host
//                could not even write kill (decision 36).
// Status       : RULE_STATUS.error and TX_STATUS.cfg_valid are as of the last response; TX_STATUS.cfg_refused
//                and userref_ignored are the result of the last command of their kind (decision 34).
// Controls     : RISK_CTRL.kill and TX_CTRL.tx_en reach net_clk through sync_bit (decision 32), STAGES edges
//                after the register changes. ctl_kill is 1 and ctl_tx_en 0 during net_rst and STAGES + 1
//                cycles after it (the synchronizers have no reset); ctl_tx_en also needs st_tx_cfg_valid
//                (decision 26). A kill write waits on the bus behind a command in flight (decision 38).
// Mailbox      : MBOX_SEND (swmod: each write once) copies the MBOX_* shadow into a cdc_async_fifo (16, COMMIT
//                = 0) to net_clk, or drops it when the FIFO is full or busy: MBOX_STATUS.dropped (the last
//                send's result, decision 34) and MBOX_DROP (live, host_clk, decision 35). MBOX_SPACE = 16 -
//                wr_count. At net the FIFO's head is m_host_* (valid = not empty; ready pops it).
// Order stats  : order_stats counts ord_fire_* (RULE_FIRED_<n>), ord_sent_* (ORDERS_SENT_*), ord_rej_* and
//                ord_lost_* (RISK_REJECT_<reason>, RING_DROP_ORDERS). The snapshot takes them, ord_queued
//                (RING_ORDER_QUEUED), st_armed (RULE_ARMED) and st_budget (BUDGET) in the same net_clk cycle as
//                the other banks. ord_sent_*, ord_rej_*, ord_lost_* and ord_queued must be event_packer's own
//                order inputs and its cnt_ord_queued (order_stats header): no extra delay (audit/14 S7 A5).
// Reset        : host_rst and net_rst asserted together (one source, one reset_sync per domain,
//                cdc_handshake contract). After reset cfg_rx_en, cfg_feed_en, cfg_session_check,
//                cfg_resync and cfg_class_en are 0; the other cfg_* are undefined until the first
//                commit of their
//                block (the receiver drops every frame while rx_en = 0, the feed counts every packet
//                as DISABLED while feed_en = 0).
module hft_csr_wrap
  import hft_pkg::*;
#(
    localparam int unsigned N_FILTERS = 8,  // = NET.FILTER[] in hft_csr.rdl (checked below)
    localparam int unsigned STAGES    = 2
) (
    input logic host_clk,
    input logic host_rst,

    input  logic        s_axil_awvalid,
    output logic        s_axil_awready,
    input  logic [15:0] s_axil_awaddr,
    input  logic [ 2:0] s_axil_awprot,
    input  logic        s_axil_wvalid,
    output logic        s_axil_wready,
    input  logic [31:0] s_axil_wdata,
    input  logic [ 3:0] s_axil_wstrb,
    output logic        s_axil_bvalid,
    input  logic        s_axil_bready,
    output logic [ 1:0] s_axil_bresp,
    input  logic        s_axil_arvalid,
    output logic        s_axil_arready,
    input  logic [15:0] s_axil_araddr,
    input  logic [ 2:0] s_axil_arprot,
    output logic        s_axil_rvalid,
    input  logic        s_axil_rready,
    output logic [31:0] s_axil_rdata,
    output logic [ 1:0] s_axil_rresp,

    input logic net_clk,
    input logic net_rst,

    output logic                       cfg_rx_en,
    output logic [         47:0]       cfg_local_mac,
    output logic                       cfg_accept_bcast,
    output logic [N_FILTERS-1:0][31:0] cfg_filter_ip,
    output logic [N_FILTERS-1:0][15:0] cfg_filter_port,
    output logic [N_FILTERS-1:0]       cfg_filter_en,

    input logic              stat_valid,
    input rx_reason_e        stat_reason,
    input logic       [15:0] stat_bytes,

    // moldudp64_rx configuration
    output logic                 cfg_feed_en,
    output logic                 cfg_session_check,
    output logic [         79:0] cfg_session,
    output logic [N_FILTERS-1:0] cfg_filter_mask,
    output logic                 cfg_resync,
    output logic                 cfg_resync_learn,
    output logic [         63:0] cfg_resync_seq,

    // itch_decoder configuration (through moldudp64_rx's per-packet context)
    output logic [ITCH_N_CLASS-1:0] cfg_class_en,
    output logic [ITCH_N_CLASS-1:0] cfg_class_filter,
    output logic [ CFG_EPOCH_W-1:0] cfg_epoch,

    // event_ring_writer, host_clk
    output logic        host_ring_enable,
    output logic [63:0] host_ring_base,
    output logic [ 4:0] host_ring_size_log2,
    output logic [31:0] host_ring_cons,
    input  logic [31:0] host_ring_prod_idx,
    input  logic        host_ring_bresp_err,
    input  logic        host_ring_enable_err,
    input  logic [63:0] host_ring_cnt_written,
    input  logic [63:0] host_ring_cnt_bresp_err,

    // event_packer counters, net_clk
    input logic [63:0] cnt_ring_commit_entries,
    input logic [63:0] cnt_ring_drop_pkts,
    input logic [63:0] cnt_ring_drop_entries,

    // Packet events as itch_decoder forwards them, and its message results
    input logic                 mpkt_valid,
    input mold_status_e         mpkt_status,
    input logic          [63:0] mpkt_gap,
    input logic          [15:0] mpkt_msgs,
    input logic          [15:0] mpkt_dup_msgs,
    input logic          [15:0] mpkt_oversize,
    input logic                 mres_valid,
    input itch_class_e          mres_class,
    input itch_outcome_e        mres_outcome,

    // moldudp64_rx feed state (a packet's update 1 cycle before moldudp64_rx's packet event)
    input logic [63:0] feed_expected_seq,
    input logic        feed_synced,
    input logic        feed_eos,

    // itch_decoder symbol bitmap command port
    output logic        sym_req_valid,
    input  logic        sym_req_ready,
    output logic        sym_req_wr,
    output logic [10:0] sym_req_addr,
    output logic [31:0] sym_req_wdata,
    output logic [ 3:0] sym_req_wstrb,
    input  logic        sym_rsp_valid,
    input  logic [31:0] sym_rsp_rdata,

    // Order path controls, to strategy_engine and risk_check (net_clk)
    output logic ctl_kill,
    output logic ctl_tx_en,

    // strategy_engine configuration and status
    output logic                            cfg_rule_wr,
    output logic        [ STRAT_RULE_W-1:0] cfg_rule_idx,
    output strat_rule_t                     cfg_rule,
    output logic                            cmd_arm,
    output logic        [STRAT_N_RULES-1:0] cmd_arm_mask,
    output logic                            cmd_disarm,
    output logic        [STRAT_N_RULES-1:0] cmd_disarm_mask,
    input  logic        [STRAT_N_RULES-1:0] st_armed,
    input  logic        [STRAT_N_RULES-1:0] st_error,

    // risk_check configuration, commands and status
    output logic        cfg_lim_wr,
    output logic [31:0] cfg_max_qty,
    output logic [63:0] cfg_max_notional,
    output logic [31:0] cfg_rate_refill,
    output logic [31:0] cfg_rate_period,
    output logic [31:0] cfg_rate_burst,
    output logic        cmd_budget_add,
    output logic [31:0] cmd_budget_amount,
    output logic        cmd_userref_wr,
    output logic [31:0] cmd_userref_value,
    input  logic        ev_userref_ignored,
    input  logic [31:0] st_userref,          // risk_check m_tx_user_ref
    input  logic [31:0] st_budget,

    // risk_check allow bitmap command port (as sym_*)
    output logic        loc_req_valid,
    input  logic        loc_req_ready,
    output logic        loc_req_wr,
    output logic [10:0] loc_req_addr,
    output logic [31:0] loc_req_wdata,
    output logic [ 3:0] loc_req_wstrb,
    input  logic        loc_rsp_valid,
    input  logic [31:0] loc_rsp_rdata,

    // order_tx_builder configuration and status
    output logic            cfg_tx_wr,
    output logic [8*42-1:0] cfg_template,
    output logic [     7:0] cfg_display,
    output logic [     7:0] cfg_capacity,
    output logic [     7:0] cfg_ise,
    output logic [     7:0] cfg_cross_type,
    input  logic            st_tx_cfg_valid,
    input  logic            ev_cfg_refused,

    // Host orders from the mailbox, to risk_check s_host_*
    output logic         m_host_valid,
    input  logic         m_host_ready,
    output logic [  7:0] m_host_side,
    output logic [ 31:0] m_host_qty,
    output logic [ 31:0] m_host_price,
    output logic [ 15:0] m_host_locate,
    output logic [ 63:0] m_host_stock,
    output logic [  7:0] m_host_tif,
    output logic [111:0] m_host_clordid,

    // Order counters' events (= event_packer's order inputs) and its queue level
    input logic                                 ord_fire_valid,   // strategy_engine m_ord_*
    input logic         [     STRAT_RULE_W-1:0] ord_fire_rule,
    input logic                                 ord_sent_valid,   // order_tx_builder m_sent_*
    input order_src_e                           ord_sent_source,
    input logic                                 ord_rej_valid,    // risk_check m_rej_*
    input risk_reason_e                         ord_rej_reason,
    input logic                                 ord_lost_valid,   // risk_check ev_rej_dropped*
    input risk_reason_e                         ord_lost_reason,
    input logic         [RING_ORD_QUEUED_W-1:0] ord_queued        // event_packer cnt_ord_queued
);
  // One NET configuration as it crosses the clock domains.
  typedef struct packed {
    logic                       rx_en;
    logic                       accept_bcast;
    logic [47:0]                local_mac;
    logic [N_FILTERS-1:0][31:0] filter_ip;
    logic [N_FILTERS-1:0][15:0] filter_port;
    logic [N_FILTERS-1:0]       filter_en;
  } net_cfg_t;

  // One FEED configuration (with ITCH_CTRL), and with a resync request as it crosses the clock
  // domains.
  typedef struct packed {
    logic                    enable;
    logic                    session_check;
    logic                    resync_learn;
    logic [N_FILTERS-1:0]    filter_mask;
    logic [79:0]             session;
    logic [63:0]             resync_seq;
    logic [ITCH_N_CLASS-1:0] class_en;
    logic [ITCH_N_CLASS-1:0] class_filter;
    logic [CFG_EPOCH_W-1:0]  epoch;
  } feed_set_t;

  // One SYMBOL access as it crosses to net.
  typedef struct packed {
    logic        wr;
    logic [10:0] addr;   // word address
    logic [31:0] wdata;
    logic [3:0]  wstrb;
  } sym_req_t;

  typedef struct packed {
    feed_set_t set;
    logic      resync;
  } feed_cfg_t;

  hft_csr_pkg::hft_csr__in_t              hwif_in;
  hft_csr_pkg::hft_csr__out_t             hwif_out;
  // RISK, STRATEGY and TX: hft_order_csr (decision 41)
  hft_order_csr_pkg::hft_order_csr__in_t  ohwif_in;
  hft_order_csr_pkg::hft_order_csr__out_t ohwif_out;

  if ($size(hwif_out.NET.FILTER) != N_FILTERS) begin : g_check
    $error("hft_csr_wrap: N_FILTERS differs from NET.FILTER[] in hft_csr.rdl");
  end

  hft_csr_regs u_csr (
      .clk           (host_clk),
      .rst           (host_rst),
      .s_axil_awready(s_axil_awready),
      .s_axil_awvalid(s_axil_awvalid),
      .s_axil_awaddr (s_axil_awaddr),
      .s_axil_awprot (s_axil_awprot),
      .s_axil_wready (s_axil_wready),
      .s_axil_wvalid (s_axil_wvalid),
      .s_axil_wdata  (s_axil_wdata),
      .s_axil_wstrb  (s_axil_wstrb),
      .s_axil_bready (s_axil_bready),
      .s_axil_bvalid (s_axil_bvalid),
      .s_axil_bresp  (s_axil_bresp),
      .s_axil_arready(s_axil_arready),
      .s_axil_arvalid(s_axil_arvalid),
      .s_axil_araddr (s_axil_araddr),
      .s_axil_arprot (s_axil_arprot),
      .s_axil_rready (s_axil_rready),
      .s_axil_rvalid (s_axil_rvalid),
      .s_axil_rdata  (s_axil_rdata),
      .s_axil_rresp  (s_axil_rresp),
      .hwif_in       (hwif_in),
      .hwif_out      (hwif_out),
      .ohwif_in      (ohwif_in),
      .ohwif_out     (ohwif_out)
  );

  // ---- config commit (host_clk) ----
  net_cfg_t shadow, commit_buf_q;
  logic commit, commit_pending_q, cfg_src_ready;

  always_comb begin
    shadow.rx_en = hwif_out.NET.NET_CTRL.rx_en.value;
    shadow.accept_bcast = hwif_out.NET.NET_CTRL.accept_bcast.value;
    shadow.local_mac = {hwif_out.NET.LOCAL_MAC_HI.mac.value, hwif_out.NET.LOCAL_MAC_LO.mac.value};
    for (int i = 0; i < N_FILTERS; i++) begin
      shadow.filter_ip[i]   = hwif_out.NET.FILTER[i].IP.ip.value;
      shadow.filter_port[i] = hwif_out.NET.FILTER[i].PORT_EN.port.value;
      shadow.filter_en[i]   = hwif_out.NET.FILTER[i].PORT_EN.en.value;
    end
  end

  // NET_COMMIT is level-sensitive here: regblock can hold the pulse one more cycle (a following
  // write with byte 0 disabled), which only repeats the same request.
  assign commit = hwif_out.NET.NET_COMMIT.commit.value;

  always_ff @(posedge host_clk) begin
    if (host_rst) begin
      commit_pending_q <= 1'b0;
    end else begin
      commit_pending_q <= commit || (commit_pending_q && !cfg_src_ready);
    end
  end

  // Datapath: no reset (only sent after a commit has loaded it).
  always_ff @(posedge host_clk) begin
    if (commit) begin
      commit_buf_q <= shadow;
    end
  end

  // commit itself counts: a read dispatched in the pulse cycle (AXI orders no read after a write)
  // must not see 0 before the request is pending.
  assign hwif_in.NET.NET_STATUS.commit_busy.next = commit || commit_pending_q || !cfg_src_ready;

  // ---- feed commit (host_clk) ----
  feed_set_t feed_shadow, feed_set_q;
  logic feed_commit, feed_resync, feed_resync_q, feed_resync_req, feed_req;
  logic feed_pending_q, feed_src_ready, feed_resync_bit_q;
  logic feed_write_q;
  logic [CFG_EPOCH_W-1:0] epoch_q, epoch_next;

  always_comb begin
    feed_shadow.enable = hwif_out.FEED.FEED_CTRL.enable.value;
    feed_shadow.session_check = hwif_out.FEED.FEED_CTRL.session_check.value;
    feed_shadow.resync_learn = hwif_out.FEED.FEED_CTRL.resync_learn.value;
    feed_shadow.filter_mask = hwif_out.FEED.FEED_CTRL.filter_mask.value;
    feed_shadow.session = {
      hwif_out.FEED.SESSION_0.bytes.value,
      hwif_out.FEED.SESSION_1.bytes.value,
      hwif_out.FEED.SESSION_2.bytes.value
    };
    feed_shadow.resync_seq = {
      hwif_out.FEED.RESYNC_SEQ_HI.seq.value, hwif_out.FEED.RESYNC_SEQ_LO.seq.value
    };
    feed_shadow.class_en = hwif_out.ITCH.ITCH_CTRL.class_en.value;
    feed_shadow.class_filter = hwif_out.ITCH.ITCH_CTRL.class_filter.value;
    feed_shadow.epoch = epoch_next;
  end

  assign feed_commit = hwif_out.FEED.FEED_COMMIT.commit.value;  // level-sensitive, like NET_COMMIT
  assign feed_resync = hwif_out.FEED.FEED_COMMIT.resync.value;
  assign feed_resync_req = feed_resync && !feed_resync_q;  // rising edge: one resync per request
  assign feed_req = feed_commit || feed_resync_req;
  assign epoch_next = epoch_q + CFG_EPOCH_W'(feed_write_q && (feed_commit || feed_resync));

  always_ff @(posedge host_clk) begin
    if (host_rst) begin
      feed_resync_q     <= 1'b0;
      feed_pending_q    <= 1'b0;
      feed_resync_bit_q <= 1'b0;
      feed_write_q      <= 1'b0;
      epoch_q           <= '0;
    end else begin
      feed_write_q   <= hwif_out.FEED.FEED_COMMIT.commit.swmod;
      epoch_q        <= epoch_next;
      feed_resync_q  <= feed_resync;
      feed_pending_q <= feed_req || (feed_pending_q && !feed_src_ready);
      // Set by a request, cleared by the transfer that carries it (a request in that same cycle
      // goes with the next transfer).
      if (feed_resync_req) begin
        feed_resync_bit_q <= 1'b1;
      end else if (feed_pending_q && feed_src_ready) begin
        feed_resync_bit_q <= 1'b0;
      end
    end
  end

  // Datapath: no reset (only sent after a request has loaded it).
  always_ff @(posedge host_clk) begin
    if (feed_req) begin
      feed_set_q <= feed_shadow;
    end
  end

  assign hwif_in.FEED.FEED_STATUS.commit_busy.next = feed_req || feed_pending_q || !feed_src_ready;

  // ---- statistics snapshot ----
  localparam int unsigned CNT_W = 64;

  // Everything one snapshot takes, in one net_clk cycle.
  typedef struct packed {
    logic [RX_N_CNT-1:0][CNT_W-1:0]   rx;
    logic [MOLD_N_CNT-1:0][CNT_W-1:0] mold;
    logic [ITCH_N_CNT-1:0][CNT_W-1:0] itch;
    logic [CNT_W-1:0]                 ring_commit_entries;
    logic [CNT_W-1:0]                 ring_drop_pkts;
    logic [CNT_W-1:0]                 ring_drop_entries;
    logic [ORD_N_CNT-1:0][CNT_W-1:0]  ord;
    logic [RING_ORD_QUEUED_W-1:0]     ord_queued;
    logic [STRAT_N_RULES-1:0]         armed;
    logic [31:0]                      budget;
    logic [63:0]                      expected_seq;
    logic                             synced;
    logic                             eos;
  } snap_t;

  logic snap, snap_pending_q, snap_req_ready, snap_req_net, snap_net_pending_q, snap_data_ready;
  logic snap_req_valid, snap_req_word, snap_landed, snap_loaded_q;
  logic [  RX_N_CNT-1:0][CNT_W-1:0] cnt;
  logic [MOLD_N_CNT-1:0][CNT_W-1:0] mold_cnt;
  logic [ITCH_N_CNT-1:0][CNT_W-1:0] itch_cnt;
  logic [ ORD_N_CNT-1:0][CNT_W-1:0] ord_cnt;
  snap_t snap_src, snap_dst, snap_cnt;

  // Level-sensitive like NET_COMMIT (regblock may hold the pulse one more cycle).
  assign snap = hwif_out.STATS.STATS_CTRL.snapshot.value;

  always_ff @(posedge host_clk) begin
    if (host_rst) begin
      snap_pending_q <= 1'b0;
    end else begin
      snap_pending_q <= (snap || snap_pending_q) && !snap_req_ready;
    end
  end

  // The request word is a constant 1; ANDing it with dst_valid gives the port a user (synthesis
  // removes it).
  cdc_handshake #(
      .WIDTH (1),
      .STAGES(STAGES)
  ) u_snap_req_cdc (
      .src_clk  (host_clk),
      .src_rst  (host_rst),
      .src_data (1'b1),
      .src_valid(snap || snap_pending_q),
      .src_ready(snap_req_ready),
      .dst_clk  (net_clk),
      .dst_rst  (net_rst),
      .dst_data (snap_req_word),
      .dst_valid(snap_req_valid)
  );
  assign snap_req_net = snap_req_valid && snap_req_word;

  rx_stats #(
      .CNT_W(CNT_W)
  ) u_rx_stats (
      .clk        (net_clk),
      .rst        (net_rst),
      .stat_valid (stat_valid),
      .stat_reason(stat_reason),
      .stat_bytes (stat_bytes),
      .cnt        (cnt)
  );

  // Packet events and results on their way to the mold and ITCH counters: see Feed commit.
  localparam int unsigned STATS_DELAY = 2;
  typedef struct packed {
    logic          mpkt_valid;
    mold_status_e  mpkt_status;
    logic [63:0]   mpkt_gap;
    logic [15:0]   mpkt_msgs;
    logic [15:0]   mpkt_dup_msgs;
    logic [15:0]   mpkt_oversize;
    logic          mres_valid;
    itch_class_e   mres_class;
    itch_outcome_e mres_outcome;
  } stats_ev_t;
  stats_ev_t [STATS_DELAY-1:0] stats_q;
  stats_ev_t                   stats_ev;

  assign stats_ev = stats_q[STATS_DELAY-1];

  always_ff @(posedge net_clk) begin
    stats_q <= {
      stats_q[STATS_DELAY-2:0],
      stats_ev_t
'{
          mpkt_valid: mpkt_valid,
          mpkt_status: mpkt_status,
          mpkt_gap: mpkt_gap,
          mpkt_msgs: mpkt_msgs,
          mpkt_dup_msgs: mpkt_dup_msgs,
          mpkt_oversize: mpkt_oversize,
          mres_valid: mres_valid,
          mres_class: mres_class,
          mres_outcome: mres_outcome
      }
    };
    if (net_rst) begin
      for (int i = 0; i < STATS_DELAY; i++) begin
        stats_q[i].mpkt_valid <= 1'b0;
        stats_q[i].mres_valid <= 1'b0;
      end
    end
  end

  mold_stats #(
      .CNT_W(CNT_W)
  ) u_mold_stats (
      .clk         (net_clk),
      .rst         (net_rst),
      .pkt_valid   (stats_ev.mpkt_valid),
      .pkt_status  (stats_ev.mpkt_status),
      .pkt_gap     (stats_ev.mpkt_gap),
      .pkt_msgs    (stats_ev.mpkt_msgs),
      .pkt_dup_msgs(stats_ev.mpkt_dup_msgs),
      .pkt_oversize(stats_ev.mpkt_oversize),
      .cnt         (mold_cnt)
  );

  itch_stats #(
      .CNT_W(CNT_W)
  ) u_itch_stats (
      .clk        (net_clk),
      .rst        (net_rst),
      .res_valid  (stats_ev.mres_valid),
      .res_class  (stats_ev.mres_class),
      .res_outcome(stats_ev.mres_outcome),
      .pkt_valid  (stats_ev.mpkt_valid),
      .pkt_status (stats_ev.mpkt_status),
      .cnt        (itch_cnt)
  );

  // Order counters: the packer's own order inputs, no delay (header).
  order_stats #(
      .CNT_W(CNT_W)
  ) u_order_stats (
      .clk        (net_clk),
      .rst        (net_rst),
      .fire_valid (ord_fire_valid),
      .fire_rule  (ord_fire_rule),
      .sent_valid (ord_sent_valid),
      .sent_source(ord_sent_source),
      .rej_valid  (ord_rej_valid),
      .rej_reason (ord_rej_reason),
      .lost_valid (ord_lost_valid),
      .lost_reason(ord_lost_reason),
      .cnt        (ord_cnt)
  );

  // Feed state delayed to the cycle in which the counter banks hold the packet event that made it.
  localparam int unsigned FEED_DELAY = 6;
  logic [FEED_DELAY-1:0][63:0] feed_seq_q;
  logic [FEED_DELAY-1:0] feed_synced_q, feed_eos_q;

  always_ff @(posedge net_clk) begin
    feed_seq_q    <= {feed_seq_q[FEED_DELAY-2:0], feed_expected_seq};
    feed_synced_q <= {feed_synced_q[FEED_DELAY-2:0], feed_synced};
    feed_eos_q    <= {feed_eos_q[FEED_DELAY-2:0], feed_eos};
  end

  assign snap_src.rx                  = cnt;
  assign snap_src.mold                = mold_cnt;
  assign snap_src.itch                = itch_cnt;
  assign snap_src.ring_commit_entries = cnt_ring_commit_entries;
  assign snap_src.ring_drop_pkts      = cnt_ring_drop_pkts;
  assign snap_src.ring_drop_entries   = cnt_ring_drop_entries;
  assign snap_src.ord                 = ord_cnt;
  assign snap_src.ord_queued          = ord_queued;
  assign snap_src.armed               = st_armed;
  assign snap_src.budget              = st_budget;
  assign snap_src.expected_seq        = feed_seq_q[FEED_DELAY-1];
  assign snap_src.synced              = feed_synced_q[FEED_DELAY-1];
  assign snap_src.eos                 = feed_eos_q[FEED_DELAY-1];

  always_ff @(posedge net_clk) begin
    if (net_rst) begin
      snap_net_pending_q <= 1'b0;
    end else begin
      snap_net_pending_q <= (snap_req_net || snap_net_pending_q) && !snap_data_ready;
    end
  end

  // The acceptance edge of this handshake is the snapshot: its source register samples snap_src.
  cdc_handshake #(
      .WIDTH ($bits(snap_t)),
      .STAGES(STAGES)
  ) u_snap_cdc (
      .src_clk  (net_clk),
      .src_rst  (net_rst),
      .src_data (snap_src),
      .src_valid(snap_req_net || snap_net_pending_q),
      .src_ready(snap_data_ready),
      .dst_clk  (host_clk),
      .dst_rst  (host_rst),
      .dst_data (snap_dst),
      .dst_valid(snap_landed)
  );

  // snap_dst has no reset: 0 until the first snapshot lands (and in its landing cycle already the
  // new values, like STATS_SEQ).
  always_ff @(posedge host_clk) begin
    if (host_rst) begin
      snap_loaded_q <= 1'b0;
    end else if (snap_landed) begin
      snap_loaded_q <= 1'b1;
    end
  end

  assign snap_cnt = (snap_loaded_q || snap_landed) ? snap_dst : '0;
  assign hwif_in.STATS.STATS_SEQ.seq.incr = snap_landed;
  assign hwif_in.STATS.RX_PKTS.LO.value.next = snap_cnt.rx[RX_CNT_PKTS][31:0];
  assign hwif_in.STATS.RX_PKTS.HI.value.next = snap_cnt.rx[RX_CNT_PKTS][63:32];
  assign hwif_in.STATS.RX_BYTES.LO.value.next = snap_cnt.rx[RX_CNT_BYTES][31:0];
  assign hwif_in.STATS.RX_BYTES.HI.value.next = snap_cnt.rx[RX_CNT_BYTES][63:32];
  assign hwif_in.STATS.DROP_DISABLED.LO.value.next = snap_cnt.rx[1+RX_DROP_DISABLED][31:0];
  assign hwif_in.STATS.DROP_DISABLED.HI.value.next = snap_cnt.rx[1+RX_DROP_DISABLED][63:32];
  assign hwif_in.STATS.DROP_FCS.LO.value.next = snap_cnt.rx[1+RX_DROP_FCS][31:0];
  assign hwif_in.STATS.DROP_FCS.HI.value.next = snap_cnt.rx[1+RX_DROP_FCS][63:32];
  assign hwif_in.STATS.DROP_MAC.LO.value.next = snap_cnt.rx[1+RX_DROP_MAC][31:0];
  assign hwif_in.STATS.DROP_MAC.HI.value.next = snap_cnt.rx[1+RX_DROP_MAC][63:32];
  assign hwif_in.STATS.DROP_ETHERTYPE.LO.value.next = snap_cnt.rx[1+RX_DROP_ETHERTYPE][31:0];
  assign hwif_in.STATS.DROP_ETHERTYPE.HI.value.next = snap_cnt.rx[1+RX_DROP_ETHERTYPE][63:32];
  assign hwif_in.STATS.DROP_IP_HDR.LO.value.next = snap_cnt.rx[1+RX_DROP_IP_HDR][31:0];
  assign hwif_in.STATS.DROP_IP_HDR.HI.value.next = snap_cnt.rx[1+RX_DROP_IP_HDR][63:32];
  assign hwif_in.STATS.DROP_FRAG.LO.value.next = snap_cnt.rx[1+RX_DROP_FRAG][31:0];
  assign hwif_in.STATS.DROP_FRAG.HI.value.next = snap_cnt.rx[1+RX_DROP_FRAG][63:32];
  assign hwif_in.STATS.DROP_PROTO.LO.value.next = snap_cnt.rx[1+RX_DROP_PROTO][31:0];
  assign hwif_in.STATS.DROP_PROTO.HI.value.next = snap_cnt.rx[1+RX_DROP_PROTO][63:32];
  assign hwif_in.STATS.DROP_FILTER.LO.value.next = snap_cnt.rx[1+RX_DROP_FILTER][31:0];
  assign hwif_in.STATS.DROP_FILTER.HI.value.next = snap_cnt.rx[1+RX_DROP_FILTER][63:32];
  assign hwif_in.STATS.DROP_LEN.LO.value.next = snap_cnt.rx[1+RX_DROP_LEN][31:0];
  assign hwif_in.STATS.DROP_LEN.HI.value.next = snap_cnt.rx[1+RX_DROP_LEN][63:32];
  // FEED: MOLD_<status> at index status, then the message and gap counters.
  assign hwif_in.FEED.MOLD_OK.LO.value.next = snap_cnt.mold[MOLD_OK][31:0];
  assign hwif_in.FEED.MOLD_OK.HI.value.next = snap_cnt.mold[MOLD_OK][63:32];
  assign hwif_in.FEED.MOLD_HEARTBEAT.LO.value.next = snap_cnt.mold[MOLD_HEARTBEAT][31:0];
  assign hwif_in.FEED.MOLD_HEARTBEAT.HI.value.next = snap_cnt.mold[MOLD_HEARTBEAT][63:32];
  assign hwif_in.FEED.MOLD_EOS.LO.value.next = snap_cnt.mold[MOLD_EOS][31:0];
  assign hwif_in.FEED.MOLD_EOS.HI.value.next = snap_cnt.mold[MOLD_EOS][63:32];
  assign hwif_in.FEED.MOLD_DUP.LO.value.next = snap_cnt.mold[MOLD_DUP][31:0];
  assign hwif_in.FEED.MOLD_DUP.HI.value.next = snap_cnt.mold[MOLD_DUP][63:32];
  assign hwif_in.FEED.MOLD_DISABLED.LO.value.next = snap_cnt.mold[MOLD_DISABLED][31:0];
  assign hwif_in.FEED.MOLD_DISABLED.HI.value.next = snap_cnt.mold[MOLD_DISABLED][63:32];
  assign hwif_in.FEED.MOLD_OTHER_FEED.LO.value.next = snap_cnt.mold[MOLD_OTHER_FEED][31:0];
  assign hwif_in.FEED.MOLD_OTHER_FEED.HI.value.next = snap_cnt.mold[MOLD_OTHER_FEED][63:32];
  assign hwif_in.FEED.MOLD_ABORT.LO.value.next = snap_cnt.mold[MOLD_ABORT][31:0];
  assign hwif_in.FEED.MOLD_ABORT.HI.value.next = snap_cnt.mold[MOLD_ABORT][63:32];
  assign hwif_in.FEED.MOLD_RUNT.LO.value.next = snap_cnt.mold[MOLD_RUNT][31:0];
  assign hwif_in.FEED.MOLD_RUNT.HI.value.next = snap_cnt.mold[MOLD_RUNT][63:32];
  assign hwif_in.FEED.MOLD_SESSION.LO.value.next = snap_cnt.mold[MOLD_SESSION][31:0];
  assign hwif_in.FEED.MOLD_SESSION.HI.value.next = snap_cnt.mold[MOLD_SESSION][63:32];
  assign hwif_in.FEED.MOLD_UNSUPPORTED.LO.value.next = snap_cnt.mold[MOLD_UNSUPPORTED][31:0];
  assign hwif_in.FEED.MOLD_UNSUPPORTED.HI.value.next = snap_cnt.mold[MOLD_UNSUPPORTED][63:32];
  assign hwif_in.FEED.MOLD_MALFORMED.LO.value.next = snap_cnt.mold[MOLD_MALFORMED][31:0];
  assign hwif_in.FEED.MOLD_MALFORMED.HI.value.next = snap_cnt.mold[MOLD_MALFORMED][63:32];
  assign hwif_in.FEED.MOLD_MSGS.LO.value.next = snap_cnt.mold[MOLD_CNT_MSGS][31:0];
  assign hwif_in.FEED.MOLD_MSGS.HI.value.next = snap_cnt.mold[MOLD_CNT_MSGS][63:32];
  assign hwif_in.FEED.MOLD_DUP_MSGS.LO.value.next = snap_cnt.mold[MOLD_CNT_DUP_MSGS][31:0];
  assign hwif_in.FEED.MOLD_DUP_MSGS.HI.value.next = snap_cnt.mold[MOLD_CNT_DUP_MSGS][63:32];
  assign hwif_in.FEED.MOLD_OVERSIZE_MSGS.LO.value.next = snap_cnt.mold[MOLD_CNT_OVERSIZE_MSGS][31:0];
  assign hwif_in.FEED.MOLD_OVERSIZE_MSGS.HI.value.next = snap_cnt.mold[MOLD_CNT_OVERSIZE_MSGS][63:32];
  assign hwif_in.FEED.MOLD_GAP_EVENTS.LO.value.next = snap_cnt.mold[MOLD_CNT_GAP_EVENTS][31:0];
  assign hwif_in.FEED.MOLD_GAP_EVENTS.HI.value.next = snap_cnt.mold[MOLD_CNT_GAP_EVENTS][63:32];
  assign hwif_in.FEED.MOLD_GAP_MSGS.LO.value.next = snap_cnt.mold[MOLD_CNT_GAP_MSGS][31:0];
  assign hwif_in.FEED.MOLD_GAP_MSGS.HI.value.next = snap_cnt.mold[MOLD_CNT_GAP_MSGS][63:32];
  // ITCH: ITCH_<class> at index class, then one counter per outcome.
  assign hwif_in.ITCH.ITCH_ORDER.LO.value.next = snap_cnt.itch[ITCH_ORDER][31:0];
  assign hwif_in.ITCH.ITCH_ORDER.HI.value.next = snap_cnt.itch[ITCH_ORDER][63:32];
  assign hwif_in.ITCH.ITCH_TRADE.LO.value.next = snap_cnt.itch[ITCH_TRADE][31:0];
  assign hwif_in.ITCH.ITCH_TRADE.HI.value.next = snap_cnt.itch[ITCH_TRADE][63:32];
  assign hwif_in.ITCH.ITCH_ADMIN.LO.value.next = snap_cnt.itch[ITCH_ADMIN][31:0];
  assign hwif_in.ITCH.ITCH_ADMIN.HI.value.next = snap_cnt.itch[ITCH_ADMIN][63:32];
  assign hwif_in.ITCH.ITCH_AUCTION.LO.value.next = snap_cnt.itch[ITCH_AUCTION][31:0];
  assign hwif_in.ITCH.ITCH_AUCTION.HI.value.next = snap_cnt.itch[ITCH_AUCTION][63:32];
  assign hwif_in.ITCH.ITCH_UNKNOWN.LO.value.next = snap_cnt.itch[ITCH_UNKNOWN][31:0];
  assign hwif_in.ITCH.ITCH_UNKNOWN.HI.value.next = snap_cnt.itch[ITCH_UNKNOWN][63:32];
  assign hwif_in.ITCH.ITCH_MALFORMED.LO.value.next = snap_cnt.itch[ITCH_MALFORMED][31:0];
  assign hwif_in.ITCH.ITCH_MALFORMED.HI.value.next = snap_cnt.itch[ITCH_MALFORMED][63:32];
  assign hwif_in.ITCH.ITCH_OUT.LO.value.next = snap_cnt.itch[ITCH_CNT_OUT][31:0];
  assign hwif_in.ITCH.ITCH_OUT.HI.value.next = snap_cnt.itch[ITCH_CNT_OUT][63:32];
  assign hwif_in.ITCH.ITCH_FILTERED.LO.value.next = snap_cnt.itch[ITCH_CNT_FILTERED][31:0];
  assign hwif_in.ITCH.ITCH_FILTERED.HI.value.next = snap_cnt.itch[ITCH_CNT_FILTERED][63:32];
  assign hwif_in.ITCH.ITCH_MASKED.LO.value.next = snap_cnt.itch[ITCH_CNT_MASKED][31:0];
  assign hwif_in.ITCH.ITCH_MASKED.HI.value.next = snap_cnt.itch[ITCH_CNT_MASKED][63:32];
  assign hwif_in.RING.RING_COMMIT_ENTRIES.LO.value.next = snap_cnt.ring_commit_entries[31:0];
  assign hwif_in.RING.RING_COMMIT_ENTRIES.HI.value.next = snap_cnt.ring_commit_entries[63:32];
  assign hwif_in.RING.RING_DROP_PKTS.LO.value.next = snap_cnt.ring_drop_pkts[31:0];
  assign hwif_in.RING.RING_DROP_PKTS.HI.value.next = snap_cnt.ring_drop_pkts[63:32];
  assign hwif_in.RING.RING_DROP_ENTRIES.LO.value.next = snap_cnt.ring_drop_entries[31:0];
  assign hwif_in.RING.RING_DROP_ENTRIES.HI.value.next = snap_cnt.ring_drop_entries[63:32];
  assign hwif_in.RING.RING_DROP_ORDERS.LO.value.next = snap_cnt.ord[ORD_CNT_DROP][31:0];
  assign hwif_in.RING.RING_DROP_ORDERS.HI.value.next = snap_cnt.ord[ORD_CNT_DROP][63:32];
  assign hwif_in.RING.RING_ORDER_QUEUED.queued.next = snap_cnt.ord_queued;
  // STRATEGY: RULE_FIRED_<n> at index n.
  assign ohwif_in.STRATEGY.RULE_FIRED_0.LO.value.next = snap_cnt.ord[0][31:0];
  assign ohwif_in.STRATEGY.RULE_FIRED_0.HI.value.next = snap_cnt.ord[0][63:32];
  assign ohwif_in.STRATEGY.RULE_FIRED_1.LO.value.next = snap_cnt.ord[1][31:0];
  assign ohwif_in.STRATEGY.RULE_FIRED_1.HI.value.next = snap_cnt.ord[1][63:32];
  assign ohwif_in.STRATEGY.RULE_FIRED_2.LO.value.next = snap_cnt.ord[2][31:0];
  assign ohwif_in.STRATEGY.RULE_FIRED_2.HI.value.next = snap_cnt.ord[2][63:32];
  assign ohwif_in.STRATEGY.RULE_FIRED_3.LO.value.next = snap_cnt.ord[3][31:0];
  assign ohwif_in.STRATEGY.RULE_FIRED_3.HI.value.next = snap_cnt.ord[3][63:32];
  assign ohwif_in.STRATEGY.RULE_FIRED_4.LO.value.next = snap_cnt.ord[4][31:0];
  assign ohwif_in.STRATEGY.RULE_FIRED_4.HI.value.next = snap_cnt.ord[4][63:32];
  assign ohwif_in.STRATEGY.RULE_FIRED_5.LO.value.next = snap_cnt.ord[5][31:0];
  assign ohwif_in.STRATEGY.RULE_FIRED_5.HI.value.next = snap_cnt.ord[5][63:32];
  assign ohwif_in.STRATEGY.RULE_FIRED_6.LO.value.next = snap_cnt.ord[6][31:0];
  assign ohwif_in.STRATEGY.RULE_FIRED_6.HI.value.next = snap_cnt.ord[6][63:32];
  assign ohwif_in.STRATEGY.RULE_FIRED_7.LO.value.next = snap_cnt.ord[7][31:0];
  assign ohwif_in.STRATEGY.RULE_FIRED_7.HI.value.next = snap_cnt.ord[7][63:32];
  assign ohwif_in.STRATEGY.RULE_ARMED.armed.next = snap_cnt.armed;
  // RISK: ORDERS_SENT_*, then RISK_REJECT_<reason> at ORD_CNT_REJ + reason - 1.
  assign ohwif_in.RISK.BUDGET.budget.next = snap_cnt.budget;
  assign ohwif_in.RISK.ORDERS_SENT_HW.LO.value.next = snap_cnt.ord[ORD_CNT_SENT_HW][31:0];
  assign ohwif_in.RISK.ORDERS_SENT_HW.HI.value.next = snap_cnt.ord[ORD_CNT_SENT_HW][63:32];
  assign ohwif_in.RISK.ORDERS_SENT_HOST.LO.value.next = snap_cnt.ord[ORD_CNT_SENT_HOST][31:0];
  assign ohwif_in.RISK.ORDERS_SENT_HOST.HI.value.next = snap_cnt.ord[ORD_CNT_SENT_HOST][63:32];
  assign ohwif_in.RISK.RISK_REJECT_KILL.LO.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_KILL)-1][31:0];
  assign ohwif_in.RISK.RISK_REJECT_KILL.HI.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_KILL)-1][63:32];
  assign ohwif_in.RISK.RISK_REJECT_TX_OFF.LO.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_TX_OFF)-1][31:0];
  assign ohwif_in.RISK.RISK_REJECT_TX_OFF.HI.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_TX_OFF)-1][63:32];
  assign ohwif_in.RISK.RISK_REJECT_INVALID.LO.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_INVALID)-1][31:0];
  assign ohwif_in.RISK.RISK_REJECT_INVALID.HI.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_INVALID)-1][63:32];
  assign ohwif_in.RISK.RISK_REJECT_LOCATE.LO.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_LOCATE)-1][31:0];
  assign ohwif_in.RISK.RISK_REJECT_LOCATE.HI.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_LOCATE)-1][63:32];
  assign ohwif_in.RISK.RISK_REJECT_QTY.LO.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_QTY)-1][31:0];
  assign ohwif_in.RISK.RISK_REJECT_QTY.HI.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_QTY)-1][63:32];
  assign ohwif_in.RISK.RISK_REJECT_NOTIONAL.LO.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_NOTIONAL)-1][31:0];
  assign ohwif_in.RISK.RISK_REJECT_NOTIONAL.HI.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_NOTIONAL)-1][63:32];
  assign ohwif_in.RISK.RISK_REJECT_BUDGET.LO.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_BUDGET)-1][31:0];
  assign ohwif_in.RISK.RISK_REJECT_BUDGET.HI.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_BUDGET)-1][63:32];
  assign ohwif_in.RISK.RISK_REJECT_RATE.LO.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_RATE)-1][31:0];
  assign ohwif_in.RISK.RISK_REJECT_RATE.HI.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_RATE)-1][63:32];
  assign ohwif_in.RISK.RISK_REJECT_TX_BUSY.LO.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_TX_BUSY)-1][31:0];
  assign ohwif_in.RISK.RISK_REJECT_TX_BUSY.HI.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_TX_BUSY)-1][63:32];
  assign ohwif_in.RISK.RISK_REJECT_RING_FULL.LO.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_RING_FULL)-1][31:0];
  assign ohwif_in.RISK.RISK_REJECT_RING_FULL.HI.value.next = snap_cnt.ord[ORD_CNT_REJ+int'(RISK_RING_FULL)-1][63:32];
  assign hwif_in.FEED.EXPECTED_SEQ.LO.value.next = snap_cnt.expected_seq[31:0];
  assign hwif_in.FEED.EXPECTED_SEQ.HI.value.next = snap_cnt.expected_seq[63:32];
  assign hwif_in.FEED.FEED_STATE.synced.next = snap_cnt.synced;
  assign hwif_in.FEED.FEED_STATE.eos.next = snap_cnt.eos;

  // ---- RING: event_ring_writer, in this clock domain ----
  assign host_ring_enable = hwif_out.RING.RING_CTRL.enable.value;
  assign host_ring_base = {
    hwif_out.RING.RING_BASE_HI.base.value, hwif_out.RING.RING_BASE_LO.base.value, 12'h000
  };
  assign host_ring_size_log2 = hwif_out.RING.RING_SIZE_LOG2.size_log2.value;
  assign host_ring_cons = hwif_out.RING.CONS_IDX.cons.value;
  assign hwif_in.RING.PROD_IDX.prod.next = host_ring_prod_idx;
  assign hwif_in.RING.RING_STATUS.bresp_err.next = host_ring_bresp_err;
  assign hwif_in.RING.RING_STATUS.enable_err.next = host_ring_enable_err;
  assign hwif_in.RING.RING_WRITTEN.LO.value.next = host_ring_cnt_written[31:0];
  assign hwif_in.RING.RING_WRITTEN.HI.value.next = host_ring_cnt_written[63:32];
  assign hwif_in.RING.RING_BRESP_ERR.LO.value.next = host_ring_cnt_bresp_err[31:0];
  assign hwif_in.RING.RING_BRESP_ERR.HI.value.next = host_ring_cnt_bresp_err[63:32];

  // ---- host -> net ----
  net_cfg_t cfg_dst;
  logic cfg_dst_valid, cfg_loaded_q;

  cdc_handshake #(
      .WIDTH ($bits(net_cfg_t)),
      .STAGES(STAGES)
  ) u_cfg_cdc (
      .src_clk  (host_clk),
      .src_rst  (host_rst),
      .src_data (commit_buf_q),
      .src_valid(commit_pending_q),
      .src_ready(cfg_src_ready),
      .dst_clk  (net_clk),
      .dst_rst  (net_rst),
      .dst_data (cfg_dst),
      .dst_valid(cfg_dst_valid)
  );

  // cfg_dst is the handshake's destination register: it changes only in the cycle cfg_dst_valid
  // is high and then holds. rx_en is gated until the first transfer (cfg_dst has no reset); with
  // cfg_dst_valid in the gate, rx_en changes in the same cycle as the other fields.
  always_ff @(posedge net_clk) begin
    if (net_rst) begin
      cfg_loaded_q <= 1'b0;
    end else if (cfg_dst_valid) begin
      cfg_loaded_q <= 1'b1;
    end
  end

  assign cfg_rx_en        = cfg_dst.rx_en && (cfg_loaded_q || cfg_dst_valid);
  assign cfg_accept_bcast = cfg_dst.accept_bcast;
  assign cfg_local_mac    = cfg_dst.local_mac;
  assign cfg_filter_ip    = cfg_dst.filter_ip;
  assign cfg_filter_port  = cfg_dst.filter_port;
  assign cfg_filter_en    = cfg_dst.filter_en;

  // ---- host -> net: feed ----
  feed_cfg_t feed_dst;
  logic feed_dst_valid, feed_loaded_q;

  cdc_handshake #(
      .WIDTH ($bits(feed_cfg_t)),
      .STAGES(STAGES)
  ) u_feed_cdc (
      .src_clk  (host_clk),
      .src_rst  (host_rst),
      .src_data ({feed_set_q, feed_resync_bit_q}),
      .src_valid(feed_pending_q),
      .src_ready(feed_src_ready),
      .dst_clk  (net_clk),
      .dst_rst  (net_rst),
      .dst_data (feed_dst),
      .dst_valid(feed_dst_valid)
  );

  // As for NET: enable and the session check are gated until the first transfer; the resync
  // pulse is the landing cycle of a transfer that carries one.
  always_ff @(posedge net_clk) begin
    if (net_rst) begin
      feed_loaded_q <= 1'b0;
    end else if (feed_dst_valid) begin
      feed_loaded_q <= 1'b1;
    end
  end

  assign cfg_feed_en = feed_dst.set.enable && (feed_loaded_q || feed_dst_valid);
  assign cfg_session_check = feed_dst.set.session_check && (feed_loaded_q || feed_dst_valid);
  assign cfg_session = feed_dst.set.session;
  assign cfg_filter_mask = feed_dst.set.filter_mask;
  assign cfg_resync = feed_dst_valid && feed_dst.resync;
  assign cfg_resync_learn = feed_dst.set.resync_learn;
  assign cfg_resync_seq = feed_dst.set.resync_seq;
  assign cfg_class_en = feed_dst.set.class_en & {ITCH_N_CLASS{feed_loaded_q || feed_dst_valid}};
  assign cfg_class_filter = feed_dst.set.class_filter;
  assign cfg_epoch = feed_dst.set.epoch;

  // ======== order path (decisions 32-38) ========
  localparam int unsigned CMD_SETTLE = 3;  // cycles after the pulse whose events go in the response
  localparam int unsigned MBOX_DEPTH_LOG2 = 4;
  localparam int unsigned PAY_W = 8 * 42 + 32;  // the widest command: TX_COMMIT

  typedef enum logic [3:0] {
    CMD_RULE_COMMIT,
    CMD_RULE_ARM,
    CMD_RULE_DISARM,
    CMD_RISK_COMMIT,
    CMD_BUDGET_ADD,
    CMD_TX_COMMIT,
    CMD_USERREF_WR,
    CMD_USERREF_RD,
    CMD_LOC
  } cmd_kind_e;

  typedef struct packed {
    logic [STRAT_RULE_W-1:0] idx;
    strat_rule_t             rule;
  } cmd_rule_t;

  typedef struct packed {
    logic [31:0] max_qty;
    logic [63:0] max_notional;
    logic [31:0] rate_refill;
    logic [31:0] rate_period;
    logic [31:0] rate_burst;
  } cmd_lim_t;

  typedef struct packed {
    logic [8*42-1:0] tmpl;
    logic [7:0]      display;
    logic [7:0]      capacity;
    logic [7:0]      ise;
    logic [7:0]      cross_type;
  } cmd_tx_t;

  // One command as it crosses to net: its kind, the host's kill and tx_en when it was written (the fence
  // waits for them at net) and its payload (cmd_rule_t, cmd_lim_t, cmd_tx_t, sym_req_t or a 32-bit value).
  typedef struct packed {
    cmd_kind_e        kind;
    logic             kill;
    logic             tx_en;
    logic [PAY_W-1:0] pay;
  } cmd_t;

  // A command's response.
  typedef struct packed {
    logic [31:0]              rdata;      // TX_USERREF_NEXT read, LOCATE_ALLOW read
    logic                     refused;    // TX_COMMIT refused
    logic                     ignored;    // TX_USERREF_NEXT write ignored
    logic                     cfg_valid;
    logic [STRAT_N_RULES-1:0] error;
  } rsp_t;

  // Bytes 4w .. 4w+3 of a register in wire order (byte 4w in [31:24]) to byte i = [8i+7:8i].
  function automatic logic [31:0] wire_bytes(input logic [31:0] w);
    return {w[7:0], w[15:8], w[23:16], w[31:24]};
  endfunction

  // ---- direct controls: kill and tx_en (decision 32) ----
  logic kill_s, tx_en_s;
  logic [STAGES:0] ctl_hold_q;  // 1s after net_rst: the synchronizers have no reset
  logic            ctl_hold;

  sync_bit #(
      .WIDTH (2),
      .STAGES(STAGES)
  ) u_ctl_sync (
      .clk(net_clk),
      .d  ({ohwif_out.RISK.RISK_CTRL.kill.value, ohwif_out.TX.TX_CTRL.tx_en.value}),
      .q  ({kill_s, tx_en_s})
  );

  always_ff @(posedge net_clk) begin
    if (net_rst) begin
      ctl_hold_q <= '1;
    end else begin
      ctl_hold_q <= {ctl_hold_q[STAGES-1:0], 1'b0};
    end
  end

  assign ctl_hold  = ctl_hold_q[STAGES];
  assign ctl_kill  = kill_s || ctl_hold;
  assign ctl_tx_en = tx_en_s && st_tx_cfg_valid && !ctl_hold;

  // ---- mailbox (host -> net) ----
  typedef struct packed {
    logic [7:0]   side;
    logic [31:0]  qty;
    logic [31:0]  price;
    logic [15:0]  locate;
    logic [63:0]  stock;
    logic [7:0]   tif;
    logic [111:0] clordid;
  } mbox_t;

  mbox_t mbox_shadow, mbox_dout;
  logic mbox_send_write_q, mbox_send, mbox_fits, mbox_wr_q;
  logic mbox_full, mbox_wr_busy, mbox_empty, mbox_rd_busy;
  logic
      mbox_overflow_unused,
      mbox_underflow_unused;  // a send is only written when it fits; reads only when valid
  logic [MBOX_DEPTH_LOG2:0] mbox_count;
  logic                     mbox_dropped_q;
  logic [             63:0] mbox_drop_q;

  always_comb begin
    mbox_shadow.side = ohwif_out.TX.MBOX_SIDE.side.value;
    mbox_shadow.qty = ohwif_out.TX.MBOX_QTY.qty.value;
    mbox_shadow.price = ohwif_out.TX.MBOX_PRICE.price.value;
    mbox_shadow.locate = ohwif_out.TX.MBOX_LOCATE.locate.value;
    mbox_shadow.stock = {
      wire_bytes(ohwif_out.TX.MBOX_STOCK_1.bytes.value),
      wire_bytes(ohwif_out.TX.MBOX_STOCK_0.bytes.value)
    };
    mbox_shadow.tif = ohwif_out.TX.MBOX_TIF.tif.value;
    mbox_shadow.clordid = {
      ohwif_out.TX.MBOX_CLORDID_TAIL.bytes.value[7:0],  // field [31:16]: byte 12 in value[15:8]
      ohwif_out.TX.MBOX_CLORDID_TAIL.bytes.value[15:8],
      wire_bytes(ohwif_out.TX.MBOX_CLORDID[2].bytes.value),
      wire_bytes(ohwif_out.TX.MBOX_CLORDID[1].bytes.value),
      wire_bytes(ohwif_out.TX.MBOX_CLORDID[0].bytes.value)
    };
  end

  // One send per write with byte 0 enabled (swmod), as FEED_COMMIT counts its writes.
  assign mbox_send = mbox_send_write_q && ohwif_out.TX.MBOX_SEND.send.value;
  assign mbox_fits = !mbox_full && !mbox_wr_busy;

  always_ff @(posedge host_clk) begin
    if (host_rst) begin
      mbox_send_write_q <= 1'b0;
      mbox_wr_q         <= 1'b0;
      mbox_dropped_q    <= 1'b0;
      mbox_drop_q       <= '0;
    end else begin
      mbox_send_write_q <= ohwif_out.TX.MBOX_SEND.send.swmod;
      mbox_wr_q         <= mbox_send;
      if (mbox_send) begin
        mbox_dropped_q <= !mbox_fits;
        mbox_drop_q    <= mbox_drop_q + 64'(!mbox_fits);
      end
    end
  end

  cdc_async_fifo #(
      .WIDTH     ($bits(mbox_t)),
      .DEPTH_LOG2(MBOX_DEPTH_LOG2),
      .WR_COUNT  (1'b1)
  ) u_mbox_fifo (
      .rst        (host_rst),
      .wr_clk     (host_clk),
      .wr_en      (mbox_send && mbox_fits),
      .din        (mbox_shadow),
      .wr_commit  (1'b0),
      .wr_rollback(1'b0),
      .full       (mbox_full),
      .overflow   (mbox_overflow_unused),
      .wr_rst_busy(mbox_wr_busy),
      .wr_count   (mbox_count),
      .rd_clk     (net_clk),
      .rd_en      (m_host_ready && m_host_valid),
      .dout       (mbox_dout),
      .empty      (mbox_empty),
      .underflow  (mbox_underflow_unused),
      .rd_rst_busy(mbox_rd_busy)
  );

  assign m_host_valid                         = !mbox_empty && !mbox_rd_busy;
  assign m_host_side                          = mbox_dout.side;
  assign m_host_qty                           = mbox_dout.qty;
  assign m_host_price                         = mbox_dout.price;
  assign m_host_locate                        = mbox_dout.locate;
  assign m_host_stock                         = mbox_dout.stock;
  assign m_host_tif                           = mbox_dout.tif;
  assign m_host_clordid                       = mbox_dout.clordid;

  // The send's result in its own cycle already: a read right after the write sees it.
  assign ohwif_in.TX.MBOX_STATUS.dropped.next = mbox_send ? !mbox_fits : mbox_dropped_q;
  assign ohwif_in.TX.MBOX_SPACE.space.next    = 5'(2 ** MBOX_DEPTH_LOG2) - 5'(mbox_count);
  assign ohwif_in.TX.MBOX_DROP.LO.value.next  = mbox_drop_q[31:0];
  assign ohwif_in.TX.MBOX_DROP.HI.value.next  = mbox_drop_q[63:32];

  // ---- command channel, host side ----
  typedef enum logic [1:0] {
    H_IDLE,
    H_FENCE,
    H_SEND,
    H_WAIT
  } host_state_e;

  host_state_e h_state_q;
  cmd_t cmd_new, cmd_q;
  logic cmd_req, cmd_nop, nop_ack_q, cmd_src_ready, rsp_landed;
  logic [STRAT_N_RULES-1:0] rule_err_q;
  logic tx_cfg_valid_q, tx_refused_q, userref_ignored_q;
  rsp_t      rsp_dst;
  cmd_rule_t new_rule;
  cmd_lim_t  new_lim;
  cmd_tx_t   new_tx;
  sym_req_t  new_loc;

  always_comb begin
    new_rule.idx = ohwif_out.STRATEGY.RULE_SEL.idx.value;
    new_rule.rule.enable = ohwif_out.STRATEGY.RULE_CTRL.enable.value;
    new_rule.rule.msg_mask = ohwif_out.STRATEGY.RULE_CTRL.msg_mask.value;
    new_rule.rule.locate = ohwif_out.STRATEGY.RULE_LOCATE.locate.value;
    new_rule.rule.side_match = ohwif_out.STRATEGY.RULE_CTRL.side_match.value;
    new_rule.rule.price_ge = ohwif_out.STRATEGY.RULE_CTRL.price_ge.value;
    new_rule.rule.price_threshold = ohwif_out.STRATEGY.RULE_THRESHOLD.threshold.value;
    new_rule.rule.min_shares = ohwif_out.STRATEGY.RULE_MIN_SHARES.min_shares.value;
    new_rule.rule.action_side = ohwif_out.STRATEGY.RULE_CTRL.action_side.value;
    new_rule.rule.qty_min = ohwif_out.STRATEGY.RULE_CTRL.qty_min.value;
    new_rule.rule.qty = ohwif_out.STRATEGY.RULE_QTY.qty.value;
    new_rule.rule.price_threshold_mode = ohwif_out.STRATEGY.RULE_CTRL.price_threshold_mode.value;
    new_rule.rule.price_offset = ohwif_out.STRATEGY.RULE_OFFSET.offset.value;
    new_rule.rule.tif = ohwif_out.STRATEGY.RULE_CTRL.tif.value;

    new_lim.max_qty = ohwif_out.RISK.MAX_QTY.max_qty.value;
    new_lim.max_notional = {
      ohwif_out.RISK.MAX_NOTIONAL_HI.value.value, ohwif_out.RISK.MAX_NOTIONAL_LO.value.value
    };
    new_lim.rate_refill = ohwif_out.RISK.RATE_REFILL.refill.value;
    new_lim.rate_period = ohwif_out.RISK.RATE_PERIOD.period.value;
    new_lim.rate_burst = ohwif_out.RISK.RATE_BURST.burst.value;

    new_tx.tmpl[8*40+:16] = {  // field [31:16]: byte 40 in value[15:8]
      ohwif_out.TX.TX_TEMPLATE_TAIL.bytes.value[7:0],
      ohwif_out.TX.TX_TEMPLATE_TAIL.bytes.value[15:8]
    };
    for (int w = 0; w < 10; w++) begin
      new_tx.tmpl[32*w+:32] = wire_bytes(ohwif_out.TX.TX_TEMPLATE[w].bytes.value);
    end
    new_tx.display    = ohwif_out.TX.TX_OUCH.display.value;
    new_tx.capacity   = ohwif_out.TX.TX_OUCH.capacity.value;
    new_tx.ise        = ohwif_out.TX.TX_OUCH.ise.value;
    new_tx.cross_type = ohwif_out.TX.TX_OUCH.cross_type.value;

    new_loc.wr        = hwif_out.LOCATE_ALLOW.req_is_wr;
    new_loc.addr      = hwif_out.LOCATE_ALLOW.addr[12:2];
    new_loc.wdata     = hwif_out.LOCATE_ALLOW.wr_data;
    for (int b = 0; b < 4; b++) begin
      new_loc.wstrb[b] = |hwif_out.LOCATE_ALLOW.wr_biten[8*b+:8];  // AXI strobes: whole bytes
    end
  end

  // The command of this cycle's external request (regblock: one at a time) and whether it is a no-op.
  always_comb begin
    cmd_new.kill  = ohwif_out.RISK.RISK_CTRL.kill.value;
    cmd_new.tx_en = ohwif_out.TX.TX_CTRL.tx_en.value;
    cmd_new.kind  = CMD_LOC;
    cmd_new.pay   = PAY_W'(new_loc);
    cmd_req       = hwif_out.LOCATE_ALLOW.req;
    cmd_nop       = 1'b0;
    if (ohwif_out.STRATEGY.RULE_COMMIT.req) begin
      cmd_new.kind = CMD_RULE_COMMIT;
      cmd_new.pay = PAY_W'(new_rule);
      cmd_req = 1'b1;
      cmd_nop      = !(ohwif_out.STRATEGY.RULE_COMMIT.wr_data.commit && ohwif_out.STRATEGY.RULE_COMMIT.wr_biten.commit);
    end
    if (ohwif_out.STRATEGY.RULE_ARM.req) begin
      cmd_new.kind = CMD_RULE_ARM;
      cmd_new.pay  = PAY_W'(ohwif_out.STRATEGY.RULE_ARM.wr_data.mask & ohwif_out.STRATEGY.RULE_ARM.wr_biten.mask);
      cmd_req = 1'b1;
    end
    if (ohwif_out.STRATEGY.RULE_DISARM.req) begin
      cmd_new.kind = CMD_RULE_DISARM;
      cmd_new.pay = PAY_W'(ohwif_out.STRATEGY.RULE_DISARM.wr_data.mask & ohwif_out.STRATEGY.RULE_DISARM.wr_biten.mask);
      cmd_req = 1'b1;
    end
    if (ohwif_out.RISK.RISK_COMMIT.req) begin
      cmd_new.kind = CMD_RISK_COMMIT;
      cmd_new.pay = PAY_W'(new_lim);
      cmd_req = 1'b1;
      cmd_nop      = !(ohwif_out.RISK.RISK_COMMIT.wr_data.commit && ohwif_out.RISK.RISK_COMMIT.wr_biten.commit);
    end
    if (ohwif_out.RISK.BUDGET_ADD.req) begin
      cmd_new.kind = CMD_BUDGET_ADD;
      cmd_new.pay = PAY_W'(ohwif_out.RISK.BUDGET_ADD.wr_data.amount & ohwif_out.RISK.BUDGET_ADD.wr_biten.amount);
      cmd_req = 1'b1;
    end
    if (ohwif_out.TX.TX_COMMIT.req) begin
      cmd_new.kind = CMD_TX_COMMIT;
      cmd_new.pay = PAY_W'(new_tx);
      cmd_req = 1'b1;
      cmd_nop = !(ohwif_out.TX.TX_COMMIT.wr_data.commit && ohwif_out.TX.TX_COMMIT.wr_biten.commit);
    end
    if (ohwif_out.TX.TX_USERREF_NEXT.req) begin
      cmd_new.kind = CMD_USERREF_WR;
      cmd_new.pay = PAY_W'(ohwif_out.TX.TX_USERREF_NEXT.wr_data.value & ohwif_out.TX.TX_USERREF_NEXT.wr_biten.value);
      cmd_req = 1'b1;
    end
    if (ohwif_out.TX.TX_USERREF.req) begin
      cmd_new.kind = CMD_USERREF_RD;
      cmd_req      = 1'b1;
    end
  end

  // The fence's host half: the mailbox read empty, and no send in this or the last cycle (wr_count
  // shows a write from the edge after it).
  logic mbox_drained;
  assign mbox_drained = mbox_count == '0 && !mbox_send && !mbox_wr_q;

  always_ff @(posedge host_clk) begin
    if (host_rst) begin
      h_state_q         <= H_IDLE;
      nop_ack_q         <= 1'b0;
      rule_err_q        <= '1;
      tx_cfg_valid_q    <= 1'b0;
      tx_refused_q      <= 1'b0;
      userref_ignored_q <= 1'b0;
    end else begin
      nop_ack_q <= 1'b0;
      unique case (h_state_q)
        H_IDLE: begin
          if (cmd_req) begin
            if (cmd_nop) begin
              nop_ack_q <= 1'b1;
            end else begin
              h_state_q <= H_FENCE;
            end
          end
        end
        H_FENCE: begin
          if (mbox_drained) begin
            h_state_q <= H_SEND;
          end
        end
        H_SEND: begin
          if (cmd_src_ready) begin
            h_state_q <= H_WAIT;
          end
        end
        H_WAIT: begin
          if (rsp_landed) begin
            h_state_q      <= H_IDLE;
            rule_err_q     <= rsp_dst.error;
            tx_cfg_valid_q <= rsp_dst.cfg_valid;
            if (cmd_q.kind == CMD_TX_COMMIT) begin
              tx_refused_q <= rsp_dst.refused;
            end
            if (cmd_q.kind == CMD_USERREF_WR) begin
              userref_ignored_q <= rsp_dst.ignored;
            end
          end
        end
        default: h_state_q <= H_IDLE;
      endcase
    end
  end

  // Datapath: no reset (only sent after a request has loaded it).
  always_ff @(posedge host_clk) begin
    if (cmd_req && h_state_q == H_IDLE) begin
      cmd_q <= cmd_new;
    end
  end

  cmd_t cmd_dst;
  logic cmd_dst_valid;

  cdc_handshake #(
      .WIDTH ($bits(cmd_t)),
      .STAGES(STAGES)
  ) u_cmd_cdc (
      .src_clk  (host_clk),
      .src_rst  (host_rst),
      .src_data (cmd_q),
      .src_valid(h_state_q == H_SEND),
      .src_ready(cmd_src_ready),
      .dst_clk  (net_clk),
      .dst_rst  (net_rst),
      .dst_data (cmd_dst),
      .dst_valid(cmd_dst_valid)
  );

  // Acks: a no-op in the cycle after its request, a command when its response lands (cmd_q holds its
  // kind; a no-op's kind is in cmd_q too).
  logic ack;
  assign ack                                  = nop_ack_q || (h_state_q == H_WAIT && rsp_landed);

  assign ohwif_in.STRATEGY.RULE_COMMIT.wr_ack = ack && cmd_q.kind == CMD_RULE_COMMIT;
  assign ohwif_in.STRATEGY.RULE_ARM.wr_ack    = ack && cmd_q.kind == CMD_RULE_ARM;
  assign ohwif_in.STRATEGY.RULE_DISARM.wr_ack = ack && cmd_q.kind == CMD_RULE_DISARM;
  assign ohwif_in.RISK.RISK_COMMIT.wr_ack     = ack && cmd_q.kind == CMD_RISK_COMMIT;
  assign ohwif_in.RISK.BUDGET_ADD.wr_ack      = ack && cmd_q.kind == CMD_BUDGET_ADD;
  assign ohwif_in.TX.TX_COMMIT.wr_ack         = ack && cmd_q.kind == CMD_TX_COMMIT;
  assign ohwif_in.TX.TX_USERREF_NEXT.wr_ack   = ack && cmd_q.kind == CMD_USERREF_WR;
  assign ohwif_in.TX.TX_USERREF.rd_ack        = ack && cmd_q.kind == CMD_USERREF_RD;
  assign ohwif_in.TX.TX_USERREF.rd_data.value = rsp_dst.rdata;
  logic host_loc_wr;
  assign host_loc_wr = cmd_q.pay[$bits(sym_req_t)-1];  // sym_req_t.wr, its first field
  assign hwif_in.LOCATE_ALLOW.wr_ack = ack && cmd_q.kind == CMD_LOC && host_loc_wr;
  assign hwif_in.LOCATE_ALLOW.rd_ack = ack && cmd_q.kind == CMD_LOC && !host_loc_wr;
  assign hwif_in.LOCATE_ALLOW.rd_data = rsp_dst.rdata;

  assign ohwif_in.STRATEGY.RULE_STATUS.error.next = rule_err_q;
  assign ohwif_in.TX.TX_STATUS.cfg_valid.next = tx_cfg_valid_q;
  assign ohwif_in.TX.TX_STATUS.cfg_refused.next = tx_refused_q;
  assign ohwif_in.TX.TX_STATUS.userref_ignored.next = userref_ignored_q;

  // ---- command channel, net side ----
  typedef enum logic [2:0] {
    N_IDLE,
    N_FENCE,
    N_LOC_REQ,
    N_LOC_RSP,
    N_SETTLE,
    N_RESP
  } net_state_e;

  net_state_e       n_state_q;
  logic       [2:0] host_take_q;  // a host order taken in each of the last 3 cycles
  logic host_take, fence_ok, pulse_q, path_closed;
  logic      [$clog2(CMD_SETTLE+1)-1:0] settle_q;
  rsp_t                                 rsp_q;
  logic                                 rsp_src_ready;
  sym_req_t                             cmd_loc;
  cmd_tx_t                              cmd_tx;
  cmd_lim_t                             cmd_lim;
  cmd_rule_t                            cmd_rule;

  assign host_take = m_host_valid && m_host_ready;
  assign fence_ok    = host_take_q == '0 && !host_take && kill_s == cmd_dst.kill && tx_en_s == cmd_dst.tx_en;
  assign path_closed = ctl_kill || !ctl_tx_en;
  assign cmd_loc = sym_req_t'(cmd_dst.pay[$bits(sym_req_t)-1:0]);
  assign cmd_tx = cmd_tx_t'(cmd_dst.pay[$bits(cmd_tx_t)-1:0]);
  assign cmd_lim = cmd_lim_t'(cmd_dst.pay[$bits(cmd_lim_t)-1:0]);
  assign cmd_rule = cmd_rule_t'(cmd_dst.pay[$bits(cmd_rule_t)-1:0]);

  always_ff @(posedge net_clk) begin
    if (net_rst) begin
      n_state_q   <= N_IDLE;
      host_take_q <= '0;
      pulse_q     <= 1'b0;
      settle_q    <= '0;
    end else begin
      host_take_q <= {host_take_q[1:0], host_take};
      pulse_q     <= 1'b0;
      unique case (n_state_q)
        N_IDLE: begin
          if (cmd_dst_valid) begin
            n_state_q <= N_FENCE;
          end
        end
        N_FENCE: begin
          if (fence_ok) begin
            if (cmd_dst.kind == CMD_LOC) begin
              n_state_q <= N_LOC_REQ;
            end else begin
              pulse_q   <= 1'b1;
              settle_q  <= '0;
              n_state_q <= N_SETTLE;
            end
          end
        end
        N_LOC_REQ: begin
          if (loc_req_ready) begin
            n_state_q <= N_LOC_RSP;
          end
        end
        N_LOC_RSP: begin
          if (loc_rsp_valid) begin
            settle_q  <= '0;
            n_state_q <= N_SETTLE;
          end
        end
        N_SETTLE: begin
          settle_q <= settle_q + 1'b1;
          if (settle_q == ($clog2(CMD_SETTLE + 1))'(CMD_SETTLE)) begin
            n_state_q <= N_RESP;
          end
        end
        N_RESP: begin
          if (rsp_src_ready) begin
            n_state_q <= N_IDLE;
          end
        end
        default: n_state_q <= N_IDLE;
      endcase
    end
  end

  // Pulses: the cycle after the fence opened. TX_COMMIT goes only while the path is closed (decision 37).
  assign cfg_rule_wr       = pulse_q && cmd_dst.kind == CMD_RULE_COMMIT;
  assign cfg_rule_idx      = cmd_rule.idx;
  assign cfg_rule          = cmd_rule.rule;
  assign cmd_arm           = pulse_q && cmd_dst.kind == CMD_RULE_ARM;
  assign cmd_arm_mask      = cmd_dst.pay[STRAT_N_RULES-1:0];
  assign cmd_disarm        = pulse_q && cmd_dst.kind == CMD_RULE_DISARM;
  assign cmd_disarm_mask   = cmd_dst.pay[STRAT_N_RULES-1:0];
  assign cfg_lim_wr        = pulse_q && cmd_dst.kind == CMD_RISK_COMMIT;
  assign cfg_max_qty       = cmd_lim.max_qty;
  assign cfg_max_notional  = cmd_lim.max_notional;
  assign cfg_rate_refill   = cmd_lim.rate_refill;
  assign cfg_rate_period   = cmd_lim.rate_period;
  assign cfg_rate_burst    = cmd_lim.rate_burst;
  assign cmd_budget_add    = pulse_q && cmd_dst.kind == CMD_BUDGET_ADD;
  assign cmd_budget_amount = cmd_dst.pay[31:0];
  assign cmd_userref_wr    = pulse_q && cmd_dst.kind == CMD_USERREF_WR;
  assign cmd_userref_value = cmd_dst.pay[31:0];
  assign cfg_tx_wr         = pulse_q && cmd_dst.kind == CMD_TX_COMMIT && path_closed;
  assign cfg_template      = cmd_tx.tmpl;
  assign cfg_display       = cmd_tx.display;
  assign cfg_capacity      = cmd_tx.capacity;
  assign cfg_ise           = cmd_tx.ise;
  assign cfg_cross_type    = cmd_tx.cross_type;
  assign loc_req_valid     = n_state_q == N_LOC_REQ;
  assign loc_req_wr        = cmd_loc.wr;
  assign loc_req_addr      = cmd_loc.addr;
  assign loc_req_wdata     = cmd_loc.wdata;
  assign loc_req_wstrb     = cmd_loc.wstrb;

  // The response collects the events of the pulse's cycle and the CMD_SETTLE after it, and the status of
  // the last of them. Datapath: no reset (sent only after a command has loaded it).
  always_ff @(posedge net_clk) begin
    if (pulse_q) begin
      rsp_q.refused <= cmd_dst.kind == CMD_TX_COMMIT && !path_closed;
      rsp_q.ignored <= 1'b0;
    end else if (n_state_q == N_SETTLE) begin
      rsp_q.refused <= rsp_q.refused || (cmd_dst.kind == CMD_TX_COMMIT && ev_cfg_refused);
      rsp_q.ignored <= rsp_q.ignored || (cmd_dst.kind == CMD_USERREF_WR && ev_userref_ignored);
    end
    if (n_state_q == N_LOC_RSP && loc_rsp_valid) begin
      rsp_q.refused <= 1'b0;
      rsp_q.ignored <= 1'b0;
      rsp_q.rdata   <= loc_rsp_rdata;
    end
    if (n_state_q == N_SETTLE) begin
      if (cmd_dst.kind != CMD_LOC) begin
        rsp_q.rdata <= st_userref;
      end
      rsp_q.cfg_valid <= st_tx_cfg_valid;
      rsp_q.error     <= st_error;
    end
  end

  cdc_handshake #(
      .WIDTH ($bits(rsp_t)),
      .STAGES(STAGES)
  ) u_rsp_cdc (
      .src_clk  (net_clk),
      .src_rst  (net_rst),
      .src_data (rsp_q),
      .src_valid(n_state_q == N_RESP),
      .src_ready(rsp_src_ready),
      .dst_clk  (host_clk),
      .dst_rst  (host_rst),
      .dst_data (rsp_dst),
      .dst_valid(rsp_landed)
  );

  // ---- symbol bitmap access: host -> net request, net -> host response ----
  sym_req_t sym_req, sym_req_q, sym_dst;
  logic sym_pending_q, sym_req_src_ready, sym_dst_valid, sym_cmd_q;
  logic sym_rsp_pending_q, sym_rsp_src_ready, sym_rsp_landed;
  logic [31:0] sym_rsp_q, sym_rsp_dst;

  always_comb begin
    sym_req.wr    = hwif_out.SYMBOL.req_is_wr;
    sym_req.addr  = hwif_out.SYMBOL.addr[12:2];
    sym_req.wdata = hwif_out.SYMBOL.wr_data;
    for (int b = 0; b < 4; b++) begin
      sym_req.wstrb[b] = |hwif_out.SYMBOL.wr_biten[8*b+:8];  // AXI strobes: whole bytes
    end
  end

  // regblock sends one access at a time and waits for its ack: the request register is free when
  // a new one comes. Pending until the handshake takes it.
  always_ff @(posedge host_clk) begin
    if (host_rst) begin
      sym_pending_q <= 1'b0;
    end else begin
      sym_pending_q <= hwif_out.SYMBOL.req || (sym_pending_q && !sym_req_src_ready);
    end
  end

  // Datapath: no reset (only sent after a request has loaded it).
  always_ff @(posedge host_clk) begin
    if (hwif_out.SYMBOL.req) begin
      sym_req_q <= sym_req;
    end
  end

  cdc_handshake #(
      .WIDTH ($bits(sym_req_t)),
      .STAGES(STAGES)
  ) u_sym_req_cdc (
      .src_clk  (host_clk),
      .src_rst  (host_rst),
      .src_data (sym_req_q),
      .src_valid(sym_pending_q),
      .src_ready(sym_req_src_ready),
      .dst_clk  (net_clk),
      .dst_rst  (net_rst),
      .dst_data (sym_dst),
      .dst_valid(sym_dst_valid)
  );

  // The command waits at net while itch_decoder is not ready (the bitmap sweep after reset).
  // sym_dst is the handshake's destination register: it holds until the next transfer.
  always_ff @(posedge net_clk) begin
    if (net_rst) begin
      sym_cmd_q <= 1'b0;
    end else begin
      sym_cmd_q <= sym_dst_valid || (sym_cmd_q && !sym_req_ready);
    end
  end

  assign sym_req_valid = sym_cmd_q;
  assign sym_req_wr    = sym_dst.wr;
  assign sym_req_addr  = sym_dst.addr;
  assign sym_req_wdata = sym_dst.wdata;
  assign sym_req_wstrb = sym_dst.wstrb;

  // The response handshake can still be finishing the previous round trip when the next response
  // comes: pending until it takes it.
  always_ff @(posedge net_clk) begin
    if (net_rst) begin
      sym_rsp_pending_q <= 1'b0;
    end else begin
      sym_rsp_pending_q <= sym_rsp_valid || (sym_rsp_pending_q && !sym_rsp_src_ready);
    end
  end

  always_ff @(posedge net_clk) begin
    if (sym_rsp_valid) begin
      sym_rsp_q <= sym_rsp_rdata;
    end
  end

  cdc_handshake #(
      .WIDTH (32),
      .STAGES(STAGES)
  ) u_sym_rsp_cdc (
      .src_clk  (net_clk),
      .src_rst  (net_rst),
      .src_data (sym_rsp_q),
      .src_valid(sym_rsp_pending_q),
      .src_ready(sym_rsp_src_ready),
      .dst_clk  (host_clk),
      .dst_rst  (host_rst),
      .dst_data (sym_rsp_dst),
      .dst_valid(sym_rsp_landed)
  );

  // The ack of the access in flight: its kind is the request register's (held until the next).
  assign hwif_in.SYMBOL.rd_ack  = sym_rsp_landed && !sym_req_q.wr;
  assign hwif_in.SYMBOL.wr_ack  = sym_rsp_landed && sym_req_q.wr;
  assign hwif_in.SYMBOL.rd_data = sym_rsp_dst;
endmodule
