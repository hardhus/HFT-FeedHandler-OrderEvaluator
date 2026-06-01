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
  logic        clk_net;
  logic        clk_pcie;
  logic        rst_n;
  logic        trade_execute_out;

  // --- Verification Tracking Register Matrix ---
  logic [31:0] polled_valid;
  logic [31:0] extracted_price;
  logic [31:0] extracted_volume;

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
  
  localparam logic [47:0] TB_LOCAL_MAC   = 48'h00_0A_35_01_02_03; // Örnek FPGA MAC adresi
  localparam logic [31:0] TB_LOCAL_IP    = {8'd192, 8'd168, 8'd1, 8'd10}; // 192.168.1.10 IP adresi
  localparam logic [15:0] TB_TARGET_PORT = 16'h04D2;              // Port 1234
  
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
  // PART 3: Low-Level Emulation Drivers
  // ==========================================================================
  task automatic drive_network_packet();
    begin
      $display("[NET_DRIVER] Injecting STRICT Big-Endian 10G UDP Market Data Packet...");
      @(posedge clk_net);
      #1;

      // WORD 0: Dest MAC (48'h00_0A_35_01_02_03) + Src MAC [63:48]
      axis_bus.tvalid <= 1'b1;
      axis_bus.tstrb  <= 8'hFF; axis_bus.tlast  <= 1'b0;
      axis_bus.tdata  <= {48'h00_0A_35_01_02_03, 16'h1111}; 
      @(posedge clk_net); #1;

      // WORD 1: Src MAC [47:16] (22223333) + EtherType (0800) + IP Vers/IHL (45) + ToS (00)
      axis_bus.tdata  <= {32'h2222_3333, 16'h0800, 8'h45, 8'h00}; 
      @(posedge clk_net); #1;

      // WORD 2: IP Total Length (002E = 46 Byte) + Ident/Flags (0000_0000) + TTL (40) + Protocol (11 = UDP)
      axis_bus.tdata  <= {16'h002E, 32'h0000_0000, 8'h40, 8'h11}; 
      @(posedge clk_net); #1;

      // WORD 3: IP Checksum (0000) + Src IP (C0A80101 = 192.168.1.1) + Dest IP [31:16] (C0A8)
      axis_bus.tdata  <= {16'h0000, 32'hC0A8_0101, 16'hC0A8}; 
      @(posedge clk_net); #1;

      // WORD 4: Dest IP [15:0] (010A = 192.168.1.10) + Src Port (C000) + Dest Port (04D2 = 1234) + UDP Length (001A = 26 Byte)
      axis_bus.tdata  <= {16'h010A, 16'hC000, 16'h04D2, 16'h001A}; 
      @(posedge clk_net); #1;

      // WORD 5: UDP Checksum (0000) + MSG_TYPE ('A' = 41) + PRICE (1C250000 = 9500) + VOLUME [31:24] (00)
      axis_bus.tdata  <= {16'h0000, 8'h41, 32'h1C250000, 8'h00}; 
      @(posedge clk_net); #1;

      // WORD 6: VOLUME [23:0] (0001F4 = 500) + Padding / Kalan Boşluklar (0000000000) + TLAST AKTİF!
      axis_bus.tdata  <= {24'h0001F4, 40'h000000_0000};
      axis_bus.tlast  <= 1'b1;
      @(posedge clk_net); #1;

      // Hat Temizleme Cycle (Inter-Packet Gap)
      axis_bus.tdata  <= 64'h0;
      axis_bus.tstrb  <= 8'h0;
      axis_bus.tlast  <= 1'b0;
      @(posedge clk_net); #1;
      
      axis_bus.tvalid <= 1'b0;
      $display("[NET_DRIVER] Packet injection complete.");
    end
  endtask

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
  // PART 4: Main Test Flow Executive
  // ==========================================================================
  initial begin
    rst_n = 1'b0;
    #100;
    rst_n = 1'b1;
    $display("[SYS_TB] Reset sequence completed. System is armed.");

    repeat (200) @(posedge clk_pcie);

    fork
      drive_network_packet();
    join_none

    $display("[SYS_TB] C++ Software starting polling loop on ADDR_DATA_VALID...");
    polled_valid = 32'h0;

    while (polled_valid == 32'h0) begin
      axil_read(32'h0000_0008, polled_valid);
      if (polled_valid == 32'h0) begin
        repeat (5) @(posedge clk_pcie);
      end
    end
    $display("[SYS_TB] MATCH! Data valid flag detected high (1).");

    $display("[SYS_TB] Extracting market data from HFT Core registers...");
    axil_read(32'h0000_0000, extracted_price);
    axil_read(32'h0000_0004, extracted_volume);

    $display("==========================================================");
    $display("[C++ APP REPORT] >>> NEW BEST BID DETECTED <<<");
    $display("[C++ APP REPORT] Price Data (Scaled) : %d", extracted_price);
    $display("[C++ APP REPORT] Volume Data (Lots)  : %d", extracted_volume);
    $display("==========================================================");
    
    // ------------------------------------------------------------------------
    // YENİ EKLEME: HFT KARAR & EMİR TETİKLEME MATRİSİ
    // ------------------------------------------------------------------------
    $display("[SYS_TB] C++ Software evaluating market conditions...");
    
    // Algoritmik Eşik Kontrolü: Fiyat $95.00 (9500) ise emri ateşle!
    if (extracted_price == 32'd9500) begin
      $display("[SYS_TB] ALGO TRIGGER: Target price matched! Sending Trade Order via AXI-Lite...");
      axil_write(32'h0000_000C, 32'h0000_0001); // Offset 3: ADDR_TRADE_EXECUTE register'ına '1' yaz
      
      // Donanımın combinational bypass hattını işlemesi için 1 cycle bekle
      @(posedge clk_pcie); 
      #1; 
      
      // Kontrol Noktası (Assertion): Donanım pini gerçekten kalktı mı?
      if (trade_execute_out === 1'b1) begin
        $display("[SYS_TB] SUCCESS!!! Hardware Pin 'trade_execute_out' driven HIGH in sub-nanosecond!");
      end else begin
        $display("[SYS_TB] ERROR: Hardware Pin 'trade_execute_out' failed to fire!");
      end
    end else begin
      $display("[SYS_TB] ALGO SKIPPED: Price does not match trading criteria.");
    end
    // ------------------------------------------------------------------------

    $display("[SYS_TB] Clearing Data Valid flag to acknowledge receipt...");
    axil_write(32'h0000_0008, 32'h0000_0000);

    #500;
    $display("[SYS_TB] Simulation finished successfully with zero-deadlock.");
    $finish;
  end

  // ==========================================================================
  // PART 5: THE PIPELINE TRACER (ZERO-GUESSWORK DEBUGGER)
  // ==========================================================================

  // 1. Data Flow Alignment Tracker (GÜNCELLENDİ)
  always @(posedge clk_net) begin
    if (axis_bus.tvalid) begin
      $display("[TRACE-1: NET_BUS] Time: %0t | word_cnt: %0d | TLAST: %b | TDATA: %016X",
               $time, dut.i_udp_parser.word_cnt, axis_bus.tlast, axis_bus.tdata);
    end
  end

  // 2. Parser Output Firing Tracker
  always @(posedge clk_net) begin
    if (dut.i_udp_parser.out_event_valid) begin
      $display("[TRACE-2: PARSER_OUT] FIRED! Msg: %h | Price: %h | Vol: %h",
               dut.i_udp_parser.out_parsed_event.msg_type,
               dut.i_udp_parser.out_parsed_event.price,
               dut.i_udp_parser.out_parsed_event.volume);
    end
  end

  // 3. FIFO Status Tracker
  always @(dut.i_cdc_fifo_if.empty) begin
    $display("[TRACE-3: FIFO] Empty flag changed to: %b at Time: %0t", dut.i_cdc_fifo_if.empty, $time);
  end

  // 4. Register Bank Ingestion Tracker
  always @(posedge clk_pcie) begin
    if (dut.i_register_bank.in_pcie_event_valid) begin
       $display("[TRACE-4: REG_BANK] Ingesting FIFO Data! Price: %h | Vol: %h",
                dut.i_register_bank.in_pcie_event.price,
                dut.i_register_bank.in_pcie_event.volume);
    end
  end

  // Smart Watchdog (GÜNCELLENDİ)
  initial begin
    #100000000; 
    $display("==========================================================");
    $display("[FATAL WATCHDOG] Simulation hung! Deadlock position:");
    $display("  - Parser Word Cnt   : %0d", dut.i_udp_parser.word_cnt);
    $display("  - FIFO Empty Status : %b", dut.i_cdc_fifo_if.empty);
    $display("  - Register Valid Bit: 0x%08X", dut.i_register_bank.reg_data_valid);
    $display("==========================================================");
    $finish;
  end
  
endmodule
