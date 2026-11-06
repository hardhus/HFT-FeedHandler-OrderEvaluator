// tb_strategy_props: checks the strategy_props checker itself. Drives its ports directly: a legal fire, its
// queue write, its accepted packet and its order; a fire in the cycle of a packet event; a rejected packet.
// Every property's antecedent is hit, so a clean run proves the checker quiet. With HFT_VIOLATE=<k> exactly
// one property is violated (numbers in test_sva_checkers.py); its assertion must fire. Values are set after
// a falling edge and sampled at the next rising edge.
`timescale 1ns / 1ps
module tb_strategy_props;
  localparam int unsigned N = 8;
  localparam int unsigned DEPTH = 16;
  localparam int unsigned PTR_W = 5;

  logic             clk = 1'b0;
  logic             rst = 1'b1;
  logic [    N-1:0] fire = '0;
  logic [    N-1:0] armed_p = '1;
  logic [    N-1:0] fired_p = '0;
  logic [    N-1:0] err = '0;
  logic             path_open = 1'b1;
  logic             fire_q = 1'b0;
  logic             pkt_q = 1'b0;
  logic             pkt_acc_q = 1'b0;
  logic [PTR_W-1:0] fill = '0;
  logic             m_ord_valid = 1'b0;

  always begin
    #3.2 clk = ~clk;
  end

  strategy_props #(
      .N    (N),
      .DEPTH(DEPTH),
      .PTR_W(PTR_W)
  ) u_props (
      .clk        (clk),
      .rst        (rst),
      .fire       (fire),
      .armed_p    (armed_p),
      .fired_p    (fired_p),
      .err        (err),
      .path_open  (path_open),
      .fire_q     (fire_q),
      .pkt_q      (pkt_q),
      .pkt_acc_q  (pkt_acc_q),
      .fill       (fill),
      .m_ord_valid(m_ord_valid)
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
      fire        = '0;
      armed_p     = '1;
      fired_p     = '0;
      err         = '0;
      path_open   = 1'b1;
      fire_q      = 1'b0;
      pkt_q       = 1'b0;
      pkt_acc_q   = 1'b0;
      fill        = '0;
      m_ord_valid = 1'b0;
    end
  endtask

  initial begin
    repeat (2) @(negedge clk);
    rst = 1'b0;
    idle(2);

    // A fire of rule 2 (rule 0 fired before in this packet), its write, the accepted packet, the order.
    @(negedge clk);
    fire      = 8'b0000_0100;
    fired_p   = 8'b0000_0001;
    armed_p   = v(2) ? 8'b0000_0001 : 8'b0000_0101;
    err       = 8'b1000_0000;
    path_open = !v(7);
    if (v(1)) begin
      fire    = 8'b0001_0100;
      armed_p = 8'b0001_0101;
    end
    if (v(3)) begin
      fired_p = 8'b0000_0101;
    end
    @(negedge clk);
    fire    = '0;
    fired_p = '0;
    armed_p = '1;
    err     = '0;
    fire_q  = 1'b1;
    fill    = v(6) ? PTR_W'(DEPTH) : PTR_W'(DEPTH - 1);
    @(negedge clk);
    fire_q    = 1'b0;
    fill      = '0;
    pkt_q     = 1'b1;
    pkt_acc_q = 1'b1;
    @(negedge clk);
    pkt_q       = 1'b0;
    pkt_acc_q   = 1'b0;
    m_ord_valid = !v(5);
    idle(3);

    // A fire in the cycle of an accepted event with no order: the fire is the next packet's, which is rejected.
    @(negedge clk);
    fire_q    = 1'b1;
    pkt_q     = 1'b1;
    pkt_acc_q = 1'b1;
    @(negedge clk);
    fire_q    = 1'b0;
    pkt_q     = 1'b1;
    pkt_acc_q = 1'b0;
    @(negedge clk);
    pkt_q       = 1'b0;
    m_ord_valid = v(4);
    idle(3);
    $display("HFT_TB_RESULT: PASS");
    $finish;
  end
endmodule
