// axil_route2: AXI4-Lite 1:2 address router. An access whose address is in one of the N_WIN windows goes to
// m1, every other one to m0.
// Clock domain : clk
// Latency      : +1 cycle per access (the input skid buffers); responses pass through combinationally.
// Throughput   : 1 request/cycle per channel to one slave, as the slave takes them (accesses to the same slave
//                do not wait for responses); switching to the other slave waits until every access in flight
//                has been answered, so responses come back in request order across both slaves (decision 41, A39).
// Order        : AW, W and AR each go through a skid buffer (ready from a flop). With a read and a write (AW and
//                W) both waiting, the read goes first unless the previous access was a read: the rule of the
//                PeakRDL-regblock AXI4-Lite interface behind it. A write offers AW and W together; until the slave
//                has taken both, nothing else starts.
// Windows      : window i is the addresses a with (a & WIN_MASK[i]) == WIN_BASE[i] (packed, window 0 in the
//                low ADDR_W bits).
module axil_route2 #(
    parameter int unsigned                    ADDR_W   = 16,
    parameter int unsigned                    DATA_W   = 32,
    parameter int unsigned                    N_WIN    = 1,
    parameter logic        [N_WIN*ADDR_W-1:0] WIN_BASE = '0,
    parameter logic        [N_WIN*ADDR_W-1:0] WIN_MASK = '0
) (
    input logic clk,
    input logic rst,

    input  logic                s_axil_awvalid,
    output logic                s_axil_awready,
    input  logic [  ADDR_W-1:0] s_axil_awaddr,
    input  logic [         2:0] s_axil_awprot,
    input  logic                s_axil_wvalid,
    output logic                s_axil_wready,
    input  logic [  DATA_W-1:0] s_axil_wdata,
    input  logic [DATA_W/8-1:0] s_axil_wstrb,
    output logic                s_axil_bvalid,
    input  logic                s_axil_bready,
    output logic [         1:0] s_axil_bresp,
    input  logic                s_axil_arvalid,
    output logic                s_axil_arready,
    input  logic [  ADDR_W-1:0] s_axil_araddr,
    input  logic [         2:0] s_axil_arprot,
    output logic                s_axil_rvalid,
    input  logic                s_axil_rready,
    output logic [  DATA_W-1:0] s_axil_rdata,
    output logic [         1:0] s_axil_rresp,

    // Master side, index 0 = m0, 1 = m1. Address, data and prot go to both; only the valids are routed.
    output logic [         1:0] m_axil_awvalid,
    input  logic [         1:0] m_axil_awready,
    output logic [  ADDR_W-1:0] m_axil_awaddr,
    output logic [         2:0] m_axil_awprot,
    output logic [         1:0] m_axil_wvalid,
    input  logic [         1:0] m_axil_wready,
    output logic [  DATA_W-1:0] m_axil_wdata,
    output logic [DATA_W/8-1:0] m_axil_wstrb,
    input  logic [         1:0] m_axil_bvalid,
    output logic [         1:0] m_axil_bready,
    input  logic [         3:0] m_axil_bresp,    // {m1, m0}
    output logic [         1:0] m_axil_arvalid,
    input  logic [         1:0] m_axil_arready,
    output logic [  ADDR_W-1:0] m_axil_araddr,
    output logic [         2:0] m_axil_arprot,
    input  logic [         1:0] m_axil_rvalid,
    output logic [         1:0] m_axil_rready,
    input  logic [2*DATA_W-1:0] m_axil_rdata,    // {m1, m0}
    input  logic [         3:0] m_axil_rresp     // {m1, m0}
);
  if (N_WIN == 0 || DATA_W % 8 != 0) begin : g_check
    $error("axil_route2: N_WIN must be >= 1 and DATA_W a multiple of 8");
  end

  function automatic logic in_win(input logic [ADDR_W-1:0] a);
    logic hit = 1'b0;
    for (int i = 0; i < N_WIN; i++) begin
      hit |= (a & WIN_MASK[i*ADDR_W+:ADDR_W]) == WIN_BASE[i*ADDR_W+:ADDR_W];
    end
    return hit;
  endfunction

  // ---- input skid buffers: channel 0 = AW {prot, addr}, 1 = W {strb, data}, 2 = AR {prot, addr} ----
  localparam int unsigned PW = (ADDR_W + 3 > DATA_W + DATA_W / 8) ? ADDR_W + 3 : DATA_W + DATA_W / 8;
  logic [2:0] in_valid, in_ready, head_v, skid_v, pop;
  logic [PW-1:0] in_data[3], head[3], skid[3];

  assign in_valid       = {s_axil_arvalid, s_axil_wvalid, s_axil_awvalid};
  assign in_data[0]     = PW'({s_axil_awprot, s_axil_awaddr});
  assign in_data[1]     = PW'({s_axil_wstrb, s_axil_wdata});
  assign in_data[2]     = PW'({s_axil_arprot, s_axil_araddr});
  assign in_ready       = ~skid_v;  // from a flop
  assign s_axil_awready = in_ready[0];
  assign s_axil_wready  = in_ready[1];
  assign s_axil_arready = in_ready[2];

  for (genvar c = 0; c < 3; c++) begin : g_skid
    // head: the oldest request; skid: the one taken in a cycle the head was full and not popped
    always_ff @(posedge clk) begin
      if (!head_v[c] || pop[c]) begin
        head[c] <= skid_v[c] ? skid[c] : in_data[c];
      end
      if (in_valid[c] && in_ready[c] && head_v[c] && !pop[c]) begin
        skid[c] <= in_data[c];
      end
      if (rst) begin
        head_v[c] <= 1'b0;
        skid_v[c] <= 1'b0;
      end else begin
        if (!head_v[c] || pop[c]) begin
          head_v[c] <= skid_v[c] || (in_valid[c] && in_ready[c]);
          skid_v[c] <= 1'b0;
        end else if (in_valid[c] && in_ready[c]) begin
          skid_v[c] <= 1'b1;
        end
      end
    end
  end

  logic [ADDR_W-1:0] aw_addr, ar_addr;
  assign aw_addr       = head[0][ADDR_W-1:0];
  assign ar_addr       = head[2][ADDR_W-1:0];
  assign m_axil_awaddr = aw_addr;
  assign m_axil_awprot = head[0][ADDR_W+:3];
  assign m_axil_wdata  = head[1][DATA_W-1:0];
  assign m_axil_wstrb  = head[1][DATA_W+:DATA_W/8];
  assign m_axil_araddr = ar_addr;
  assign m_axil_arprot = head[2][ADDR_W+:3];

  // ---- issue ----
  logic cur_q, prev_rd_q;  // cur_q: the slave of every access in flight (1 = m1)
  logic [2:0] cnt_q;  // accesses started and not yet answered upstream
  logic
      aw_pend_q, w_pend_q, ar_pend_q, pend_sel_q;  // a started request the slave has not taken yet
  logic pend, want_rd, want_wr, tgt, start_rd, start_wr, start, sel, aw_issue, w_issue, ar_issue;
  logic aw_take, w_take, ar_take, resp;

  assign pend     = aw_pend_q || w_pend_q || ar_pend_q;
  // The rule of the regblock behind: read first, unless the previous access was a read and a write waits.
  assign want_rd  = head_v[2] && (!prev_rd_q || !(head_v[0] && head_v[1]));
  assign want_wr  = head_v[0] && head_v[1] && !want_rd;
  assign tgt      = want_rd ? in_win(ar_addr) : in_win(aw_addr);
  // Same slave: start at once; the other slave: only once nothing is in flight.
  assign start    = !pend && (want_rd || want_wr) && (cnt_q == '0 || tgt == cur_q);
  assign start_rd = start && want_rd;
  assign start_wr = start && want_wr;
  assign sel      = pend ? pend_sel_q : tgt;

  assign aw_issue = start_wr || aw_pend_q;
  assign w_issue  = start_wr || w_pend_q;
  assign ar_issue = start_rd || ar_pend_q;
  assign aw_take  = aw_issue && m_axil_awready[sel];
  assign w_take   = w_issue && m_axil_wready[sel];
  assign ar_take  = ar_issue && m_axil_arready[sel];
  assign pop      = {ar_take, w_take, aw_take};

  always_comb begin
    m_axil_awvalid       = '0;
    m_axil_wvalid        = '0;
    m_axil_arvalid       = '0;
    m_axil_bready        = '0;
    m_axil_rready        = '0;
    m_axil_awvalid[sel]  = aw_issue;
    m_axil_wvalid[sel]   = w_issue;
    m_axil_arvalid[sel]  = ar_issue;
    m_axil_bready[cur_q] = s_axil_bready && cnt_q != '0;
    m_axil_rready[cur_q] = s_axil_rready && cnt_q != '0;
  end

  // ---- responses: from the slave of the accesses in flight ----
  assign s_axil_bvalid = m_axil_bvalid[cur_q] && cnt_q != '0;
  assign s_axil_bresp  = m_axil_bresp[2*int'(cur_q)+:2];
  assign s_axil_rvalid = m_axil_rvalid[cur_q] && cnt_q != '0;
  assign s_axil_rdata  = m_axil_rdata[DATA_W*int'(cur_q)+:DATA_W];
  assign s_axil_rresp  = m_axil_rresp[2*int'(cur_q)+:2];
  assign resp          = (s_axil_bvalid && s_axil_bready) || (s_axil_rvalid && s_axil_rready);

  always_ff @(posedge clk) begin
    if (start) begin
      cur_q      <= tgt;
      pend_sel_q <= tgt;
    end
    if (rst) begin
      cnt_q     <= '0;
      prev_rd_q <= 1'b0;
      aw_pend_q <= 1'b0;
      w_pend_q  <= 1'b0;
      ar_pend_q <= 1'b0;
    end else begin
      cnt_q <= cnt_q + 3'(start) - 3'(resp);
      if (start) begin
        prev_rd_q <= start_rd;
        aw_pend_q <= start_wr && !aw_take;
        w_pend_q  <= start_wr && !w_take;
        ar_pend_q <= start_rd && !ar_take;
      end else begin
        aw_pend_q <= aw_pend_q && !aw_take;
        w_pend_q  <= w_pend_q && !w_take;
        ar_pend_q <= ar_pend_q && !ar_take;
      end
    end
  end
endmodule
