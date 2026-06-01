`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 01.06.2026 18:15:05
// Design Name: 
// Module Name: async_fifo_wrapper
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////


import hft_pkg::*;

module async_fifo_wrapper (
    input logic   w_clk,
    input logic   r_clk,
    input logic   rst_n,
          fifo_if f_if
);

  // Packed struct boyutu hesaplaması: msg_type(8) + price(32) + volume(32) = 72 bit.
  localparam int DATA_WIDTH = 72;

  // Vivado Native XPM Async FIFO Instantiation
  xpm_fifo_async #(
      .CASCADE_HEIGHT(0),
      .CDC_SYNC_STAGES(2),       // İki saat alanını senkronize etmek için 2 aşamalı flip-flop
      .DOUT_RESET_VALUE("0"),
      .ECC_MODE("no_ecc"),
      .FIFO_MEMORY_TYPE("auto"),
      .FIFO_READ_LATENCY(0),     // FWFT (First-Word-Fall-Through) için '0' olmalı
      .FIFO_WRITE_DEPTH(16),     // Eski koddaki MAX_DEPTH
      .PROG_EMPTY_THRESH(5),
      .PROG_FULL_THRESH(11),
      .RD_DATA_COUNT_WIDTH(5),
      .READ_DATA_WIDTH(DATA_WIDTH),
      .READ_MODE("fwft"),        // Kombinasyonel okuma modu (eskisiyle aynı davranış)
      .SIM_ASSERT_CHK(0),
      .USE_ADV_FEATURES("0000"),
      .WAKEUP_TIME(0),
      .WRITE_DATA_WIDTH(DATA_WIDTH),
      .WR_DATA_COUNT_WIDTH(5)
  ) i_xpm_fifo (
      .rst          (~rst_n),      // XPM FIFO aktif yüksek (Active-High) reset bekler
      .wr_clk       (w_clk),
      .wr_en        (f_if.w_en),
      .din          (f_if.w_data),
      .full         (f_if.full),
      
      .rd_clk       (r_clk),
      .rd_en        (f_if.r_en),
      .dout         (f_if.r_data),
      .empty        (f_if.empty),
      
      // Kullanılmayan gelişmiş sinyalleri topraklıyoruz (0'a çekiyoruz)
      .sleep        (1'b0),
      .injectdbiterr(1'b0),
      .injectsbiterr(1'b0)
  );

endmodule