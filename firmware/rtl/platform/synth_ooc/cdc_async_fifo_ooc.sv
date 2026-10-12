// cdc_async_fifo_ooc: OOC synthesis harness for cdc_async_fifo. Registers dout in the read
// domain, as a real consumer would, so the memory -> read-domain CDC path exists in the netlist
// and cdc.tcl / report_cdc can be checked on it. Not used in simulation.
// Clock domain : wr_clk / rd_clk
module cdc_async_fifo_ooc #(
    parameter int unsigned WIDTH      = 64,
    parameter int unsigned DEPTH_LOG2 = 4,
    parameter string       IMPL       = "portable"
) (
    input  logic             rst,
    input  logic             wr_clk,
    input  logic             wr_en,
    input  logic [WIDTH-1:0] din,
    output logic             full,
    input  logic             rd_clk,
    input  logic             rd_en,
    output logic [WIDTH-1:0] dout_q,
    output logic             empty
);
  logic [WIDTH-1:0] dout;
  logic overflow, underflow, wr_rst_busy, rd_rst_busy;

  cdc_async_fifo #(
      .WIDTH     (WIDTH),
      .DEPTH_LOG2(DEPTH_LOG2),
      .IMPL      (IMPL)
  ) u_fifo (
      .rst        (rst),
      .wr_clk     (wr_clk),
      .wr_en      (wr_en),
      .din        (din),
      .full       (full),
      .overflow   (overflow),
      .wr_rst_busy(wr_rst_busy),
      .rd_clk     (rd_clk),
      .rd_en      (rd_en),
      .dout       (dout),
      .empty      (empty),
      .underflow  (underflow),
      .rd_rst_busy(rd_rst_busy)
  );

  always_ff @(posedge rd_clk) begin
    if (rd_en && !empty) begin
      dout_q <= dout;
    end
  end
endmodule
