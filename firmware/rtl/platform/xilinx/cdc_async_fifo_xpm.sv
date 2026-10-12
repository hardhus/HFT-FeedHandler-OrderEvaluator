// cdc_async_fifo_xpm: XPM_FIFO_ASYNC behind the cdc_async_fifo port contract (FWFT, LUTRAM).
// Clock domain : wr_clk / rd_clk (see cdc_async_fifo)
// Note         : Xilinx/Vivado only; listed in filelists/xilinx.f, never in common.f.
//                Selected with cdc_async_fifo #(.IMPL("xpm")) when HFT_HAVE_XPM is defined.
//                XPM requires FIFO_WRITE_DEPTH >= 16 (DEPTH_LOG2 >= 4).
module cdc_async_fifo_xpm #(
    parameter int unsigned WIDTH       = 64,
    parameter int unsigned DEPTH_LOG2  = 4,
    parameter int unsigned SYNC_STAGES = 2
) (
    input  logic             rst,
    input  logic             wr_clk,
    input  logic             wr_en,
    input  logic [WIDTH-1:0] din,
    output logic             full,
    output logic             overflow,
    output logic             wr_rst_busy,

    input  logic             rd_clk,
    input  logic             rd_en,
    output logic [WIDTH-1:0] dout,
    output logic             empty,
    output logic             underflow,
    output logic             rd_rst_busy
);
  if (DEPTH_LOG2 < 4) begin : g_check
    $error("cdc_async_fifo_xpm: XPM_FIFO_ASYNC needs DEPTH_LOG2 >= 4");
  end

  xpm_fifo_async #(
      .FIFO_MEMORY_TYPE ("distributed"),
      .FIFO_WRITE_DEPTH (2 ** DEPTH_LOG2),
      .WRITE_DATA_WIDTH (WIDTH),
      .READ_DATA_WIDTH  (WIDTH),
      .READ_MODE        ("fwft"),
      .FIFO_READ_LATENCY(0),
      .CDC_SYNC_STAGES  (SYNC_STAGES),
      .FULL_RESET_VALUE (1),
      .USE_ADV_FEATURES ("0101"),           // bit 0 overflow, bit 8 underflow
      .SIM_ASSERT_CHK   (1)
  ) u_fifo (
      .sleep        (1'b0),
      .rst          (rst),
      .wr_clk       (wr_clk),
      .wr_en        (wr_en),
      .din          (din),
      .full         (full),
      .prog_full    (),
      .wr_data_count(),
      .overflow     (overflow),
      .wr_rst_busy  (wr_rst_busy),
      .almost_full  (),
      .wr_ack       (),
      .rd_clk       (rd_clk),
      .rd_en        (rd_en),
      .dout         (dout),
      .empty        (empty),
      .prog_empty   (),
      .rd_data_count(),
      .underflow    (underflow),
      .rd_rst_busy  (rd_rst_busy),
      .almost_empty (),
      .data_valid   (),
      .injectsbiterr(1'b0),
      .injectdbiterr(1'b0),
      .sbiterr      (),
      .dbiterr      ()
  );
endmodule
