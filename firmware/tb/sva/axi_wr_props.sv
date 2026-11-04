// axi_wr_props: AXI4 write master checks for INCR bursts of full-width beats; bind to the master. Simulation
// only. Disabled until the first reset has been seen, because flops start with random values
// (--x-initial unique). W beats may come before their AW, so each burst's beat count is compared with its
// AWLEN once both are known.
module axi_wr_props #(
    parameter int unsigned DATA_W = 512
) (
    input logic                clk,
    input logic                rst,
    input logic [        63:0] awaddr,
    input logic [         7:0] awlen,
    input logic [         2:0] awsize,
    input logic [         1:0] awburst,
    input logic                awvalid,
    input logic                awready,
    input logic [  DATA_W-1:0] wdata,
    input logic [DATA_W/8-1:0] wstrb,
    input logic                wlast,
    input logic                wvalid,
    input logic                wready,
    input logic                bvalid,
    input logic                bready
);
  // Sim-only: initial + always (not always_ff) on purpose.
  logic armed;
  int unsigned aw_lens[$], w_lens[$];
  int unsigned w_beats, aw_count, b_count;
  logic        beats_wrong;
  logic [12:0] burst_end;

  initial begin
    armed       = 1'b0;
    w_beats     = 0;
    aw_count    = 0;
    b_count     = 0;
    beats_wrong = 1'b0;
  end

  assign burst_end = 13'(awaddr[11:0]) + 13'((int'(awlen) + 1) << awsize);

  always @(posedge clk) begin
    if (rst) begin
      armed <= 1'b1;
      aw_lens.delete();
      w_lens.delete();
      w_beats     <= 0;
      aw_count    <= 0;
      b_count     <= 0;
      beats_wrong <= 1'b0;
    end else begin
      beats_wrong <= 1'b0;
      if (awvalid && awready) begin
        aw_lens.push_back(int'(awlen) + 1);
        aw_count <= aw_count + 1;
      end
      if (wvalid && wready) begin
        if (wlast) begin
          w_lens.push_back(w_beats + 1);
          w_beats <= 0;
        end else begin
          w_beats <= w_beats + 1;
        end
      end
      if (bvalid && bready) begin
        b_count <= b_count + 1;
      end
      if (aw_lens.size() > 0 && w_lens.size() > 0) begin
        beats_wrong <= aw_lens[0] != w_lens[0];
        void'(aw_lens.pop_front());
        void'(w_lens.pop_front());
      end
    end
  end

  a_aw_stable :
  assert property (@(posedge clk) disable iff (rst || !armed)
      awvalid && !awready |=> awvalid && $stable(
      awaddr
  ) && $stable(
      awlen
  ) && $stable(
      awsize
  ) && $stable(
      awburst
  ))
  else begin
    $error("axi_wr_props: AW changed or dropped before AWREADY");
  end

  a_w_stable :
  assert property (@(posedge clk) disable iff (rst || !armed)
      wvalid && !wready |=> wvalid && $stable(
      wdata
  ) && $stable(
      wstrb
  ) && $stable(
      wlast
  ))
  else begin
    $error("axi_wr_props: W changed or dropped before WREADY");
  end

  a_4k :
  assert property (@(posedge clk) disable iff (rst || !armed) awvalid |-> burst_end <= 13'd4096)
  else begin
    $error("axi_wr_props: a burst crosses 4 KiB");
  end

  a_incr :
  assert property (@(posedge clk) disable iff (rst || !armed)
      awvalid |-> awburst == 2'b01 && (8 << awsize) == int'(DATA_W))
  else begin
    $error("axi_wr_props: a burst that is not INCR of full-width beats");
  end

  a_wstrb :
  assert property (@(posedge clk) disable iff (rst || !armed) wvalid |-> &wstrb)
  else begin
    $error("axi_wr_props: a W beat without all its strobes");
  end

  a_w_beats :
  assert property (@(posedge clk) disable iff (rst || !armed) !beats_wrong)
  else begin
    $error("axi_wr_props: W beats differ from AWLEN+1");
  end

  a_b_after_aw :
  assert property (@(posedge clk) disable iff (rst || !armed) bvalid && bready |-> b_count < aw_count)
  else begin
    $error("axi_wr_props: more B responses than AW");
  end

  a_reset_valid :
  assert property (@(posedge clk) disable iff (!armed) rst |=> !awvalid && !wvalid)
  else begin
    $error("axi_wr_props: VALID high after reset");
  end
endmodule
