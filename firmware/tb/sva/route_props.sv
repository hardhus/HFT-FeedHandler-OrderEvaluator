// route_props: axil_cpuif2 routing rules (decision 41, 43) and the regblock internal CPUIF protocol on its block
// side (A40); bind to the axil_cpuif2 instance's ports. Simulation only. Disabled until the first reset has been
// seen, because flops start with random values (--x-initial unique).
module route_props #(
    parameter int unsigned                    ADDR_W   = 16,
    parameter int unsigned                    DATA_W   = 32,
    parameter int unsigned                    N_WIN    = 1,
    parameter logic        [N_WIN*ADDR_W-1:0] WIN_BASE = '0,
    parameter logic        [N_WIN*ADDR_W-1:0] WIN_MASK = '0
) (
    input logic              clk,
    input logic              rst,
    input logic              s_bvalid,
    input logic              s_bready,
    input logic              s_rvalid,
    input logic              s_rready,
    input logic [       1:0] m_req,
    input logic              m_req_is_wr,
    input logic [ADDR_W-1:0] m_addr,
    input logic [DATA_W-1:0] m_wr_data,
    input logic [DATA_W-1:0] m_wr_biten,
    input logic [       1:0] m_stall_wr,
    input logic [       1:0] m_stall_rd,
    input logic [       1:0] m_rd_ack,
    input logic [       1:0] m_wr_ack,
    // a request block 0 made on a port of an address in the windows (hft_csr's external RISK, STRATEGY and TX
    // blocks); 0 where block 0 has none
    input logic              m0_win_req
);
  function automatic logic in_win(input logic [ADDR_W-1:0] a);
    logic hit = 1'b0;
    for (int i = 0; i < N_WIN; i++) begin
      hit |= (a & WIN_MASK[i*ADDR_W+:ADDR_W]) == WIN_BASE[i*ADDR_W+:ADDR_W];
    end
    return hit;
  endfunction

  // Sim-only: initial + always (not always_ff) on purpose.
  // out: accesses a block has taken and not yet answered upstream; owner: their block.
  logic armed, owner;
  int unsigned       out;
  logic        [1:0] stall;
  logic taken, resp_hs, own_now;
  initial begin
    armed = 1'b0;
    out   = 0;
  end
  assign stall   = m_req_is_wr ? m_stall_wr : m_stall_rd;
  assign taken   = |(m_req & ~stall);
  assign resp_hs = (s_bvalid && s_bready) || (s_rvalid && s_rready);
  // a write the block takes can be acked in the same cycle
  assign own_now = taken ? m_req[1] : owner;
  always @(posedge clk) begin
    if (rst) begin
      armed <= 1'b1;
      out   <= 0;
    end else begin
      out <= out + int'(taken) - int'(resp_hs);
      if (taken) begin
        owner <= m_req[1];
      end
    end
  end

  a_route_wr :
  assert property (@(posedge clk) disable iff (rst || !armed) m_req_is_wr |-> (m_req == 2'b10 |-> in_win(
      m_addr
  )) and(m_req == 2'b01 |-> !in_win(
      m_addr
  )))
  else begin
    $error("route_props: a write went to the block its address is not routed to");
  end

  a_route_rd :
  assert property (@(posedge clk) disable iff (rst || !armed) !m_req_is_wr |-> (m_req == 2'b10 |-> in_win(
      m_addr
  )) and(m_req == 2'b01 |-> !in_win(
      m_addr
  )))
  else begin
    $error("route_props: a read went to the block its address is not routed to");
  end

  a_one_slave :
  assert property (@(posedge clk) disable iff (rst || !armed) m_req != 2'b11)
  else begin
    $error("route_props: requests to both blocks in one cycle");
  end

  a_switch_drained :
  assert property (@(posedge clk) disable iff (rst || !armed) taken && out != 0 |-> m_req[1] == owner)
  else begin
    $error("route_props: a request went to the other block while accesses were in flight");
  end

  a_resp_owned :
  assert property (@(posedge clk) disable iff (rst || !armed)
      (m_rd_ack | m_wr_ack) != 2'b00 |-> (out != 0 || taken) && !m_rd_ack[!own_now] && !m_wr_ack[!own_now])
  else begin
    $error("route_props: a response from a block that has no access in flight");
  end

  a_m0_win_quiet :
  assert property (@(posedge clk) disable iff (rst || !armed) !m0_win_req)
  else begin
    $error("route_props: block 0 got a request for an address in the windows");
  end

  // The CPUIF protocol: "If stalled, the request shall be held until accepted. A request's parameters (type,
  // address, etc) shall remain static throughout the stall."
  a_stall_hold :
  assert property (@(posedge clk) disable iff (rst || !armed) (m_req & stall) != 2'b00 |=> m_req == $past(
      m_req
  ) && $stable(
      {m_req_is_wr, m_addr, m_wr_data, m_wr_biten}
  ))
  else begin
    $error("route_props: a stalled request dropped or changed before it was taken");
  end
endmodule
