// hft_csr_wrap: host CSR block (generated hft_csr) and its transfers to the net clock domain.
// Clock domain : host_clk (s_axil_*, hft_csr), net_clk (cfg_*)
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
// Reset        : host_rst and net_rst asserted together (one source, one reset_sync per domain,
//                cdc_handshake contract). After reset cfg_rx_en = 0; the other cfg_* are undefined
//                until the first commit (the receiver drops every frame while rx_en = 0).
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
    output logic [N_FILTERS-1:0]       cfg_filter_en
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

  hft_csr_pkg::hft_csr__in_t  hwif_in;
  hft_csr_pkg::hft_csr__out_t hwif_out;

  if ($size(hwif_out.NET.FILTER) != N_FILTERS) begin : g_check
    $error("hft_csr_wrap: N_FILTERS differs from NET.FILTER[] in hft_csr.rdl");
  end

  hft_csr u_csr (
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
      .hwif_out      (hwif_out)
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

  // ---- statistics: snapshot path arrives in the next step (S5) ----
  assign hwif_in.STATS.STATS_SEQ.seq.incr        = 1'b0;
  assign hwif_in.STATS.RX_PKTS                   = '{default: '0};
  assign hwif_in.STATS.RX_BYTES                  = '{default: '0};
  assign hwif_in.STATS.DROP_DISABLED             = '{default: '0};
  assign hwif_in.STATS.DROP_FCS                  = '{default: '0};
  assign hwif_in.STATS.DROP_MAC                  = '{default: '0};
  assign hwif_in.STATS.DROP_ETHERTYPE            = '{default: '0};
  assign hwif_in.STATS.DROP_IP_HDR               = '{default: '0};
  assign hwif_in.STATS.DROP_FRAG                 = '{default: '0};
  assign hwif_in.STATS.DROP_PROTO                = '{default: '0};
  assign hwif_in.STATS.DROP_FILTER               = '{default: '0};
  assign hwif_in.STATS.DROP_LEN                  = '{default: '0};

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
endmodule
