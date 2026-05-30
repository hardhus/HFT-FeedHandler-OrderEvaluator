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


module tb_HFT_Top ();

  logic        clk;
  logic        reset_n;
  logic [ 7:0] s_axis_tdata;
  logic        s_axis_tvalid;
  logic        s_axis_tready;
  logic        s_axis_tlast;
  logic [ 1:0] host_addr;
  logic        host_write_en;
  logic [31:0] host_write_data;
  logic [31:0] host_read_data;
  logic        trade_execute_out;

  // Sistem En Üst Modülünü Bağlıyoruz
  HFT_Top uut_top (.*);

  // Clock üretimi (100 MHz)
  always #5 clk = ~clk;

  // Byte gönderme görevi
  task automatic send_byte(input logic [7:0] data, input logic last_byte);
    begin
      s_axis_tdata  = data;
      s_axis_tvalid = 1'b1;
      s_axis_tlast  = last_byte;
      @(posedge clk);
      while (!s_axis_tready) @(posedge clk);
      s_axis_tvalid = 1'b0;
      s_axis_tlast  = 1'b0;
    end
  endtask

  initial begin
    clk             = 1'b0;
    reset_n         = 1'b0;
    s_axis_tdata    = 8'h0;
    s_axis_tvalid   = 1'b0;
    s_axis_tlast    = 1'b0;
    host_addr       = 2'b00;
    host_write_en   = 1'b0;
    host_write_data = 32'h0;

    #20 reset_n = 1'b1;
    #20;

    $display("[SYSTEM TB] --- Entegrasyon Simülasyonu Başladı ---");

    // 1. ADIM: Donanıma 42 Byte Header fırlat
    for (int i = 0; i < 42; i++) begin
      send_byte(8'hAA, 1'b0);
    end

    // 2. ADIM: Fiyat fırlat (95 -> 32'h0000005F)
    send_byte(8'h00, 1'b0);
    send_byte(8'h00, 1'b0);
    send_byte(8'h00, 1'b0);
    send_byte(8'h5F, 1'b0);

    // 3. ADIM: Hacim fırlat (500 -> 32'h000001F4) ve paket bitsin
    send_byte(8'h00, 1'b0);
    send_byte(8'h00, 1'b0);
    send_byte(8'h01, 1'b0);
    send_byte(8'hF4, 1'b1);

    // Hat boşa çıksın, donanım register'ları güncellesin
    @(posedge clk);

    // 4. ADIM: Sahte C++ Polling & Karar Mekanizması
    $display("[SYSTEM TB] C++ Yazılımı (Host) PCIe hattı üzerinden Polling yapıyor...");

    // Reg 2'yi (DATA_VALID) oku, 1 olana kadar bekle
    host_addr = 2'b10;  // Reg 2 adresi
    #1;
    while (host_read_data != 32'h1) begin
      @(posedge clk);
      #1;
    end

    $display("[SYSTEM TB] C++: Yeni veri hazır sinyali yakalandı! Veriler okunuyor...");

    // Reg 0 (Price) oku
    host_addr = 2'b00;
    #1;
    $display("[SYSTEM TB] C++ Okunan Fiyat: %0d", host_read_data);

    // 5. ADIM: C++ Emri Donanıma Yazıyor (Fiyat 95 < 100 olduğu için)
    if (host_read_data < 100) begin
      $display(
          "[SYSTEM TB] C++: Fiyat eşik altı! BUY emri PCIe üzerinden Reg 3'e yazılıyor...");
      host_addr       = 2'b11;  // Reg 3 (TRADE_EXECUTE)
      host_write_data = 32'h1;  // AL emri
      host_write_en   = 1'b1;
      @(posedge clk);
      host_write_en = 1'b0;
    end

    // Donanımın emri dışarı fırlatmasını izle
    #1;
    if (trade_execute_out == 1'b1) begin
      $display(
          "[SYSTEM TB] BAŞARILI: Tüm donanım döngüsü kapandı. Emir pini HIGH! [SUCCESS]");
    end else begin
      $display("[SYSTEM TB] HATA: Emir sinyali tetiklenemedi! [FAIL]");
    end

    #50;
    $finish;
  end

endmodule
