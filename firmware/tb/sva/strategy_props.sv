// strategy_props: strategy_engine fire rules and order queue; bind to the strategy_engine instance.
// Simulation only. Disabled until the first reset has been seen, because flops start with random
// values (--x-initial unique).
//
// fire, armed_p, fired_p, err: the message stage (armed_p: after the cycle's packet event, without the rules
// that a disarm or commit of the same cycle blocks). fire_q, pkt_q, pkt_acc_q:
// the same one cycle later, where the queue is written. fill: queued orders before this cycle's write.
module strategy_props #(
    parameter int unsigned N     = 8,
    parameter int unsigned DEPTH = 16,
    parameter int unsigned PTR_W = 5
) (
    input logic             clk,
    input logic             rst,
    input logic [    N-1:0] fire,
    input logic [    N-1:0] armed_p,
    input logic [    N-1:0] fired_p,
    input logic [    N-1:0] err,
    input logic             path_open,
    input logic             fire_q,
    input logic             pkt_q,
    input logic             pkt_acc_q,
    input logic [PTR_W-1:0] fill,
    input logic             m_ord_valid
);
  // Sim-only: initial + always (not always_ff) on purpose.
  logic        armed;
  int unsigned open_fires;  // fires of the open packet (queue side)
  int unsigned ready;  // orders of accepted packets not out yet
  initial begin
    armed      = 1'b0;
    open_fires = 0;
    ready      = 0;
  end
  always @(posedge clk) begin
    if (rst) begin
      armed      <= 1'b1;
      open_fires <= 0;
      ready      <= 0;
    end else begin
      // A fire in the cycle of the packet event is the next packet's.
      open_fires <= (pkt_q ? 0 : open_fires) + int'(fire_q);
      // Stops at 0, so an extra order fires a_order_committed alone.
      ready <= ready + ((pkt_q && pkt_acc_q) ? open_fires : 0) - int'(m_ord_valid && ready > 0);
    end
  end

  a_fire_onehot :
  assert property (@(posedge clk) disable iff (rst || !armed) $onehot0(fire))
  else begin
    $error("strategy_props: more than one rule fired on a message");
  end

  a_fire_armed :
  assert property (@(posedge clk) disable iff (rst || !armed) (fire & ~(armed_p & ~err)) == '0)
  else begin
    $error("strategy_props: a rule fired while disarmed or with a static error");
  end

  a_fire_closed :
  assert property (@(posedge clk) disable iff (rst || !armed) fire != '0 |-> path_open)
  else begin
    $error("strategy_props: a rule fired while the order path was closed");
  end

  a_fire_once :
  assert property (@(posedge clk) disable iff (rst || !armed) (fire & fired_p) == '0)
  else begin
    $error("strategy_props: a rule fired twice in one packet");
  end

  a_order_committed :
  assert property (@(posedge clk) disable iff (rst || !armed) m_ord_valid |-> ready > 0)
  else begin
    $error("strategy_props: an order out that no accepted packet committed");
  end

  a_drain :
  assert property (@(posedge clk) disable iff (rst || !armed) ready > 0 |-> m_ord_valid)
  else begin
    $error("strategy_props: an accepted order waited");
  end

  a_room :
  assert property (@(posedge clk) disable iff (rst || !armed) fire_q |-> fill < PTR_W'(DEPTH))
  else begin
    $error("strategy_props: an order written to a full queue");
  end
endmodule
