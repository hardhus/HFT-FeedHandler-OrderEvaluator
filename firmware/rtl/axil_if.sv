`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 30.05.2026 22:52:27
// Design Name: 
// Module Name: axil_if
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


interface axil_if #(
    parameter int ADDR_WIDTH = 32,
    parameter int REG_WIDTH  = 32
);
  // Write Address Channel
  logic [ADDR_WIDTH-1:0] awaddr;  // Write Address
  logic                  awvalid;  // Write Address Valid
  logic                  awready;  // FPGA Write Address Ready
  // Write Data Channel
  logic [ REG_WIDTH-1:0] wdata;  // Write Data
  logic                  wvalid;  // Write Data Valid
  logic                  wready;  // FPGA Write Data Ready
  // Read Address Channel
  logic [ADDR_WIDTH-1:0] araddr;  // Read Address
  logic                  arvalid;  // Read Address Valid
  logic                  arready;  // FPGA Read Address Ready
  // Read Data Channel
  logic [ REG_WIDTH-1:0] rdata;  // Read Data driven by FPGA
  logic                  rvalid;  // FPGA Read Data Valid
  logic                  rready;  // Host Read Data Ready

  // Slave mode (Our Register Bank)
  modport slave(
      input awaddr, awvalid, wdata, wvalid, araddr, arvalid, rready,
      output awready, wready, arready, rdata, rvalid
  );
endinterface
