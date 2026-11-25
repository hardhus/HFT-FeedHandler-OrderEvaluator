// axil_cpuif2: AXI4-Lite slave in front of two PeakRDL-regblock register blocks built with --cpuif passthrough
// (A40). An access whose address is in one of the N_WIN windows goes to block 1, every other one to block 0.
// The request to the blocks comes from flops (req, is_wr, addr, wr_data, wr_biten), so the blocks' address
// decode starts at a register; the arbitration runs one cycle earlier, from the input skid buffers.
// Clock domain : clk
// Latency      : 2 cycles from the AXI handshake to the block's request (skid buffer head, request register)
//                and 1 from the block's ack to the AXI response (response buffer): the same as the axil_route2
//                router and regblock AXI4-Lite interfaces this module replaces
// Throughput   : 1 request/cycle to one block, as the block takes them (accesses to the same block do not wait
//                for responses, up to DEPTH in flight); switching to the other block waits until every access in
//                flight has been answered, so responses come back in request order across both blocks
//                (decision 43).
// Order        : AW, W and AR each go through a skid buffer (ready from a flop). With a read and a write (AW and
//                W) both waiting, the read goes first unless the previous access was a read. A write takes AW and
//                W together.
// Protocol     : the regblock internal CPUIF protocol: a stalled request is held unchanged until taken; acks are
//                strobes with no backpressure, so they go into a DEPTH-entry response buffer and no request
//                starts while DEPTH accesses are unanswered. SLVERR (2'b10) when the block reports an error.
// Windows      : window i is the addresses a with (a & WIN_MASK[i]) == WIN_BASE[i] (packed, window 0 in the
//                low ADDR_W bits).
module axil_cpuif2 #(
    parameter int unsigned                    ADDR_W   = 16,
    parameter int unsigned                    DATA_W   = 32,
    parameter int unsigned                    N_WIN    = 1,
    parameter logic        [N_WIN*ADDR_W-1:0] WIN_BASE = '0,
    parameter logic        [N_WIN*ADDR_W-1:0] WIN_MASK = '0,
    parameter int unsigned                    DEPTH    = 4
) (
    input logic clk,
    input logic rst,

    input  logic                s_axil_awvalid,
    output logic                s_axil_awready,
    input  logic [  ADDR_W-1:0] s_axil_awaddr,
    /* verilator lint_off UNUSEDSIGNAL */  // AxPROT: the register blocks grant every access
    input  logic [         2:0] s_axil_awprot,
    /* verilator lint_on UNUSEDSIGNAL */
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
    /* verilator lint_off UNUSEDSIGNAL */  // AxPROT: the register blocks grant every access
    input  logic [         2:0] s_axil_arprot,
    /* verilator lint_on UNUSEDSIGNAL */
    output logic                s_axil_rvalid,
    input  logic                s_axil_rready,
    output logic [  DATA_W-1:0] s_axil_rdata,
    output logic [         1:0] s_axil_rresp,

    // Block side, index 0 = block 0, 1 = block 1. is_wr, addr and the write data go to both; only req is
    // routed.
    output logic [         1:0] m_req,
    output logic                m_req_is_wr,
    output logic [  ADDR_W-1:0] m_addr,
    output logic [  DATA_W-1:0] m_wr_data,
    output logic [  DATA_W-1:0] m_wr_biten,
    input  logic [         1:0] m_req_stall_wr,
    input  logic [         1:0] m_req_stall_rd,
    input  logic [         1:0] m_rd_ack,
    input  logic [         1:0] m_rd_err,
    input  logic [2*DATA_W-1:0] m_rd_data,       // {block 1, block 0}
    input  logic [         1:0] m_wr_ack,
    input  logic [         1:0] m_wr_err
);
  localparam int unsigned CW = $clog2(DEPTH + 1);
  localparam int unsigned PTR_W = $clog2(DEPTH);

  if (N_WIN == 0 || DATA_W % 8 != 0 || DEPTH < 2 || (DEPTH & (DEPTH - 1)) != 0) begin : g_check
    $error("axil_cpuif2: N_WIN must be >= 1, DATA_W a multiple of 8 and DEPTH a power of two >= 2");
  end

  function automatic logic in_win(input logic [ADDR_W-1:0] a);
    logic hit = 1'b0;
    for (int i = 0; i < N_WIN; i++) begin
      hit |= (a & WIN_MASK[i*ADDR_W+:ADDR_W]) == WIN_BASE[i*ADDR_W+:ADDR_W];
    end
    return hit;
  endfunction

  // ---- input skid buffers: channel 0 = AW {addr}, 1 = W {strb, data}, 2 = AR {addr} (prot is not used) ----
  localparam int unsigned PW = (ADDR_W > DATA_W + DATA_W / 8) ? ADDR_W : DATA_W + DATA_W / 8;
  logic [2:0] in_valid, in_ready, head_v, skid_v, pop;
  logic [PW-1:0] in_data[3], head[3], skid[3];

  assign in_valid       = {s_axil_arvalid, s_axil_wvalid, s_axil_awvalid};
  assign in_data[0]     = PW'(s_axil_awaddr);
  assign in_data[1]     = PW'({s_axil_wstrb, s_axil_wdata});
  assign in_data[2]     = PW'(s_axil_araddr);
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
  logic [DATA_W/8-1:0] wstrb;
  assign aw_addr = head[0][ADDR_W-1:0];
  assign ar_addr = head[2][ADDR_W-1:0];
  assign wstrb   = head[1][DATA_W+:DATA_W/8];

  // ---- issue: the request register ----
  logic [1:0] req_q;  // one-hot: the block the request in the register goes to
  logic is_wr_q, cur_q, prev_rd_q;  // cur_q: the block of every access in flight (1 = block 1)
  logic [ADDR_W-1:0] addr_q;
  logic [DATA_W-1:0] wdata_q, biten_q;
  logic [CW-1:0] cnt_q;  // accesses started and not yet answered upstream
  logic [   1:0] stall;
  logic taken, free, want_rd, want_wr, tgt, start, start_rd, start_wr, resp;

  assign stall = is_wr_q ? m_req_stall_wr : m_req_stall_rd;
  assign taken = |(req_q & ~stall);  // the block takes the request this cycle
  assign free = req_q == '0 || taken;
  assign want_rd = head_v[2] && (!prev_rd_q || !(head_v[0] && head_v[1]));
  assign want_wr = head_v[0] && head_v[1] && !want_rd;
  assign tgt = want_rd ? in_win(ar_addr) : in_win(aw_addr);
  // Same block: start at once; the other block: only once nothing is in flight. Never more than DEPTH
  // unanswered (the response buffer's size).
  assign start    = free && (want_rd || want_wr) && (cnt_q == '0 || tgt == cur_q) && cnt_q != CW'(DEPTH);
  assign start_rd = start && want_rd;
  assign start_wr = start && want_wr;
  assign pop = {start_rd, start_wr, start_wr};

  assign m_req = req_q;
  assign m_req_is_wr = is_wr_q;
  assign m_addr = addr_q;
  assign m_wr_data = wdata_q;
  assign m_wr_biten = biten_q;

  always_ff @(posedge clk) begin
    if (start) begin
      is_wr_q <= start_wr;
      cur_q   <= tgt;
      // word address, as the regblock AXI4-Lite interface gives it
      addr_q  <= {start_rd ? ar_addr[ADDR_W-1:2] : aw_addr[ADDR_W-1:2], 2'b00};
      wdata_q <= head[1][DATA_W-1:0];
      for (int i = 0; i < DATA_W / 8; i++) begin
        biten_q[i*8+:8] <= {8{wstrb[i]}};
      end
    end
    if (rst) begin
      req_q     <= '0;
      prev_rd_q <= 1'b0;
      cnt_q     <= '0;
    end else begin
      if (start) begin
        req_q     <= tgt ? 2'b10 : 2'b01;
        prev_rd_q <= start_rd;
      end else if (taken) begin
        req_q <= '0;
      end
      cnt_q <= cnt_q + CW'(start) - CW'(resp);
    end
  end

  // ---- responses: the acks of the block in flight into the response buffer ----
  logic rd_ack, wr_ack, push;
  logic [PTR_W:0] wptr_q, rptr_q;
  logic buf_wr[DEPTH], buf_err[DEPTH];
  logic [DATA_W-1:0] buf_data[DEPTH];
  logic [PTR_W-1:0] wi, ri;

  assign rd_ack = m_rd_ack[cur_q];
  assign wr_ack = m_wr_ack[cur_q];
  assign push   = rd_ack || wr_ack;
  assign wi     = wptr_q[PTR_W-1:0];
  assign ri     = rptr_q[PTR_W-1:0];

  always_ff @(posedge clk) begin
    if (push) begin
      buf_wr[wi]   <= !rd_ack;
      buf_err[wi]  <= rd_ack ? m_rd_err[cur_q] : m_wr_err[cur_q];
      buf_data[wi] <= m_rd_data[DATA_W*int'(cur_q)+:DATA_W];
    end
    if (rst) begin
      wptr_q <= '0;
      rptr_q <= '0;
    end else begin
      wptr_q <= wptr_q + (PTR_W + 1)'(push);
      rptr_q <= rptr_q + (PTR_W + 1)'(resp);
    end
  end

  assign s_axil_bvalid = wptr_q != rptr_q && buf_wr[ri];
  assign s_axil_rvalid = wptr_q != rptr_q && !buf_wr[ri];
  assign s_axil_rdata  = buf_data[ri];
  assign s_axil_bresp  = buf_err[ri] ? 2'b10 : 2'b00;
  assign s_axil_rresp  = buf_err[ri] ? 2'b10 : 2'b00;
  assign resp          = (s_axil_bvalid && s_axil_bready) || (s_axil_rvalid && s_axil_rready);
endmodule
