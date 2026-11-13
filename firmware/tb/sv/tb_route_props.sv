// tb_route_props: checks the route_props checker itself. Drives its ports directly: a write to m0, a read to m1,
// two reads to m1 in flight together (pipelined), then a switch to m0 after both answers. Every property's
// antecedent is hit, so a clean run proves the checker quiet. With HFT_VIOLATE=<k> exactly one property is
// violated (numbers in test_sva_checkers.py); its assertion must fire. Values are set after a falling edge and
// sampled at the next rising edge.
`timescale 1ns / 1ps
module tb_route_props;
  localparam logic [15:0] OUT_ADDR = 16'h0100;  // not in a window: m0
  localparam logic [15:0] IN_ADDR = 16'h1004;  // in the window 0x1000-0x1FFF: m1

  logic clk = 1'b0;
  logic rst = 1'b1;
  logic s_bvalid = 1'b0, s_bready = 1'b0, s_rvalid = 1'b0, s_rready = 1'b0;
  logic [1:0] m_awvalid = '0, m_awready = '0, m_wvalid = '0, m_arvalid = '0, m_arready = '0;
  logic [1:0] m_bvalid = '0, m_rvalid = '0;
  logic [15:0] m_awaddr = '0, m_araddr = '0;
  logic m0_win_req = 1'b0;

  always begin
    #2 clk = ~clk;
  end

  route_props #(
      .ADDR_W  (16),
      .N_WIN   (2),
      .WIN_BASE({16'h2000, 16'h1000}),
      .WIN_MASK({16'hF000, 16'hF000})
  ) u_props (
      .clk       (clk),
      .rst       (rst),
      .s_bvalid  (s_bvalid),
      .s_bready  (s_bready),
      .s_rvalid  (s_rvalid),
      .s_rready  (s_rready),
      .m_awvalid (m_awvalid),
      .m_awready (m_awready),
      .m_awaddr  (m_awaddr),
      .m_wvalid  (m_wvalid),
      .m_arvalid (m_arvalid),
      .m_arready (m_arready),
      .m_araddr  (m_araddr),
      .m_bvalid  (m_bvalid),
      .m_rvalid  (m_rvalid),
      .m0_win_req(m0_win_req)
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
      {s_bvalid, s_bready, s_rvalid, s_rready}                                   = '0;
      {m_awvalid, m_awready, m_wvalid, m_arvalid, m_arready, m_bvalid, m_rvalid} = '0;
      m0_win_req                                                                 = 1'b0;
    end
  endtask

  // A request taken by slave i in one cycle.
  task automatic read_req(input int i, input logic [15:0] addr);
    @(negedge clk);
    {m_awvalid, m_wvalid, m_bvalid, m_rvalid, s_rvalid, s_bvalid} = '0;
    m_arvalid                                                     = '0;
    m_arready                                                     = '0;
    m_arvalid[i]                                                  = 1'b1;
    m_arready[i]                                                  = 1'b1;
    m_araddr                                                      = addr;
  endtask

  task automatic read_resp(input int i);
    @(negedge clk);
    {m_arvalid, m_arready, m_awvalid, m_wvalid} = '0;
    m_rvalid                                    = '0;
    m_rvalid[i]                                 = 1'b1;
    s_rvalid                                    = 1'b1;
    s_rready                                    = 1'b1;
  endtask

  initial begin
    repeat (2) @(negedge clk);
    rst = 1'b0;
    idle(2);

    // A write to m0 and its answer.
    @(negedge clk);
    m_awvalid[0] = 1'b1;
    m_awready[0] = 1'b1;
    m_wvalid[0]  = 1'b1;
    m_awaddr     = v(1) ? IN_ADDR : OUT_ADDR;  // 1: a_route_aw
    @(negedge clk);
    {m_awvalid, m_awready, m_wvalid} = '0;
    m_bvalid[0]                      = 1'b1;
    s_bvalid                         = 1'b1;
    s_bready                         = 1'b1;
    idle();

    // A read to m1 and its answer.
    read_req(1, v(2) ? OUT_ADDR : IN_ADDR);  // 2: a_route_ar
    read_resp(1);
    idle();

    // Two reads to m1 in flight together, then their answers.
    read_req(1, IN_ADDR);
    if (v(
            4
        )) begin  // 4: a_switch_drained: a read taken by m0 while the m1 read waits for its answer
      read_req(0, OUT_ADDR);
    end else begin
      read_req(1, IN_ADDR);
    end
    read_resp(1);
    read_resp(1);
    idle();

    // The switch to m0 after both answers.
    read_req(0, OUT_ADDR);
    read_resp(0);
    idle();

    // Requests offered to both slaves at once (not taken).
    if (v(3)) begin  // 3: a_one_slave
      @(negedge clk);
      m_arvalid[0] = 1'b1;
      m_araddr     = OUT_ADDR;
      m_awvalid[1] = 1'b1;
      m_awaddr     = IN_ADDR;
      idle();
    end

    if (v(5)) begin  // 5: a_resp_owned: an answer with nothing in flight
      @(negedge clk);
      m_bvalid[1] = 1'b1;
      idle();
    end

    @(negedge clk);
    m0_win_req = v(6);  // 6: a_m0_win_quiet
    idle(3);
    $display("HFT_TB_RESULT: PASS");
    $finish;
  end
endmodule
