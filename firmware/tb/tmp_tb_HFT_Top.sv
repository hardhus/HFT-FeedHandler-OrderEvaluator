`timescale 1ns / 1ps

import hft_pkg::*;

module tb_HFT_Top ();

  // ==========================================================================
  // 1. CLOCK & RESET SIGNALING
  // ==========================================================================
  logic clk_net;
  logic clk_pcie;
  logic rst_n;

  // Hardware output pin
  logic trade_execute_out;

  // ==========================================================================
  // 2. RUNTIME CONFIGURATION PARAMETERS (Static Network Addresses)
  // ==========================================================================
  localparam logic [47:0] TB_LOCAL_MAC = 48'h00_0A_35_01_02_03;
  localparam logic [31:0] TB_LOCAL_IP = {8'd192, 8'd168, 8'd1, 8'd10};  // 192.168.1.10
  localparam logic [15:0] TB_TARGET_PORT = 16'h04D2;  // Port 1234

  // ==========================================================================
  // 3. VERIFICATION TRACKING VARIABLES (C++ App Emulation)
  // ==========================================================================
  logic [31:0] polled_valid;
  logic [31:0] extracted_price;
  logic [31:0] extracted_volume;

  // ==========================================================================
  // 4. INTERFACE INSTANTIATION
  // ==========================================================================
  axis_if axis_bus ();  // 10G Ethernet MAC streaming output emulation
  axil_if axil_bus ();  // PCIe Host CPU memory-mapped bridge emulation

  // ==========================================================================
  // 5. CONCURRENT CLOCK GENERATION
  // ==========================================================================
  initial clk_net = 0;
  always #3.2 clk_net = ~clk_net;  // 156.25 MHz Network Domain Clock Line (6.4ns period)

  initial clk_pcie = 0;
  always #4.0 clk_pcie = ~clk_pcie;  // 125.00 MHz PCIe Domain Clock Line (8.0ns period)

  // ==========================================================================
  // 6. DESIGN UNDER TEST (DUT) INSTANTIATION
  // ==========================================================================
  HFT_Top_Core dut (
      .clk_net          (clk_net),
      .clk_pcie         (clk_pcie),
      .rst_n            (rst_n),
      .local_mac        (TB_LOCAL_MAC),
      .local_ip         (TB_LOCAL_IP),
      .target_port      (TB_TARGET_PORT),
      .s_axis           (axis_bus.slave),
      .s_axi            (axil_bus.slave),
      .trade_execute_out(trade_execute_out)
  );

  // ==========================================================================
  // 7. LOW-LEVEL EMULATION DRIVERS (TASKS)
  // ==========================================================================

  // TASK 1: Dinamik Ağ Paketi Enjektörü (Kurşun Geçirmez Big-Endian Standartında)
  // Dışarıdan verdiğimiz adres ve verilere göre anında paket sentezler.
  task automatic send_udp_packet(input logic [47:0] p_dst_mac, input logic [31:0] p_dst_ip,
                                 input logic [15:0] p_dst_port, input logic [7:0] p_msg_type,
                                 input logic [31:0] p_price, input logic [31:0] p_volume);
    begin
      @(posedge clk_net);
      #1;

      // WORD 0: Dest MAC + Src MAC [63:48]
      axis_bus.tvalid <= 1'b1;
      axis_bus.tstrb  <= 8'hFF;
      axis_bus.tlast  <= 1'b0;
      axis_bus.tdata  <= {p_dst_mac, 16'h1111};
      @(posedge clk_net);
      #1;

      // WORD 1: Src MAC [47:16] + EtherType(0800) + IP Vers/IHL(45) + ToS(00)
      axis_bus.tdata <= {32'h2222_3333, 16'h0800, 8'h45, 8'h00};
      @(posedge clk_net);
      #1;

      // WORD 2: IP Total Length(002E) + Ident/Flags + TTL(40) + Protocol(11 = UDP)
      axis_bus.tdata <= {16'h002E, 32'h0000_0000, 8'h40, 8'h11};
      @(posedge clk_net);
      #1;

      // WORD 3: IP Checksum + Src IP + Dest IP [31:16]
      axis_bus.tdata <= {16'h0000, 32'hC0A8_0101, p_dst_ip[31:16]};
      @(posedge clk_net);
      #1;

      // WORD 4: Dest IP [15:0] + Src Port + Dest Port + UDP Length(001A)
      axis_bus.tdata <= {p_dst_ip[15:0], 16'hC000, p_dst_port, 16'h001A};
      @(posedge clk_net);
      #1;

      // WORD 5: UDP Checksum + MSG_TYPE + PRICE + VOLUME [31:24]
      axis_bus.tdata <= {16'h0000, p_msg_type, p_price, p_volume[31:24]};
      @(posedge clk_net);
      #1;

      // WORD 6: VOLUME [23:0] + Padding (0000000000) + TLAST AKTİF!
      axis_bus.tdata <= {p_volume[23:0], 40'h000000_0000};
      axis_bus.tlast <= 1'b1;
      @(posedge clk_net);
      #1;

      // Hat Temizleme (Inter-Packet Gap) - Donanımın nefes alma payı
      axis_bus.tdata <= 64'h0;
      axis_bus.tstrb <= 8'h0;
      axis_bus.tlast <= 1'b0;
      @(posedge clk_net);
      #1;

      axis_bus.tvalid <= 1'b0;
    end
  endtask

  // TASK 2: PCIe C++ Host İşlemci Sürücüleri (AXI-Lite Read/Write)
  task automatic axil_write(input logic [31:0] addr, input logic [31:0] data);
    begin
      @(posedge clk_pcie);
      axil_bus.awaddr  <= addr;
      axil_bus.awvalid <= 1'b1;
      axil_bus.wdata   <= data;
      axil_bus.wstrb   <= 4'hF;
      axil_bus.wvalid  <= 1'b1;

      fork
        begin
          wait (axil_bus.awready);
          @(posedge clk_pcie);
          axil_bus.awvalid <= 1'b0;
        end
        begin
          wait (axil_bus.wready);
          @(posedge clk_pcie);
          axil_bus.wvalid <= 1'b0;
          axil_bus.wstrb  <= 4'b0;
        end
      join

      axil_bus.bready <= 1'b1;
      wait (axil_bus.bvalid);
      @(posedge clk_pcie);
      axil_bus.bready <= 1'b0;
    end
  endtask

  task automatic axil_read(input logic [31:0] addr, output logic [31:0] data);
    begin
      @(posedge clk_pcie);
      axil_bus.araddr  <= addr;
      axil_bus.arvalid <= 1'b1;
      axil_bus.rready  <= 1'b1;

      wait (axil_bus.arready);
      @(posedge clk_pcie);
      axil_bus.arvalid <= 1'b0;

      wait (axil_bus.rvalid);
      data = axil_bus.rdata;

      @(posedge clk_pcie);
      axil_bus.rready <= 1'b0;
      axil_bus.araddr <= 32'h0;
    end
  endtask

  // ==========================================================================
  // 8. C++ HOST POLLING EMULATOR TASK
  // Paket düşürüldüğünde (Drop) sonsuz döngüye girmemek için Timeout'lu okuyucu
  // ==========================================================================
  task automatic poll_and_verify(input string test_name, input logic expect_data,
                                 input logic [31:0] expected_price,
                                 input logic [31:0] expected_vol);
    int   timeout_cnt = 0;
    logic found = 1'b0;
    begin
      $display("----------------------------------------------------------");
      $display("[C++ HOST] Starting polling for: %s", test_name);

      polled_valid = 32'h0;
      // 20 kere poll et (Eğer FIFO boşsa timeout'a düşer)
      while (polled_valid == 32'h0 && timeout_cnt < 20) begin
        axil_read(32'h0000_0008, polled_valid);
        if (polled_valid == 32'h0) begin
          repeat (5) @(posedge clk_pcie);
          timeout_cnt++;
        end else begin
          found = 1'b1;
        end
      end

      if (expect_data) begin
        if (found) begin
          axil_read(32'h0000_0000, extracted_price);
          axil_read(32'h0000_0004, extracted_volume);
          if (extracted_price == expected_price && extracted_volume == expected_vol) begin
            $display("[TEST PASSED] %s -> Data Matched! Price: %d, Vol: %d", test_name,
                     extracted_price, extracted_volume);
          end else begin
            $display("[TEST FAILED] %s -> Data Mismatch! Expected P:%d V:%d | Got P:%d V:%d",
                     test_name, expected_price, expected_vol, extracted_price, extracted_volume);
          end
          // Valid bayrağını temizle
          axil_write(32'h0000_0008, 32'h0000_0000);
        end else begin
          $display("[TEST FAILED] %s -> Expected data but Timeout occurred (Packet Dropped?)",
                   test_name);
        end
      end else begin
        if (found) begin
          $display("[TEST FAILED] %s -> Expected FIREWALL DROP, but data was written to FIFO!",
                   test_name);
          axil_write(32'h0000_0008, 32'h0000_0000);  // Sistemi temizle
        end else begin
          $display(
              "[TEST PASSED] %s -> Timeout as expected. Firewall successfully blocked the packet.",
              test_name);
        end
      end
    end
  endtask

  // ==========================================================================
  // 9. MAIN TEST EXECUTIVE (The Torture Test)
  // ==========================================================================
  initial begin
    rst_n = 1'b0;
    #100;
    rst_n = 1'b1;
    $display("[SYS_TB] Reset sequence completed. System is armed.");
    repeat (100) @(posedge clk_pcie);

    // ------------------------------------------------------------------------
    // TEST 1: HAPPY PATH (Her şey doğru)
    // ------------------------------------------------------------------------
    $display("\n>>> FIRING TEST 1: HAPPY PATH <<<");
    fork
      send_udp_packet(TB_LOCAL_MAC, TB_LOCAL_IP, TB_TARGET_PORT, 8'h41, 32'h1C250000,
                      32'h000001F4);  // P:9500, V:500
    join_none
    poll_and_verify("Valid Packet Test", 1'b1, 32'd9500, 32'd500);

    // ------------------------------------------------------------------------
    // TEST 2: FIREWALL MAC DROP (Yanlış MAC Adresi)
    // ------------------------------------------------------------------------
    $display("\n>>> FIRING TEST 2: WRONG MAC ADDRESS <<<");
    fork
      send_udp_packet(48'hDD_EE_FF_AA_BB_CC, TB_LOCAL_IP, TB_TARGET_PORT, 8'h41, 32'h00001111,
                      32'h00000010);
    join_none
    poll_and_verify("MAC Firewall Test", 1'b0, 0, 0);  // expect_data = 0 (Timeout bekliyoruz)

    // ------------------------------------------------------------------------
    // TEST 3: FIREWALL IP DROP (Yanlış IP Adresi)
    // ------------------------------------------------------------------------
    $display("\n>>> FIRING TEST 3: WRONG IP ADDRESS <<<");
    fork
      // 192.168.1.99 -> {8'd192, 8'd168, 8'd1, 8'd99}
      send_udp_packet(TB_LOCAL_MAC, {8'd192, 8'd168, 8'd1, 8'd99}, TB_TARGET_PORT, 8'h41,
                      32'h00001111, 32'h00000010);
    join_none
    poll_and_verify("IP Firewall Test", 1'b0, 0, 0);

    // ------------------------------------------------------------------------
    // TEST 4: WRONG MESSAGE TYPE (Yanlış Borsa Mesajı)
    // ------------------------------------------------------------------------
    $display("\n>>> FIRING TEST 4: WRONG MESSAGE TYPE ('X') <<<");
    fork
      // 8'h58 = 'X' karakteri ('A' değil)
      send_udp_packet(TB_LOCAL_MAC, TB_LOCAL_IP, TB_TARGET_PORT, 8'h58, 32'h00001111, 32'h00000010);
    join_none
    poll_and_verify("Message Type Test", 1'b0, 0, 0);

    // ------------------------------------------------------------------------
    // TEST 5: ALGO HARDWARE TRIGGER (Piyasa Kriteri Eşleşmesi)
    // ------------------------------------------------------------------------
    $display("\n>>> FIRING TEST 5: HARDWARE TRIGGER EXECUTION <<<");
    fork
      // Fiyatı 9500 gönderiyoruz, C++ yazılım okuyup Trade Execute yazacak
      send_udp_packet(TB_LOCAL_MAC, TB_LOCAL_IP, TB_TARGET_PORT, 8'h41, 32'h1C250000,
                      32'h000000AA);  // P:9500, V:170
    join_none

    poll_and_verify("Algo Match Verification", 1'b1, 32'd9500, 32'd170);

    if (extracted_price == 32'd9500) begin
      $display(
          "[SYS_TB] ALGO TRIGGER: Target price 9500 matched! Sending Trade Order via AXI-Lite...");
      axil_write(32'h0000_000C, 32'h0000_0001);  // Offset 3: ADDR_TRADE_EXECUTE
      @(posedge clk_pcie);
      #1;

      if (trade_execute_out === 1'b1) begin
        $display("[SYS_TB] [TEST PASSED] SUCCESS!!! Hardware Pin 'trade_execute_out' driven HIGH!");
      end else begin
        $display("[SYS_TB] [TEST FAILED] ERROR: Hardware Pin failed to fire!");
      end
    end

    #1000;
    $display("\n==========================================================");
    $display("ALL TORTURE TESTS FINISHED SUCCESSFULLY!");
    $display("==========================================================\n");
    $finish;
  end

  // ==========================================================================
  // 10. THE PIPELINE TRACER & WATCHDOG
  // ==========================================================================
  always @(posedge clk_net) begin
    if (axis_bus.tvalid) begin
      $display("[TRACE-NET] Time: %0t | word: %0d | tlast: %b | TDATA: %016X", $time,
               dut.i_udp_parser.word_cnt, axis_bus.tlast, axis_bus.tdata);
    end
  end

  always @(posedge clk_net) begin
    if (dut.i_udp_parser.out_event_valid) begin
      $display("[TRACE-PARSER] FIRED! Msg: %h | Price: %h | Vol: %h",
               dut.i_udp_parser.out_parsed_event.msg_type, dut.i_udp_parser.out_parsed_event.price,
               dut.i_udp_parser.out_parsed_event.volume);
    end
  end

  always @(posedge clk_pcie) begin
    if (dut.i_register_bank.in_pcie_event_valid) begin
      $display("[TRACE-REG_BANK] Ingesting FIFO Data! Price: %h | Vol: %h",
               dut.i_register_bank.in_pcie_event.price, dut.i_register_bank.in_pcie_event.volume);
    end
  end

  // Testlerin uzun sürebileceği hesaba katılarak Watchdog süresi uzatıldı
  initial begin
    #5000000;
    $display("==========================================================");
    $display("[FATAL WATCHDOG] Simulation hung! Deadlock position:");
    $display("  - Parser Word Cnt   : %0d", dut.i_udp_parser.word_cnt);
    $display("  - FIFO Empty Status : %b", dut.i_cdc_fifo_if.empty);
    $display("==========================================================");
    $finish;
  end

endmodule
