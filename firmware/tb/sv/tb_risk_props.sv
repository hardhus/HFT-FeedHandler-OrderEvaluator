// tb_risk_props: checks the risk_props checker itself. Drives its ports directly: an accept with the path open,
// its handover, an accept and its cancel, a drain. Every property's antecedent is hit, so a clean run proves
// the checker quiet. With HFT_VIOLATE=<k> exactly one property is violated (numbers in test_sva_checkers.py);
// its assertion must fire. Values are set after a falling edge and sampled at the next rising edge.
`timescale 1ns / 1ps
module tb_risk_props;
  localparam int unsigned QD = 8;
  localparam int unsigned QP = 5;

  logic          clk = 1'b0;
  logic          rst = 1'b1;
  logic          accept = 1'b0;
  logic          ctl_kill = 1'b0;
  logic          ctl_tx_en = 1'b1;
  logic [  31:0] budget = 32'd5;
  logic [  31:0] tokens = 32'd5;
  logic          handover = 1'b0;
  logic [QP-1:0] cancelled = '0;
  logic [QP-1:0] held = '0;
  logic [QP-1:0] live = '0;
  logic          m_tx_valid = 1'b0;
  logic [  32:0] ref_next = 33'd1;
  logic [   4:0] credits = 5'd16;
  logic [   7:0] flags = 8'b0010_1010;
  logic [   7:0] defs = 8'b0010_1010;

  always begin
    #3.2 clk = ~clk;
  end

  risk_props #(
      .QD(QD),
      .QP(QP)
  ) u_props (
      .clk         (clk),
      .rst         (rst),
      .accept      (accept),
      .ctl_kill    (ctl_kill),
      .ctl_tx_en   (ctl_tx_en),
      .budget      (budget),
      .tokens      (tokens),
      .handover    (handover),
      .cancelled   (cancelled),
      .held        (held),
      .live        (live),
      .m_tx_valid  (m_tx_valid),
      .ref_next    (ref_next),
      .credits     (credits),
      .flags       (flags),
      .defs        (defs),
      .lim_wr      (1'b0),
      .prod2       (64'd0),
      .max_notional(64'd0)
  );

  // 1 when this run violates property k.
  function automatic logic v(input int k);
`ifdef HFT_VIOLATE
    return k == `HFT_VIOLATE;
`else
    return k < 0;
`endif  // HFT_VIOLATE
  endfunction

  task automatic idle(input int unsigned n = 1);
    repeat (n) begin
      @(negedge clk);
      accept     = 1'b0;
      ctl_kill   = 1'b0;
      ctl_tx_en  = 1'b1;
      budget     = 32'd5;
      tokens     = 32'd5;
      handover   = 1'b0;
      cancelled  = '0;
      held       = '0;
      live       = '0;
      m_tx_valid = 1'b0;
      ref_next   = 33'd1;
      credits    = 5'd16;
      flags      = 8'b0010_1010;
      defs       = 8'b0010_1010;
    end
  endtask

  initial begin
    repeat (2) @(negedge clk);
    rst = 1'b0;
    idle(2);

    // An accept on an open path (budget and a token left), then its handover.
    @(negedge clk);
    accept    = 1'b1;
    ctl_kill  = v(1);
    ctl_tx_en = !v(2);
    budget    = v(3) ? 32'd0 : 32'd1;
    tokens    = v(4) ? 32'd0 : 32'd1;
    credits   = v(9) ? 5'd0 : 5'd1;
    @(negedge clk);
    accept     = 1'b0;
    ctl_kill   = 1'b0;
    ctl_tx_en  = 1'b1;
    budget     = 32'd5;
    tokens     = 32'd5;
    live       = 5'd1;
    m_tx_valid = 1'b1;
    handover   = 1'b1;
    ref_next   = v(8) ? 33'h1_0000_0000 : 33'hFFFF_FFFF;
    idle();

    // An accept and its cancel (live 1 -> held 1), the drain, then a handover with nothing accepted.
    @(negedge clk);
    accept = 1'b1;
    @(negedge clk);
    accept    = 1'b0;
    live      = 5'd1;
    ctl_kill  = 1'b1;
    cancelled = 5'd1;
    @(negedge clk);
    ctl_kill   = 1'b1;
    cancelled  = '0;
    live       = '0;
    held       = v(6) ? 5'd9 : 5'd1;
    credits    = v(10) ? 5'd17 : 5'd16;
    defs       = v(11) ? 8'b0010_1000 : 8'b0010_1010;
    m_tx_valid = v(7);
    idle();
    @(negedge clk);
    handover = v(5);
    idle(3);
    $display("HFT_TB_RESULT: PASS");
    $finish;
  end
endmodule
