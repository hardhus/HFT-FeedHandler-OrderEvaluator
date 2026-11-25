// reset_sync: asynchronous-assert / synchronous-deassert reset synchronizer.
// Clock domain : clk
// Latency      : assert = immediate (async); deassert = STAGES + 1 rising edges
// Pulse        : any rst_in pulse, even one between two clk edges, gives rst_out high until
//                STAGES + 1 rising edges after its release (in hardware: at least the flop's
//                minimum asynchronous reset pulse width)
// Copies       : rst_out has COPIES bits, each from its own output flop behind the STAGES ASYNC_REG flops
//                (decision 14, audit/15): one per consumer block, so no single flop drives every reset pin of a
//                domain. The copies carry no ASYNC_REG (UG949: a synchronizer is never replicated; the output
//                stage may be, here and by the placer) and are kept apart in synthesis (KEEP). They take the
//                same settled value at the same edge and keep the asynchronous preset, so assert stays
//                immediate. No consumer takes a bit from inside the chain.
// Note         : the only module allowed to use an async reset (see CODING_STYLE.md)
module reset_sync #(
    parameter int unsigned STAGES = 3,
    parameter int unsigned COPIES = 1
) (
    input  logic              clk,
    input  logic              rst_in,  // async, active-high
    output logic [COPIES-1:0] rst_out  // active-high, deasserts synchronously to clk
);
  if (STAGES < 2 || COPIES < 1) begin : g_check
    $error("reset_sync: STAGES must be >= 2 and COPIES >= 1");
  end

  (* ASYNC_REG = "TRUE" *)logic [STAGES-1:0] sync_q;
  (* keep = "true" *)logic [COPIES-1:0] out_q;

  always_ff @(posedge clk or posedge rst_in) begin
    if (rst_in) begin
      sync_q <= '1;
      out_q  <= '1;
    end else begin
      sync_q <= {sync_q[STAGES-2:0], 1'b0};
      out_q  <= {COPIES{sync_q[STAGES-1]}};
    end
  end

  assign rst_out = out_q;
endmodule
