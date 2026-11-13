// route_props: axil_route2 routing rules (decision 41); bind to the axil_route2 instance.
// Simulation only. Disabled until the first reset has been seen, because flops start with random
// values (--x-initial unique).
module route_props #(
    parameter int unsigned                    ADDR_W   = 16,
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
    input logic [       1:0] m_awvalid,
    input logic [       1:0] m_awready,
    input logic [ADDR_W-1:0] m_awaddr,
    input logic [       1:0] m_wvalid,
    input logic [       1:0] m_arvalid,
    input logic [       1:0] m_arready,
    input logic [ADDR_W-1:0] m_araddr,
    input logic [       1:0] m_bvalid,
    input logic [       1:0] m_rvalid,
    // a request m0 made on a port of an address in the windows (hft_csr's external RISK, STRATEGY and TX
    // blocks); 0 where m0 has none
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
  // out: accesses a slave has taken (AW or AR handshake) and not yet answered upstream; owner: their slave.
  logic armed, owner, hs_sel;
  int unsigned out;
  logic aw_hs, ar_hs, resp_hs;
  initial begin
    armed = 1'b0;
    out   = 0;
  end
  assign aw_hs   = |(m_awvalid & m_awready);
  assign ar_hs   = |(m_arvalid & m_arready);
  assign hs_sel  = aw_hs ? m_awvalid[1] : m_arvalid[1];
  assign resp_hs = (s_bvalid && s_bready) || (s_rvalid && s_rready);
  always @(posedge clk) begin
    if (rst) begin
      armed <= 1'b1;
      out   <= 0;
    end else begin
      out <= out + int'(aw_hs) + int'(ar_hs) - int'(resp_hs);
      if (aw_hs || ar_hs) begin
        owner <= hs_sel;
      end
    end
  end

  a_route_aw :
  assert property (@(posedge clk) disable iff (rst || !armed) (m_awvalid[1] |-> in_win(
      m_awaddr
  )) and(m_awvalid[0] |-> !in_win(
      m_awaddr
  )))
  else begin
    $error("route_props: a write went to the slave its address is not routed to");
  end

  a_route_ar :
  assert property (@(posedge clk) disable iff (rst || !armed) (m_arvalid[1] |-> in_win(
      m_araddr
  )) and(m_arvalid[0] |-> !in_win(
      m_araddr
  )))
  else begin
    $error("route_props: a read went to the slave its address is not routed to");
  end

  a_one_slave :
  assert property (@(posedge clk) disable iff (rst || !armed)
      !((m_awvalid[0] || m_wvalid[0] || m_arvalid[0]) && (m_awvalid[1] || m_wvalid[1] || m_arvalid[1])))
  else begin
    $error("route_props: requests to both slaves in one cycle");
  end

  a_switch_drained :
  assert property (@(posedge clk) disable iff (rst || !armed) (aw_hs || ar_hs) && out != 0 |-> hs_sel == owner)
  else begin
    $error("route_props: a request went to the other slave while accesses were in flight");
  end

  a_resp_owned :
  assert property (@(posedge clk) disable iff (rst || !armed)
      (m_bvalid | m_rvalid) != 2'b00 |-> out != 0 && !m_bvalid[!owner] && !m_rvalid[!owner])
  else begin
    $error("route_props: a response from a slave that has no access in flight");
  end

  a_m0_win_quiet :
  assert property (@(posedge clk) disable iff (rst || !armed) !m0_win_req)
  else begin
    $error("route_props: m0 got a request for an address in the windows");
  end
endmodule
