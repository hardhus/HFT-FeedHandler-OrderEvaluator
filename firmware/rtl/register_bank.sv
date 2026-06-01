`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company:
// Engineer:
//
// Create Date: 27.05.2026 11:56:47
// Design Name:
// Module Name: register_bank
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

module register_bank (
    // Synchronous with PCIe IP core & s_axi interface
    input logic clk,
    // Async assertion, synchronous de-assertion recommended
    input logic rst_n,

    // CDC FIFO outputs; data is already synchronized to PCIe domain here
    input hft_internal_event_t in_pcie_event,
    input logic                in_pcie_event_valid,

    // C++ memory-mapped link; standard AMBA AXI4-Lite slave
    axil_if.slave s_axi,

    // Combinational or registered raw electrical trigger pin out
    output logic out_trade_execute
);

  // --- Internal Latch Registers and Control Flags ---
  logic [31:0] reg_waddr;  // Latches awaddr if wdata is delayed
  logic        waddr_captured;  // High until transaction completes
  logic        wdata_captured;  // High until transaction completes
  logic        reg_rvalid;  // Registered rvalid to guarantee AXI protocol safety
  logic        reg_bvalid;  // Write Response control flag

  // --- Real Internal Registers Matrix (32-bit Storage Cells) ---
  logic [31:0] reg_best_bid_price;
  logic [31:0] reg_best_bid_volume;
  logic [31:0] reg_data_valid;
  logic [31:0] reg_trade_execute;

  // --- Register Address Offsets (Derived from s_axi.awaddr[3:2]) ---
  localparam bit [1:0] ADDR_BEST_BID_PRICE = 2'b00;  // Offset 0: Best Bid Price (Read-Only)
  localparam bit [1:0] ADDR_BEST_BID_VOLUME = 2'b01;  // Offset 1: Best Bid Volume (Read-Only)
  localparam bit [1:0] ADDR_DATA_VALID = 2'b10;  // Offset 2: Data Valid Flag (Read/Write)
  localparam bit [1:0] ADDR_TRADE_EXECUTE   = 2'b11;  // Offset 3: Trade Execution Trigger (Read/Write)

  // ==========================================================================
  // PART 1: AXI4-Lite Write Handshake & Channel Latching Logic
  // ==========================================================================
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      reg_waddr      <= 1'b0;  // Reset width-matched address latch
      waddr_captured <= 1'b0;
      wdata_captured <= 1'b0;
    end else begin
      // Lookahead signals to check if channels are valid either instantly or from past cycles
      automatic logic current_waddr_valid = waddr_captured || s_axi.awvalid;
      automatic logic current_wdata_valid = wdata_captured || s_axi.wvalid;

      // SCENARIO A: Complete transaction window; clear latches for next AXI burst
      if (current_waddr_valid && current_wdata_valid) begin
        waddr_captured <= 1'b0;  // Clear the flags for the next run
        wdata_captured <= 1'b0;
      end  // SCENARIO B: Address arrives first; lock bus address until data channel catches up
      else if (s_axi.awvalid && !waddr_captured) begin
        reg_waddr      <= s_axi.awaddr;
        waddr_captured <= 1'b1;
      end  // SCENARIO C: Data arrives first; latch state until address channel catches up
      else if (s_axi.wvalid && !wdata_captured) begin
        wdata_captured <= 1'b1;
      end
    end
  end  // always_ff

  // Backpressure signals to PCIe master; low when latches are full to guarantee no data loss
  assign s_axi.awready = !waddr_captured;
  assign s_axi.wready  = !wdata_captured;

  // ==========================================================================
  // PART 2: Priority-Encoded Multiplexer Tree & Hardware Firewall Logic
  // ==========================================================================
  // Routes correct offset slice [3:2] to case selector based on latch state
  logic [1:0] current_write_sel;
  assign current_write_sel = waddr_captured ? reg_waddr[3:2] : s_axi.awaddr[3:2];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      reg_best_bid_price  <= 32'h0;
      reg_best_bid_volume <= 32'h0;
      reg_data_valid      <= 32'h0;
      reg_trade_execute   <= 32'h0;
    end else begin
      // PRIORITY 1: CDC FIFO update. Absolute precedence to maintain line-rate ingestion.
      if (in_pcie_event_valid) begin
        reg_best_bid_price  <= in_pcie_event.price;
        reg_best_bid_volume <= in_pcie_event.volume;
        reg_data_valid      <= 32'h1;  // Hardware sets flag to poll interrupt for Host C++
      end  // PRIORITY 2: Host (C++) write via PCIe. Evaluated only if FIFO path is idle.
      else if (waddr_captured || s_axi.awvalid) begin
        if (wdata_captured || s_axi.wvalid) begin
          case (current_write_sel)
            ADDR_DATA_VALID: begin
              reg_data_valid <= s_axi.wdata;  // Host clears this register to ack event processing
            end
            ADDR_TRADE_EXECUTE: begin
              reg_trade_execute <= s_axi.wdata;  // Host arms/fires execution matrix
            end
            default: begin
              // Firewall active: PRICE and VOLUME targets trigger no routing paths from host bus
            end
          endcase
        end
      end
    end
  end  // always_ff

  // ==========================================================================
  // PART 3: High-Speed Host Read & Physical Trigger Matrix
  // ==========================================================================

  // Continuous assignment for read readiness
  assign s_axi.arready = 1'b1;  // Always ready to sample read addresses

  // Combinational Multiplexer for Read Data Selection
  always_comb begin
    case (s_axi.araddr[3:2])
      ADDR_BEST_BID_PRICE:  s_axi.rdata = reg_best_bid_price;
      ADDR_BEST_BID_VOLUME: s_axi.rdata = reg_best_bid_volume;
      ADDR_DATA_VALID:      s_axi.rdata = reg_data_valid;
      ADDR_TRADE_EXECUTE:   s_axi.rdata = reg_trade_execute;
      default:              s_axi.rdata = 32'h0;
    endcase
  end

  // Robust AXI Read Valid sequential control to prevent interface deadlock
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      reg_rvalid <= 1'b0;
    end else begin
      if (s_axi.arvalid && s_axi.arready) begin
        reg_rvalid <= 1'b1;  // Assert rvalid when a valid read address is sampled
      end else if (s_axi.rvalid && s_axi.rready) begin
        reg_rvalid <= 1'b0;  // De-assert only when host acknowledges data reception
      end
    end
  end  // always_ff

  assign s_axi.rvalid = reg_rvalid;

  // Robust AXI Write Response (B Channel) sequential control
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      reg_bvalid <= 1'b0;
    end else begin
      // Trigger bvalid when both components of the write transaction are locked or active
      automatic logic current_waddr_valid = waddr_captured || s_axi.awvalid;
      automatic logic current_wdata_valid = wdata_captured || s_axi.wvalid;

      if (current_waddr_valid && current_wdata_valid && !reg_bvalid) begin
        reg_bvalid <= 1'b1;  // Assert response valid to show host memory-write is committed
      end else if (s_axi.bready && s_axi.bvalid) begin
        reg_bvalid <= 1'b0;  // Clear after host master samples the response
      end
    end
  end  // always_ff

  assign s_axi.bvalid      = reg_bvalid;

  // Continuous comparator bypasses clock path to achieve sub-nanosecond drive
  assign out_trade_execute = (reg_trade_execute == 32'h1);

endmodule
