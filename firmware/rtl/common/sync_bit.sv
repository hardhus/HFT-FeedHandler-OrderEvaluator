// sync_bit: multi-flop synchronizer for level signals; bits are synchronized independently.
// Clock domain : clk (destination); d is asynchronous to clk
// Latency      : STAGES rising edges (STAGES or STAGES+1 with HFT_SIM_CDC_JITTER)
// Note         : only for single bits or gray-coded buses, never for binary multi-bit values.
//                No reset: the chain settles to d within STAGES cycles.
// Pulse        : hold each level for more than 2 clk periods (longest period, jitter included);
//                a shorter pulse may be missed. Use cdc_handshake for events.
module sync_bit #(
    parameter int unsigned WIDTH  = 1,
    parameter int unsigned STAGES = 2
) (
    input  logic             clk,
    input  logic [WIDTH-1:0] d,
    output logic [WIDTH-1:0] q
);
  if (STAGES < 2) begin : g_check
    $error("sync_bit: STAGES must be >= 2");
  end

  logic [WIDTH-1:0] d_in;

`ifdef HFT_SIM_CDC_JITTER
  // Simulation-only metastability model (audit 08 S1). In hardware only bits that change
  // close to the capturing edge are uncertain. Model: the bits that changed at the most
  // recent change instant since the previous clk edge may each be captured one cycle late
  // (old value). Bits that changed earlier are captured correctly. A gray-coded bus then
  // yields either the old or the new code; a binary bus yields invalid mixtures.
  // A change coinciding with a clk edge may not have its timestamp updated yet when the
  // edge samples d; such bits (changed, but timestamp older than the previous edge) are
  // by definition the most recent ones.
  realtime t_chg[WIDTH];
  realtime t_max, t_edge_q;
  logic [WIDTH-1:0] d_q, late_sel, changed, pending, latest, cand;

  for (genvar i = 0; i < WIDTH; i++) begin : g_track
    realtime t;  // reals start at 0.0
    always @(posedge d[i] or negedge d[i]) begin
      t <= $realtime;
    end
    assign t_chg[i] = t;
  end

  always_comb begin
    changed = d ^ d_q;  // bits that differ from what the previous edge saw
    t_max   = 0.0;
    for (int i = 0; i < WIDTH; i++) begin
      pending[i] = changed[i] && t_chg[i] < t_edge_q;
      if (changed[i] && t_chg[i] > t_max) begin
        t_max = t_chg[i];
      end
    end
    for (int i = 0; i < WIDTH; i++) begin
      latest[i] = changed[i] && t_chg[i] == t_max;
    end
    cand = (|pending) ? pending : latest;
  end

  always_ff @(posedge clk) begin
    d_q      <= d;
    t_edge_q <= $realtime;
    late_sel <= WIDTH'($urandom);
  end

  assign d_in = (late_sel & cand & ~d) | (~(late_sel & cand) & d);
`else
  assign d_in = d;
`endif  // HFT_SIM_CDC_JITTER

  (* ASYNC_REG = "TRUE" *) logic [WIDTH-1:0] sync_q[STAGES];

  always_ff @(posedge clk) begin
    sync_q[0] <= d_in;
    for (int unsigned s = 1; s < STAGES; s++) begin
      sync_q[s] <= sync_q[s-1];
    end
  end

  assign q = sync_q[STAGES-1];
endmodule
