// symbol_filter: stock-locate bitmap (one bit per locate, 1 = subscribed) in one block RAM, with
// a lookup port for the decoder and a command port for the host (through hft_csr_wrap).
// Clock domain : clk (net_clk), RAM included: one clock, so a lookup and a write of the same word
//                in the same cycle are defined (UG473 Conflict Avoidance, common clock)
// Latency      : lk_locate -> lk_hit 1 cycle; an accepted command -> sym_rsp_valid 1 cycle
// Layout       : bit i of word w = locate 32w + i
// Write/lookup : port A writes READ_FIRST; a lookup in the cycle a command writes its word sees the
//                old word, a lookup from the next cycle on the new one. So sym_rsp_valid of a write
//                means: every later lookup sees it
// Commands     : one per cycle at most (sym_req_valid && sym_req_ready); sym_rsp_rdata is the word
//                for a read (not defined for a write, whose response is the acknowledgement)
// Reset        : the RAM has no reset. After rst every word is written 0 (2048 cycles); meanwhile
//                sym_req_ready is 0 and a lookup gives 0 (not subscribed), so the bitmap after a
//                reset is always all 0 (deterministic, as after power-up)
module symbol_filter (
    input logic clk,
    input logic rst,

    input  logic [15:0] lk_locate,  // lookup, every cycle
    output logic        lk_hit,     // bit of lk_locate one cycle earlier; 0 while sweeping

    input  logic        sym_req_valid,
    output logic        sym_req_ready,
    input  logic        sym_req_wr,
    input  logic [10:0] sym_req_addr,
    input  logic [31:0] sym_req_wdata,
    input  logic [ 3:0] sym_req_wstrb,
    output logic        sym_rsp_valid,
    output logic [31:0] sym_rsp_rdata
);
  localparam int unsigned N_WORDS = 2048;

  (* ram_style = "block" *)logic [31:0] mem          [N_WORDS];

  // ---- reset sweep ----
  logic        sweep_q;
  logic [10:0] sweep_addr_q;

  always_ff @(posedge clk) begin
    if (rst) begin
      sweep_q      <= 1'b1;
      sweep_addr_q <= '0;
    end else if (sweep_q) begin
      sweep_addr_q <= sweep_addr_q + 11'd1;
      if (sweep_addr_q == 11'(N_WORDS - 1)) begin
        sweep_q <= 1'b0;
      end
    end
  end

  // ---- port A: sweep or host command (READ_FIRST, byte writes) ----
  logic        acc;
  logic        a_en;
  logic [10:0] a_addr;
  logic [31:0] a_wdata;
  logic [ 3:0] a_we;

  assign sym_req_ready = !sweep_q && !rst;
  assign acc           = sym_req_valid && sym_req_ready;
  assign a_en          = acc || (sweep_q && !rst);
  assign a_addr        = sweep_q ? sweep_addr_q : sym_req_addr;
  assign a_wdata       = sweep_q ? '0 : sym_req_wdata;
  assign a_we          = sweep_q ? 4'hF : (acc && sym_req_wr) ? sym_req_wstrb : 4'h0;

  always_ff @(posedge clk) begin
    if (a_en) begin
      for (int b = 0; b < 4; b++) begin
        if (a_we[b]) begin
          mem[a_addr][8*b+:8] <= a_wdata[8*b+:8];
        end
      end
      sym_rsp_rdata <= mem[a_addr];
    end
  end

  always_ff @(posedge clk) begin
    sym_rsp_valid <= rst ? 1'b0 : acc;
  end

  // ---- port B: lookup ----
  logic [31:0] lk_word_q;
  logic [ 4:0] lk_bit_q;
  logic        lk_sweep_q;

  always_ff @(posedge clk) begin
    lk_word_q  <= mem[lk_locate[15:5]];
    lk_bit_q   <= lk_locate[4:0];
    lk_sweep_q <= sweep_q;
  end

  assign lk_hit = lk_word_q[lk_bit_q] && !lk_sweep_q;
endmodule
