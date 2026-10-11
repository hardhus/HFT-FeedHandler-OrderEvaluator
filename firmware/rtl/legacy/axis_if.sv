`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 30.05.2026 22:51:36
// Design Name: 
// Module Name: axis_if
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


interface axis_if #(
    parameter int DATA_WIDTH = 64
);
  logic [DATA_WIDTH-1:0] tdata;  // Streaming raw data bus (64-bit for 10G)
  logic [(DATA_WIDTH/8)-1:0] tstrb;  // Byte strobe to validate specific bytes in the chunk
  logic tvalid;  // Driven by MAC IP. High means data on tdata is valid
  logic tready;  // Driven by Our Parser. High means parser can accept data
  logic tlast;  // Driven by MAC IP. High indicates the last clock cycle of the packet

  // Master side (e.g., Ethernet MAC output)
  modport master(output tdata, tstrb, tvalid, tlast, input tready);

  // Slave side (e.g., Our UDP Parser input)
  modport slave(input tdata, tstrb, tvalid, tlast, output tready);
endinterface
