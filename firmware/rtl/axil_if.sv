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
  logic [(REG_WIDTH/8)-1:0] wstrb;
  logic                  wvalid;  // Write Data Valid
  logic                  wready;  // FPGA Write Data Ready
  // Read Address Channel
  logic [ADDR_WIDTH-1:0] araddr;  // Read Address
  logic                  arvalid;  // Read Address Valid
  logic                  arready;  // FPGA Read Address Ready
  // Read Data Channel
  logic [ REG_WIDTH-1:0] rdata;  // Read Data driven by FPGA
  logic [1:0] rresp;
  logic                  rvalid;  // FPGA Read Data Valid
  logic                  rready;  // Host Read Data Ready
  // Write Response Channel (B Channel) - EKSİK OLAN KATMAN
  logic [1:0] bresp;
  logic                  bvalid;  // Driven by FPGA Register Bank
  logic                  bready;  // Driven by Host CPU (Testbench)

  // Slave mode (Our Register Bank)
  modport slave(
      input awaddr, awvalid, wdata, wstrb, wvalid, araddr, arvalid, rready, bready,
      output awready, wready, arready, rdata, rresp, rvalid, bresp, bvalid
  );
endinterface
