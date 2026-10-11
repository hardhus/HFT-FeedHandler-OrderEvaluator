`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 30.05.2026 23:16:00
// Design Name: 
// Module Name: fifo_if
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

interface fifo_if;
  // WRITE PORT (Network Domain - clk_net)
  logic                w_en;  // Write enable driven by UDP Parser
  hft_internal_event_t w_data;  // Packed structural data containing Price and Volume
  logic                full;  // Driven by FIFO. High means FIFO is full, cannot write

  // READ PORT (PCIe Domain - clk_pcie)
  logic                r_en;
  hft_internal_event_t r_data;
  logic                empty;

  // Write port modport for the UDP Parser side
  modport write_port(output w_en, w_data, input full);

  // Read port modport for the Register Bank side
  modport read_port(output r_en, input r_data, empty);
endinterface
