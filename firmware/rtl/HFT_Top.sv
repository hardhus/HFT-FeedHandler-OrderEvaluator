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


module HFT_Top (
    input logic clk,
    input logic reset_n,

    // Dış Dünya: Ethernet MAC AXI4-Stream Arayüzü (Giriş)
    input  logic [7:0] s_axis_tdata,
    input  logic       s_axis_tvalid,
    output logic       s_axis_tready,
    input  logic       s_axis_tlast,

    // Dış Dünya: Sahte PCIe / Host Arayüzü (C++ Motorunun Okuyup Yazacağı Hatlar)
    input  logic [ 1:0] host_addr,
    input  logic        host_write_en,
    input  logic [31:0] host_write_data,
    output logic [31:0] host_read_data,

    // Donanım Çıkışı: Tetiklenen Emir Sinyali
    output logic trade_execute_out
);

  // Modüller Arası Bağlantı Sinyalleri (Wires)
  logic [31:0] w_parser_price;
  logic [31:0] w_parser_volume;
  logic        w_parser_data_valid;

  // 1. MODÜL: UDP & Market Data Parser Örneklendirmesi (Instantiation)
  udp_parser i_udp_parser (
      .clk              (clk),
      .reset_n          (reset_n),
      .s_axis_tdata     (s_axis_tdata),
      .s_axis_tvalid    (s_axis_tvalid),
      .s_axis_tready    (s_axis_tready),
      .s_axis_tlast     (s_axis_tlast),
      .parser_price     (w_parser_price),
      .parser_volume    (w_parser_volume),
      .parser_data_valid(w_parser_data_valid)
  );

  // 2. MODÜL: Register Bank / PCIe Bellek Haritası Örneklendirmesi
  register_bank i_register_bank (
      .clk              (clk),
      .reset_n          (reset_n),
      .parser_price     (w_parser_price),
      .parser_volume    (w_parser_volume),
      .parser_data_valid(w_parser_data_valid),
      .host_addr        (host_addr),
      .host_write_en    (host_write_en),
      .host_write_data  (host_write_data),
      .host_read_data   (host_read_data),
      .out_trade_execute(trade_execute_out)
  );

endmodule
