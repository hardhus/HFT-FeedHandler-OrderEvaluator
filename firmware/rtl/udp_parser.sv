`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 27.05.2026 12:13:35
// Design Name: 
// Module Name: udp_parser
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


module udp_parser (
    input logic clk,
    input logic reset_n,

    // Ethernet MAC'ten gelen AXI4-Stream Giriş Arayüzü
    input  logic [7:0] s_axis_tdata,   // Akıştaki anlık byte
    input  logic       s_axis_tvalid,  // Veri şu an geçerli mi?
    output logic       s_axis_tready,  // Parser veri almaya hazır mı? (Sürekli 1)
    input  logic       s_axis_tlast,   // Paketin son byte'ı mı?

    // Register Bankasına Gidecek Çıkışlar
    output logic [31:0] parser_price,
    output logic [31:0] parser_volume,
    output logic        parser_data_valid
);

  // Sayaç ve Durum Tanımlamaları
  typedef enum logic [1:0] {
    ST_IDLE         = 2'b00,
    ST_SKIP_HEADERS = 2'b01,
    ST_PARSE_DATA   = 2'b10
  } state_t;

  state_t current_state, next_state;
  logic [ 5:0] byte_cnt;  // Başlıkları saymak için sayaç (Maks 42 byte sayacak)
  logic [ 2:0] data_cnt;  // 8 byte payload'u kendi içinde saymak için sayaç

  // Geçici veri biriktirme yazmaçları (Shift Register)
  logic [31:0] price_reg;
  logic [31:0] volume_reg;

  // AXI-Stream Ready sinyalini sürekli yukarıda tutuyoruz (Hat hızında işleme)
  assign s_axis_tready = 1'b1;

  // 1. KISIM: Durum Geçişleri (Sequential State Transition)
  always_ff @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
      current_state <= ST_IDLE;
      byte_cnt      <= 6'd0;
      data_cnt      <= 3'd0;
    end else begin
      current_state <= next_state;

      // Header byte sayacı kontrolü
      if (current_state == ST_SKIP_HEADERS && s_axis_tvalid) begin
        byte_cnt <= byte_cnt + 1'b1;
      end else if (current_state == ST_IDLE) begin
        byte_cnt <= 6'd0;
      end

      // Payload data byte sayacı kontrolü
      if (current_state == ST_PARSE_DATA && s_axis_tvalid) begin
        data_cnt <= data_cnt + 1'b1;
      end else if (current_state == ST_IDLE) begin
        data_cnt <= 3'd0;
      end
    end
  end

  // 2. KISIM: Sonraki Durum Mantığı (Combinational Next State Logic)
  always_comb begin
    next_state = current_state;
    case (current_state)
      ST_IDLE: begin
        // Geçerli bir paket akışı başladığı an başlıkları atlamaya geç
        if (s_axis_tvalid) next_state = ST_SKIP_HEADERS;
      end

      ST_SKIP_HEADERS: begin
        // Toplam başlık boyutu: 14 (MAC) + 20 (IP) + 8 (UDP) = 42 Byte.
        // 41. byte'ı gördüğümüzde bir sonraki vuruşta payload başlayacak demektir.
        if (s_axis_tvalid && byte_cnt == 6'd41) next_state = ST_PARSE_DATA;
      end

      ST_PARSE_DATA: begin
        // 4 byte fiyat + 4 byte hacim = 8 Byte veri okunduğunda veya paket bittiğinde çık
        if (s_axis_tvalid && (data_cnt == 3'd7 || s_axis_tlast)) next_state = ST_IDLE;
      end

      default: next_state = ST_IDLE;
    endcase
  end

  // 3. KISIM: Veri Yakalama ve Bit-Slicing Mantığı
  always_ff @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
      price_reg         <= 32'h0;
      volume_reg        <= 32'h0;
      parser_price      <= 32'h0;
      parser_volume     <= 32'h0;
      parser_data_valid <= 1'b0;
    end else begin
      parser_data_valid <= 1'b0;  // Default olarak pasif

      if (current_state == ST_PARSE_DATA && s_axis_tvalid) begin
        // Ağdan gelen büyük-endian (Big-Endian) veriyi shift ederek dolduruyoruz
        case (data_cnt)
          // İlk 4 byte: Fiyat (Price)
          3'd0: price_reg[31:24] <= s_axis_tdata;
          3'd1: price_reg[23:16] <= s_axis_tdata;
          3'd2: price_reg[15:8] <= s_axis_tdata;
          3'd3: price_reg[7:0] <= s_axis_tdata;

          // Sonraki 4 byte: Hacim (Volume)
          3'd4: volume_reg[31:24] <= s_axis_tdata;
          3'd5: volume_reg[23:16] <= s_axis_tdata;
          3'd6: volume_reg[15:8] <= s_axis_tdata;
          3'd7: begin
            volume_reg[7:0]   <= s_axis_tdata;

            // Paket başarıyla tamamlandı, çıkışları güncelle ve register bankasını tetikle
            parser_price      <= price_reg;
            parser_volume     <= {volume_reg[31:8], s_axis_tdata};  // Son byte'ı anlık yakala
            parser_data_valid <= 1'b1;  // Register bankasına "YAZ" emri
          end
          default: begin
          end
        endcase
      end
    end
  end

endmodule
