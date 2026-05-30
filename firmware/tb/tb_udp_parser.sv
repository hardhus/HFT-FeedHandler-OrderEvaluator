`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 27.05.2026 12:27:03
// Design Name: 
// Module Name: tb_udp_parser
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


module tb_udp_parser ();

  // Testbench Sinyalleri
  logic        clk;
  logic        reset_n;
  logic [ 7:0] s_axis_tdata;
  logic        s_axis_tvalid;
  logic        s_axis_tready;
  logic        s_axis_tlast;

  logic [31:0] parser_price;
  logic [31:0] parser_volume;
  logic        parser_data_valid;

  // UUT (Unit Under Test) - Modülü Bağlıyoruz
  udp_parser uut (
      .clk              (clk),
      .reset_n          (reset_n),
      .s_axis_tdata     (s_axis_tdata),
      .s_axis_tvalid    (s_axis_tvalid),
      .s_axis_tready    (s_axis_tready),
      .s_axis_tlast     (s_axis_tlast),
      .parser_price     (parser_price),
      .parser_volume    (parser_volume),
      .parser_data_valid(parser_data_valid)
  );

  // 100 MHz Saat Vuruşu Üretici (Clock Generator)
  always #5 clk = ~clk;

  // Görev (Task): Tek bir byte'ı AXI-Stream protokolüne uygun gönderme
  task automatic send_byte(input logic [7:0] data, input logic last_byte);
    begin
      s_axis_tdata  = data;
      s_axis_tvalid = 1'b1;
      s_axis_tlast  = last_byte;
      @(posedge clk);
      // Modül ready değilse (bizimki sürekli ready ama standart gereği) beklemek gerekir
      while (!s_axis_tready) @(posedge clk);
      s_axis_tvalid = 1'b0;
      s_axis_tlast  = 1'b0;
    end
  endtask

  // Ana Test Akışı
  initial begin
    // İlk Değer Atamaları
    clk           = 1'b0;
    reset_n       = 1'b0;
    s_axis_tdata  = 8'h0;
    s_axis_tvalid = 1'b0;
    s_axis_tlast  = 1'b0;

    // Reset Durumu
    #20;
    reset_n = 1'b1;
    #20;

    $display("[TB] --- UDP Paket Simülasyonu Başlatılıyor ---");

    // 1. ADIM: 42 Byte Hat Başlıklarını (Headers) Gönder
    // Modülün durum makinesinin ST_SKIP_HEADERS durumunda kalması gerekir.
    for (int i = 0; i < 42; i++) begin
      send_byte(8'hAA, 1'b0);  // Sahte MAC/IP/UDP Header byte'ları
    end

    // 2. ADIM: 4 Byte Ham Fiyat Verisi Gönder (Değer: 95 -> 32'h0000005F)
    send_byte(8'h00, 1'b0);
    send_byte(8'h00, 1'b0);
    send_byte(8'h00, 1'b0);
    send_byte(8'h5F, 1'b0);

    // 3. ADIM: 4 Byte Ham Hacim Verisi Gönder (Değer: 500 -> 32'h000001F4)
    send_byte(8'h00, 1'b0);
    send_byte(8'h00, 1'b0);
    send_byte(8'h01, 1'b0);
    send_byte(8'hF4, 1'b1);  // Paketin SON byte'ı olduğu için tlast = 1

    // Hat boşluğuna düşme simülasyonu (Idle durumu)
    @(posedge clk);
    s_axis_tvalid = 1'b0;

    // Kontrol Noktası (Assertion)
    #1;  // Çıkışların oturması için çok kısa bekle
    if (parser_data_valid && (parser_price == 32'd95) && (parser_volume == 32'd500)) begin
      $display("[TB] BAŞARILI: Fiyat (95) ve Hacim (500) hatasız ayrıştırıldı! [OK]");
    end else begin
      $display("[TB] HATA: Parser çıktıları beklenen değerlerle uyuşmuyor! [FAIL]");
    end

    #50;
    $display("[TB] --- Simülasyon Kapatılıyor ---");
    $finish;
  end

endmodule
