`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 27.05.2026 12:37:00
// Design Name: 
// Module Name: HFT_Top
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

module HFT_Top_Core (
    // CLOCK AND RESET
    input logic clk_net,   // Clock for Network domain (156.25 MHz)
    input logic clk_pcie,  // Clock for PCIe domain (125 MHz)
    input logic rst_n,     // Active-low asynchronous reset

    // TOP LEVEL INTERFACES
    axis_if.slave s_axis,  // Direct stream from Ethernet MAC IP
    axil_if.slave s_axi,   // Direct Memory Map from PCIe Bridge IP

    // HARDWARE PIN OUT
    output logic trade_execute_out
);

  // ==========================================
  // INTERFACE INSTANTIATION
  // ==========================================
  // This single interface instance creates the safe bridge between domains
  fifo_if i_cdc_fifo_if ();

  // ==========================================
  // MODULE INSTANTIATIONS
  // ==========================================

  // 1. Network Domain Core: Parses UDP data and writes to FIFO
  udp_parser i_udp_parser (
      .clk       (clk_net),
      .reset_n   (rst_n),
      .s_axis    (s_axis),
      .fifo_write(i_cdc_fifo_if.write_port)  // Hooked up to FIFO write port
  );

  // 2. Hardware CDC Component (Xilinx FIFO Generator IP or Custom Async FIFO)
  // It takes write signals from clk_net and cross them over to clk_pcie
  async_fifo_wrapper i_async_fifo (
      .w_clk(clk_net),
      .r_clk(clk_pcie),
      .rst_n(rst_n),
      .f_if (i_cdc_fifo_if)
  );

  // 3. PCIe Domain Core: Reads from FIFO and handles C++ AXI-Lite requests
  register_bank i_register_bank (
      .clk              (clk_pcie),
      .reset_n          (rst_n),
      .fifo_read        (i_cdc_fifo_if.read_port),  // Hooked up to FIFO read port
      .s_axi            (s_axi),
      .out_trade_execute(trade_execute_out)
  );

endmodule
