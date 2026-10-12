// reset_sync: asynchronous-assert / synchronous-deassert reset synchronizer.
// Clock domain : clk
// Latency      : assert = immediate (async); deassert = STAGES rising edges
// Pulse        : any rst_in pulse, even one between two clk edges, gives rst_out high until
//                STAGES rising edges after its release (in hardware: at least the flop's
//                minimum asynchronous reset pulse width)
// Note         : the only module allowed to use an async reset (see CODING_STYLE.md)
module reset_sync #(
    parameter int unsigned STAGES = 3
) (
    input  logic clk,
    input  logic rst_in,  // async, active-high
    output logic rst_out  // active-high, deasserts synchronously to clk
);
  if (STAGES < 2) begin : g_check
    $error("reset_sync: STAGES must be >= 2");
  end

  (* ASYNC_REG = "TRUE" *) logic [STAGES-1:0] sync_q;

  always_ff @(posedge clk or posedge rst_in) begin
    if (rst_in) begin
      sync_q <= '1;
    end else begin
      sync_q <= {sync_q[STAGES-2:0], 1'b0};
    end
  end

  assign rst_out = sync_q[STAGES-1];
endmodule
