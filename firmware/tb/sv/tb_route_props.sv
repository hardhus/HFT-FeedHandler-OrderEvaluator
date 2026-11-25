// tb_route_props: checks the route_props checker itself. Drives its ports directly: a write to block 0 acked in
// the cycle it is taken, a read to block 1, two reads to block 1 in flight together (pipelined), a switch to
// block 0 after both answers, a read held through a stall and a write held through a stall. Every property's
// antecedent is hit, so a clean run proves the checker quiet. With HFT_VIOLATE=<k> exactly one property is
// violated (numbers in test_sva_checkers.py); its assertion must fire. Values are set after a falling edge and
// sampled at the next rising edge.
`timescale 1ns / 1ps
module tb_route_props;
  localparam logic [15:0] OUT_ADDR = 16'h0100;  // not in a window: block 0
  localparam logic [15:0] OUT_ADDR2 = 16'h0104;  // not in a window: block 0
  localparam logic [15:0] IN_ADDR = 16'h1004;  // in the window 0x1000-0x1FFF: block 1

  logic clk = 1'b0;
  logic rst = 1'b1;
  logic s_bvalid = 1'b0, s_bready = 1'b0, s_rvalid = 1'b0, s_rready = 1'b0;
  logic [1:0] m_req = '0, m_stall_wr = '0, m_stall_rd = '0, m_rd_ack = '0, m_wr_ack = '0;
  logic        m_req_is_wr = 1'b0;
  logic [15:0] m_addr = '0;
  logic [31:0] m_wr_data = '0, m_wr_biten = '0;
  logic m0_win_req = 1'b0;

  always begin
    #2 clk = ~clk;
  end

  route_props #(
      .ADDR_W  (16),
      .DATA_W  (32),
      .N_WIN   (2),
      .WIN_BASE({16'h2000, 16'h1000}),
      .WIN_MASK({16'hF000, 16'hF000})
  ) u_props (
      .clk        (clk),
      .rst        (rst),
      .s_bvalid   (s_bvalid),
      .s_bready   (s_bready),
      .s_rvalid   (s_rvalid),
      .s_rready   (s_rready),
      .m_req      (m_req),
      .m_req_is_wr(m_req_is_wr),
      .m_addr     (m_addr),
      .m_wr_data  (m_wr_data),
      .m_wr_biten (m_wr_biten),
      .m_stall_wr (m_stall_wr),
      .m_stall_rd (m_stall_rd),
      .m_rd_ack   (m_rd_ack),
      .m_wr_ack   (m_wr_ack),
      .m0_win_req (m0_win_req)
  );

  // 1 when this run violates property k.
  function automatic logic v(input int k);
`ifdef HFT_VIOLATE
    return k == `HFT_VIOLATE;
`else
    return k < 0;
`endif  // HFT_VIOLATE
  endfunction

  // Everything low except the request's parameters.
  task automatic quiet();
    {s_bvalid, s_bready, s_rvalid, s_rready}            = '0;
    {m_req, m_stall_wr, m_stall_rd, m_rd_ack, m_wr_ack} = '0;
    m0_win_req                                          = 1'b0;
  endtask

  task automatic idle(input int unsigned n = 1);
    repeat (n) begin
      @(negedge clk);
      quiet();
    end
  endtask

  // A read request block i takes in one cycle.
  task automatic read_req(input int i, input logic [15:0] addr);
    @(negedge clk);
    quiet();
    m_req[i]    = 1'b1;
    m_req_is_wr = 1'b0;
    m_addr      = addr;
  endtask

  // Block i's read ack.
  task automatic read_ack(input int i);
    @(negedge clk);
    quiet();
    m_rd_ack[i] = 1'b1;
  endtask

  // An upstream read response taken.
  task automatic read_resp();
    @(negedge clk);
    quiet();
    s_rvalid = 1'b1;
    s_rready = 1'b1;
  endtask

  initial begin
    repeat (2) @(negedge clk);
    rst = 1'b0;
    idle(2);

    // A write to block 0, acked in the cycle it is taken, and its upstream answer.
    @(negedge clk);
    m_req[0]    = 1'b1;
    m_req_is_wr = 1'b1;
    m_addr      = v(1) ? IN_ADDR : OUT_ADDR;  // 1: a_route_wr
    m_wr_data   = 32'h1234_5678;
    m_wr_biten  = '1;
    m_wr_ack[0] = 1'b1;
    @(negedge clk);
    quiet();
    s_bvalid = 1'b1;
    s_bready = 1'b1;
    idle();

    // A read to block 1, its ack and its upstream answer.
    read_req(1, v(2) ? OUT_ADDR : IN_ADDR);  // 2: a_route_rd
    read_ack(1);
    read_resp();
    idle();

    // Two reads to block 1 in flight together, then their acks and answers.
    read_req(1, IN_ADDR);
    if (v(
            4
        )) begin  // 4: a_switch_drained: a read taken by block 0 while the block 1 read waits for its answer
      read_req(0, OUT_ADDR);
    end else begin
      read_req(1, IN_ADDR);
    end
    read_ack(1);
    read_ack(1);
    read_resp();
    read_resp();
    idle();

    // The switch to block 0 after both answers: a read stalled for one cycle, then taken.
    read_req(0, OUT_ADDR);
    m_stall_rd[0] = 1'b1;
    @(negedge clk);
    m_stall_rd[0] = 1'b0;
    m_addr        = v(7) ? OUT_ADDR2 : OUT_ADDR;  // 7: a_stall_hold: the stalled request changed
    read_ack(0);
    read_resp();
    idle();

    // A write to block 0 stalled for one cycle, then taken and acked.
    @(negedge clk);
    quiet();
    m_req[0]      = 1'b1;
    m_req_is_wr   = 1'b1;
    m_addr        = OUT_ADDR2;
    m_stall_wr[0] = 1'b1;
    @(negedge clk);
    m_stall_wr[0] = 1'b0;
    m_wr_ack[0]   = 1'b1;
    @(negedge clk);
    quiet();
    s_bvalid = 1'b1;
    s_bready = 1'b1;
    idle();

    // Requests to both blocks at once.
    if (v(3)) begin  // 3: a_one_slave
      @(negedge clk);
      m_req       = 2'b11;
      m_req_is_wr = 1'b0;
      idle();
    end

    if (v(5)) begin  // 5: a_resp_owned: an ack with nothing in flight
      @(negedge clk);
      m_rd_ack[1] = 1'b1;
      idle();
    end

    @(negedge clk);
    m0_win_req = v(6);  // 6: a_m0_win_quiet
    idle(3);
    $display("HFT_TB_RESULT: PASS");
    $finish;
  end
endmodule
