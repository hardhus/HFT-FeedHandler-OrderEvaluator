`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 27.05.2026 12:44:03
// Design Name: 
// Module Name: tb_HFT_Top
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

module tb_HFT_Top ();

  // --- Clock & Reset Bus Signaling ---
  logic clk_net;
  logic clk_pcie;
  logic rst_n;
  logic trade_execute_out;

  // --- Interface Instantiation (Top-Level Infrastructure) ---
  axis_if axis_bus ();  // Emulates 10G Ethernet MAC streaming output
  axil_if axil_bus ();  // Emulates PCIe Host CPU memory-mapped bridge

  // ==========================================================================
  // PART 1: Concurrent Clock Generation Processes
  // ==========================================================================
  initial clk_net = 0;
  always #3.2 clk_net = ~clk_net;  // 156.25 MHz Network Domain Clock Line

  initial clk_pcie = 0;
  always #4.0 clk_pcie = ~clk_pcie;  // 125.00 MHz PCIe/Host Domain Clock Line

  // ==========================================================================
  // PART 2: Design Under Test (DUT) Instantiation
  // ==========================================================================
  HFT_Top_Core dut (
      .clk_net          (clk_net),
      .clk_pcie         (clk_pcie),
      .rst_n            (rst_n),
      .s_axis           (axis_bus.slave),
      .s_axi            (axil_bus.slave),
      .trade_execute_out(trade_execute_out)
  );

  // ==========================================================================
  // PART 3: Low-Level Emulation Drivers (Verification Tasks)
  // ==========================================================================

  // --- Task A: 10G Ethernet Packet Injector ---
  task automatic drive_network_packet();
    begin
      $display("[NET_DRIVER] Injecting misaligned 10G UDP market data packet...");

      // TODO: Synchronize with @(posedge clk_net)
      // TODO: Set axis_bus.tvalid high and stream 5 words of dummy headers (Cycles 1-5)
      // TODO: Construct Cycle 6 (Word 5) -> Inject byte-misaligned msg_type & partial price
      // TODO: Construct Cycle 7 (Word 6) -> Inject remaining volume bytes & assert axis_bus.tlast
      // TODO: Clear axis_bus signals back to idle state
    end
  endtask

  // --- Task B: PCIe Memory-Mapped AXI4-Lite Write Driver ---
  task automatic axil_write(input logic [31:0] addr, input logic [31:0] data);
    begin
      // TODO: Sync with @(posedge clk_pcie)
      // TODO: Drive awaddr/awvalid and wdata/wvalid channels independently
      // TODO: Wait for awready and wready handshakes from the register_bank
      // TODO: Reset valid signals after successful transaction commit
    end
  endtask

  // --- Task C: PCIe Memory-Mapped AXI4-Lite Read Driver ---
  task automatic axil_read(input logic [31:0] addr, output logic [31:0] data);
    begin
      // TODO: Sync with @(posedge clk_pcie)
      // TODO: Drive araddr and arvalid lines high
      // TODO: Wait for arready handshake from register_bank
      // TODO: Capture s_axi.rdata once rvalid asserts, then drive rready to finish
    end
  endtask

  // ==========================================================================
  // PART 4: Main Test Flow Executive (Procedural Simulation)
  // ==========================================================================
  initial begin
    // Reset Sequence Activation
    rst_n = 1'b0;
    #100;
    @(posedge clk_pcie);
    rst_n = 1'b1;
    $display("[SYS_TB] Reset sequence completed. System is armed.");

    // Stabilize pipeline clocks
    repeat (10) @(posedge clk_pcie);

    // --- Main Verification Scenario ---
    // TODO: Step 1. Fork/Run drive_network_packet() to stream live market feed
    // TODO: Step 2. Simulate C++ application polling loops using axil_read tasks
    // TODO: Step 3. Once data_valid register hits 1, extract price/volume and display
    // TODO: Step 4. Execute C++ Handshake Acknowledgment via axil_write task

    #1000;
    $display("[SYS_TB] Simulation finished successfully with zero-deadlock.");
    $finish;
  end

endmodule
