// tb_axi_wr_props: checks the axi_wr_props checker itself. Drives its ports directly: a burst with stalls on AW
// and W, one whose W comes before its AW, their B responses and a reset. Every property's antecedent is hit, so a
// clean run proves the checker quiet. With HFT_VIOLATE=<k> exactly one property is violated (numbers in
// test_sva_checkers.py); its assertion must fire. Values are set after a falling edge and sampled at the next
// rising edge.
`timescale 1ns / 1ps
module tb_axi_wr_props;
  localparam int unsigned DATA_W = 512;

  logic                clk = 1'b0;
  logic                rst = 1'b1;
  logic [        63:0] awaddr = '0;
  logic [         7:0] awlen = 8'd1;
  logic [         2:0] awsize = 3'd6;
  logic [         1:0] awburst = 2'b01;
  logic                awvalid = 1'b0;
  logic                awready = 1'b0;
  logic [  DATA_W-1:0] wdata = '0;
  logic [DATA_W/8-1:0] wstrb = '1;
  logic                wlast = 1'b0;
  logic                wvalid = 1'b0;
  logic                wready = 1'b0;
  logic                bvalid = 1'b0;
  logic                bready = 1'b1;

  always begin
    #2 clk = ~clk;
  end

  axi_wr_props #(
      .DATA_W(DATA_W)
  ) u_props (
      .clk    (clk),
      .rst    (rst),
      .awaddr (awaddr),
      .awlen  (awlen),
      .awsize (awsize),
      .awburst(awburst),
      .awvalid(awvalid),
      .awready(awready),
      .wdata  (wdata),
      .wstrb  (wstrb),
      .wlast  (wlast),
      .wvalid (wvalid),
      .wready (wready),
      .bvalid (bvalid),
      .bready (bready)
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
      awvalid = 1'b0;
      awready = 1'b0;
      wvalid  = 1'b0;
      wready  = 1'b0;
      wlast   = 1'b0;
      bvalid  = 1'b0;
    end
  endtask

  task automatic w_beat(input logic last, input logic [DATA_W-1:0] data);
    @(negedge clk);
    awvalid = 1'b0;
    awready = 1'b0;
    wvalid  = 1'b1;
    wready  = 1'b1;
    wlast   = last;
    wdata   = data;
    bvalid  = 1'b0;
  endtask

  task automatic response();
    idle();
    bvalid = 1'b1;
    idle();
  endtask

  initial begin
    repeat (2) @(negedge clk);
    rst = 1'b0;
    idle(2);

    // AW with a stall, then its W with a stall on the first beat.
    @(negedge clk);
    awaddr  = 64'h1000;
    awvalid = 1'b1;
    awburst = v(4) ? 2'b00 : 2'b01;  // 4: a_incr
    if (v(3)) begin  // 3: a_4k
      awaddr = 64'h1FF0;
    end
    @(negedge clk);
    if (v(1)) begin  // 1: a_aw_stable
      awaddr = 64'h2000;
    end
    awready = 1'b1;
    @(negedge clk);
    awvalid = 1'b0;
    awready = 1'b0;
    awburst = 2'b01;
    wvalid  = 1'b1;
    wdata   = 512'd1;
    @(negedge clk);
    if (v(2)) begin  // 2: a_w_stable
      wdata = 512'd2;
    end
    wready = 1'b1;
    wstrb  = v(5) ? '0 : '1;  // 5: a_wstrb
    w_beat(!v(6), 512'd3);  // 6: a_w_beats, with a third beat
    wstrb = '1;
    if (v(6)) begin
      w_beat(1'b1, 512'd4);
    end
    response();
    if (v(7)) begin  // 7: a_b_after_aw
      response();
    end
    idle(4);

    // W first, then its AW, then its B.
    w_beat(1'b0, 512'd5);
    w_beat(1'b1, 512'd6);
    idle();
    @(negedge clk);
    awaddr  = 64'h1F80;
    awvalid = 1'b1;
    awready = 1'b1;
    idle();
    response();
    idle(4);

    // A reset; VALID stays low after it.
    @(negedge clk);
    rst = 1'b1;
    @(negedge clk);
    rst = 1'b0;
    if (v(8)) begin  // 8: a_reset_valid
      awvalid = 1'b1;
      awready = 1'b1;
    end
    idle(4);
    $display("HFT_TB_RESULT: PASS");
    $finish;
  end
endmodule
