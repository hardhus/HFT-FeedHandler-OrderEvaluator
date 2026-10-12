// cdc_handshake: 4-phase req/ack transfer of a WIDTH-bit word between unrelated clocks.
// Clock domain : src_clk (src_*), dst_clk (dst_*)
// Latency      : STAGES+1 dst cycles from acceptance to dst_valid (+1 for synchronizer jitter)
// Throughput   : one word per full req/ack round trip (src_ready low until it completes)
// Note         : src_rst and dst_rst must be asserted together (one reset source, one
//                reset_sync per domain). data_q -> dst_data_q is a CDC path without a
//                synchronizer: data_q is stable while req is high (constraints: audit 08 §2.9).
module cdc_handshake #(
    parameter int unsigned WIDTH  = 32,
    parameter int unsigned STAGES = 2
) (
    input  logic             src_clk,
    input  logic             src_rst,
    input  logic [WIDTH-1:0] src_data,
    input  logic             src_valid,
    output logic             src_ready,

    input  logic             dst_clk,
    input  logic             dst_rst,
    output logic [WIDTH-1:0] dst_data,
    output logic             dst_valid
);
  logic req_q, ack_s, req_s, ack_q, dst_valid_q;
  (* hft_cdc_data = "TRUE" *)logic [WIDTH-1:0] data_q;
  logic [WIDTH-1:0] dst_data_q;

  // ---- source side ----
  assign src_ready = !req_q && !ack_s;

  always_ff @(posedge src_clk) begin
    if (src_rst) begin
      req_q <= 1'b0;
    end else if (src_valid && src_ready) begin
      req_q <= 1'b1;
    end else if (ack_s) begin
      req_q <= 1'b0;
    end
  end

  always_ff @(posedge src_clk) begin
    if (src_valid && src_ready) begin
      data_q <= src_data;  // held stable while req_q is high
    end
  end

  sync_bit #(
      .WIDTH (1),
      .STAGES(STAGES)
  ) u_ack_sync (
      .clk(src_clk),
      .d  (ack_q),
      .q  (ack_s)
  );

  // ---- destination side ----
  sync_bit #(
      .WIDTH (1),
      .STAGES(STAGES)
  ) u_req_sync (
      .clk(dst_clk),
      .d  (req_q),
      .q  (req_s)
  );

  always_ff @(posedge dst_clk) begin
    if (dst_rst) begin
      ack_q       <= 1'b0;
      dst_valid_q <= 1'b0;
    end else begin
      ack_q       <= req_s;
      dst_valid_q <= req_s && !ack_q;  // one pulse per transfer (rising edge of req_s)
    end
  end

  always_ff @(posedge dst_clk) begin
    if (req_s && !ack_q) begin
      dst_data_q <= data_q;
    end
  end

  assign dst_valid = dst_valid_q;
  assign dst_data  = dst_data_q;
endmodule
