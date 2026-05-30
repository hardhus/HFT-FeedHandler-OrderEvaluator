`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 27.05.2026 11:52:57
// Design Name: 
// Module Name: hft_pkg
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


package hft_pkg;

  typedef struct packed {
    logic [31:0] price;
    logic [31:0] volume;
    logic [63:0] timestamp;
    logic [7:0]  side;
  } market_data_event_t;

endpackage
