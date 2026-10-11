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


`timescale 1ns / 1ps

package hft_pkg;

  // ==========================================
  // NETWORK PROTOCOL CONSTANTS
  // ==========================================
  // Standard IPv4 Protocol ID for UDP is 17 (0x11)
  localparam logic [7:0] UDP_PROTOCOL_ID = 8'h11;

  // Target UDP Port for our HFT Feed Handler (Example: Port 1234 -> 0x04D2)
  localparam logic [15:0] TARGET_UDP_PORT = 16'h04D2;

  // ==========================================
  // MARKET DATA MESSAGE TYPES (NASDAQ ITCH Style)
  // ==========================================
  typedef enum logic [7:0] {
    MSG_ORDER_ADD     = 8'h41,  // ASCII 'A' - Add New Order
    MSG_ORDER_EXECUTE = 8'h45,  // ASCII 'E' - Order Executed
    MSG_ORDER_CANCEL  = 8'h43   // ASCII 'C' - Order Cancelled
  } msg_type_e;

  // ==========================================
  // PACKED STRUCTS FOR MARKET EVENTS
  // ==========================================
  // All structs are PACKED to ensure strict memory alignment for network byte stream.
  // Big-Endian format is assumed from the wire.

  // 1. Structure for 'Add Order' Message Payload
  typedef struct packed {
    logic [31:0] order_id;  // Unique identifier for the order
    logic [31:0] price;     // Stock price (scaled integer, e.g., $95.00 -> 9500)
    logic [31:0] volume;    // Number of shares
    logic [7:0]  side;      // Buy = ASCII 'B' (0x42), Sell = ASCII 'S' (0x53)
  } order_add_t;

  // 2. Structure for 'Order Executed' Message Payload
  typedef struct packed {
    logic [31:0] order_id;     // ID of the order being executed
    logic [31:0] exec_price;   // Match price of execution
    logic [31:0] exec_volume;  // Executed number of shares
  } order_exec_t;

  // 3. Complete Parsed System Event passed to Register Bank/FIFO
  typedef struct packed {
    logic [7:0]  msg_type;  // Type derived from msg_type_e
    logic [31:0] price;     // Active price for decision engine
    logic [31:0] volume;    // Active volume for decision engine
  } hft_internal_event_t;

endpackage
