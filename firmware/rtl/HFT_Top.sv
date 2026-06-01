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
      .clk             (clk_net),
      .rst_n           (rst_n),
      .s_axis          (s_axis),
      .out_parsed_event(i_cdc_fifo_if.w_data),
      .out_event_valid (i_cdc_fifo_if.w_en)
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
      .clk                (clk_pcie),
      .rst_n              (rst_n),
      .in_pcie_event      (i_cdc_fifo_if.r_data),  // FIFO read data out
      .in_pcie_event_valid(!i_cdc_fifo_if.empty),  // Valid when FIFO is NOT empty
      .s_axi              (s_axi),
      .out_trade_execute  (trade_execute_out)
  );

  assign i_cdc_fifo_if.r_en = !i_cdc_fifo_if.empty;

endmodule
