// tb_ring_props: checks the ring_props checker itself. Drives its ports directly: an enable, bursts over a whole
// pass of a 16-entry ring until it is full, one more after the host frees a slot, up to the limit of bursts
// unanswered. Every property's antecedent is hit, so a clean run proves the checker quiet. With HFT_VIOLATE=<k>
// exactly one property is violated (numbers in test_sva_checkers.py); its assertion must fire. Values are set
// after a falling edge and sampled at the next rising edge.
`timescale 1ns / 1ps
module tb_ring_props;
  localparam int unsigned MAX_OUTSTANDING = 4;
  localparam int unsigned OUT_W = 3;
  localparam logic [31:0] MASK = 32'd15;
  localparam logic [51:0] BASE = 52'h12345;

  logic        clk = 1'b0;
  logic        rst = 1'b1;
  logic        enable_q = 1'b0;
  logic        start = 1'b0;
  logic [31:0] cfg_cons = '0;
  logic [ 4:0] cfg_size_log2 = 5'd8;
  localparam logic [4:0] BAD_SIZES[2] = '{5'd3, 5'd21};
  logic             take = 1'b0;
  logic             fifo_rd_en = 1'b0;
  logic [     31:0] mask_q = MASK;
  logic [     51:0] base_q = BASE;
  logic [     31:0] prod_q = '0;
  logic [     31:0] cons_q = '0;
  logic [     56:0] addr_q = {BASE, 5'd0};
  logic             phase_q = 1'b1;
  logic [OUT_W-1:0] out_q = '0;

  always begin
    #2 clk = ~clk;
  end

  ring_props #(
      .MAX_OUTSTANDING(MAX_OUTSTANDING),
      .OUT_W          (OUT_W)
  ) u_props (
      .clk          (clk),
      .rst          (rst),
      .enable_q     (enable_q),
      .start        (start),
      .cfg_cons     (cfg_cons),
      .cfg_size_log2(cfg_size_log2),
      .take         (take),
      .fifo_rd_en   (fifo_rd_en),
      .mask_q       (mask_q),
      .base_q       (base_q),
      .prod_q       (prod_q),
      .cons_q       (cons_q),
      .addr_q       (addr_q),
      .phase_q      (phase_q),
      .out_q        (out_q)
  );

  // 1 when this run violates property k.
  function automatic logic v(input int k);
`ifdef HFT_VIOLATE
    return k == `HFT_VIOLATE;
`else
    return k < 0;
`endif  // HFT_VIOLATE
  endfunction

  // prod_q and everything that follows from it.
  task automatic set_prod(input logic [31:0] prod);
    prod_q  = prod;
    addr_q  = {BASE, 5'd0} + 57'(prod & MASK);
    phase_q = (prod & (MASK + 32'd1)) == '0;
  endtask

  task automatic idle(input int unsigned n = 1);
    repeat (n) begin
      @(negedge clk);
      start      = 1'b0;
      take       = 1'b0;
      fifo_rd_en = 1'b0;
    end
  endtask

  // One burst: take with its pop, prod moves in the next cycle.
  task automatic burst();
    @(negedge clk);
    take       = 1'b1;
    fifo_rd_en = 1'b1;
    @(negedge clk);
    take       = 1'b0;
    fifo_rd_en = 1'b0;
    set_prod(prod_q + 32'd1);
  endtask

  initial begin
    // A stale prod from an earlier run, set during reset.
    @(negedge clk);
    set_prod(32'd5);
    @(negedge clk);
    rst = 1'b0;
    idle(2);

    // An enable with a stale cons: refused.
    @(negedge clk);
    cfg_cons = 32'd9;
    start    = 1'b1;
    @(negedge clk);
    start    = 1'b0;
    cfg_cons = '0;
    enable_q = v(8);  // 8: a_enable_refused
    idle();
    enable_q = 1'b0;
    idle();

    // Enables with a size just out of range, on both sides: refused.
    foreach (BAD_SIZES[i]) begin
      @(negedge clk);
      cfg_size_log2 = BAD_SIZES[i];
      start         = 1'b1;
      @(negedge clk);
      start         = 1'b0;
      cfg_size_log2 = 5'd8;
      enable_q      = v(9);  // 9: a_enable_bad_size
      idle();
      enable_q = 1'b0;
      idle();
    end

    // The enable: prod goes to 0 in the next cycle.
    @(negedge clk);
    start = 1'b1;
    @(negedge clk);
    start    = 1'b0;
    enable_q = 1'b1;
    if (!v(4)) begin  // 4: a_prod_start
      set_prod('0);
    end
    idle(2);
    if (v(4)) begin
      $finish;
    end

    // One whole pass: the ring is full. Then the host frees one slot and one more goes, phase 0.
    repeat (16) begin
      burst();
    end
    idle(2);
    if (v(1)) begin  // 1: a_no_take_when_full
      @(negedge clk);
      take = 1'b1;
      set_prod(prod_q);
      @(negedge clk);
      take = 1'b0;
      set_prod(prod_q + 32'd1);
    end
    if (v(2)) begin  // 2: a_no_pop_when_full
      @(negedge clk);
      fifo_rd_en = 1'b1;
    end
    idle();
    @(negedge clk);
    cons_q = 32'd1 + (v(1) ? 32'd1 : 32'd0);
    idle(2);
    burst();
    idle();

    if (v(3)) begin  // 3: a_prod_step
      @(negedge clk);
      set_prod(prod_q + 32'd2);
      idle();
    end
    if (v(5)) begin  // 5: a_phase
      @(negedge clk);
      phase_q = !phase_q;
      idle();
    end
    if (v(6)) begin  // 6: a_addr
      @(negedge clk);
      addr_q = addr_q + 57'd1;
      idle();
    end

    // Bursts unanswered, up to the limit.
    @(negedge clk);
    out_q = OUT_W'(MAX_OUTSTANDING) + (v(7) ? OUT_W'(1) : '0);  // 7: a_outstanding
    idle(2);
    $display("HFT_TB_RESULT: PASS");
    $finish;
  end
endmodule
