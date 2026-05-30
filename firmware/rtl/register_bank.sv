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


module register_bank (
    input logic clk,
    input logic reset_n,

    // Parser (İç Donanım) Arayüzü - FPGA buraya yazar
    input logic [31:0] parser_price,
    input logic [31:0] parser_volume,
    input logic        parser_data_valid,

    // Sahte PCIe / Host Arayüzü (C++ Simülasyonu için Okuma/Yazma)
    input  logic [ 1:0] host_addr,        // 4 register için 2-bit adres yeterli (00, 01, 10, 11)
    input  logic        host_write_en,    // Host (C++) yazma isteği
    input  logic [31:0] host_write_data,  // Host'un yazacağı veri
    output logic [31:0] host_read_data,   // Host'un okuyacağı veri

    // Çıkış Kontrolü (UDP Emir Göndericiye Gidecek Sinyal)
    output logic out_trade_execute
);

  // Register Tanımlamaları (4 adet 32-bit hücre)
  logic [31:0] reg_market_price;
  logic [31:0] reg_market_volume;
  logic [31:0] reg_data_valid;
  logic [31:0] reg_trade_execute;

  // 1. KISIM: HOST (C++) OKUMA MANTIĞI (Combinational Read)
  // C++ pointer üzerinden adresi yokladığı anda anlık cevap döner
  always_comb begin
    case (host_addr)
      2'b00:   host_read_data = reg_market_price;
      2'b01:   host_read_data = reg_market_volume;
      2'b10:   host_read_data = reg_data_valid;
      2'b11:   host_read_data = reg_trade_execute;
      default: host_read_data = 32'h0;
    endcase
  end

  // 2. KISIM: YAZMA VE GÜNCELLEME MANTIĞI (Sequential Write)
  always_ff @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
      reg_market_price  <= 32'h0;
      reg_market_volume <= 32'h0;
      reg_data_valid    <= 32'h0;
      reg_trade_execute <= 32'h0;
    end else begin

      // Eğer UDP Parser yeni bir borsa verisi yakaladıysa, öncelik donanımındır (HFT hat hızı)
      if (parser_data_valid) begin
        reg_market_price  <= parser_price;
        reg_market_volume <= parser_volume;
        reg_data_valid    <= 32'h1;  // Yazılıma "YENİ VERİ GELDİ" işareti
      end 
            // Donanım yazmıyorken, Host (C++) register'ları güncelleyebilir (Handshake veya Emir Yazma)
      else if (host_write_en) begin
        case (host_addr)
          2'b10: reg_data_valid <= host_write_data;  // C++ veriyi okuyunca burayı 0 yapacak
          2'b11: reg_trade_execute <= host_write_data;  // C++ karar verince buraya 1 (AL) yazacak
          default: begin  /* Reg 0 ve 1 Salt Okunurdur (Read-Only) */
          end
        endcase
      end
    end
  end

  // Dışarıya emrin tetiklendiğini bildiren sürekli atama
  assign out_trade_execute = (reg_trade_execute == 32'h1);

endmodule
