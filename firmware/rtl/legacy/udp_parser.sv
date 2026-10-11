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


import hft_pkg::*;

module udp_parser (
    input logic clk,
    input logic rst_n,

    // --- RUNTIME CONFIGURATION PINS ---
    // C++ dünyasından veya sistemden gelen statik ağ adresleri (IP-Ready)
    input logic [47:0] local_mac,   // FPGA'in kendi MAC adresi
    input logic [31:0] local_ip,    // FPGA'in kendi IP adresi
    input logic [15:0] target_port, // Dinamik olarak izlenen HFT UDP Portu

    // --- AXIS NETWORK INTERFACE (From 10G EMAC IP) ---
    axis_if.slave s_axis,

    // --- CDC FIFO OUTPUT INTERFACE ---
    output hft_internal_event_t out_parsed_event,
    output logic                out_event_valid
);

  // 10G EMAC her zaman veri basabilir, backpressure uygulamıyoruz (Cut-Through)
  assign s_axis.tready = 1'b1;

  // --- INTERNAL COUNTERS & REGISTERS ---
  logic                [ 5:0] word_cnt;  // 64-bit kelime sayacı
  logic                [15:0] byte_cnt;  // Canlı paket byte sayacı (Length varyasyonları için)
  logic                [15:0] udp_length_reg;  // UDP Header'dan çekilen dinamik paket boyu

  // --- PROTOCOL FIREWALL FLAGS ---
  logic                       eth_is_ipv4;
  logic                       ip_is_udp;
  logic                       mac_matches;
  logic                       ip_matches;
  logic                       port_matches;
  logic                       ihl_valid;
  logic                       firewall_passed;
  logic                       ip_match_part1;

  // --- TEMPORARY ACCUMULATORS ---
  logic                [ 7:0] msg_type_reg;
  logic                [31:0] price_reg;

  // Internal registers for glitch-free registered output
  hft_internal_event_t        parsed_event_reg;
  logic                       event_valid_reg;

  assign out_parsed_event = parsed_event_reg;
  assign out_event_valid  = event_valid_reg;

  // ==========================================================================
  // PART 1: Word and Byte Counter Management
  // ==========================================================================
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      word_cnt <= 6'd0;
      byte_cnt <= 16'd0;
    end else begin
      if (s_axis.tvalid) begin
        if (s_axis.tlast) begin
          word_cnt <= 6'd0;
          byte_cnt <= 16'd0;
        end else begin
          word_cnt <= word_cnt + 6'd1;
          byte_cnt <= byte_cnt + 16'd8;
        end
      end
    end
  end

  // ==========================================================================
  // PART 2: Line-Rate Firewall Engine (STRICT BIG-ENDIAN ALIGNED)
  // ==========================================================================
  assign firewall_passed = eth_is_ipv4 && ip_is_udp && mac_matches && ip_matches && port_matches && ihl_valid;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      eth_is_ipv4    <= 1'b0;
      ip_is_udp      <= 1'b0;
      mac_matches    <= 1'b0;
      ip_matches     <= 1'b0;
      port_matches   <= 1'b0;
      ihl_valid      <= 1'b0;
      udp_length_reg <= 16'd0;
      msg_type_reg   <= 8'h0;
      price_reg      <= 32'h0;
    end else begin
      if (s_axis.tvalid) begin
        if (s_axis.tlast) begin
          eth_is_ipv4    <= 1'b0;
          ip_is_udp      <= 1'b0;
          mac_matches    <= 1'b0;
          ip_matches     <= 1'b0;
          port_matches   <= 1'b0;
          ihl_valid      <= 1'b0;
          udp_length_reg <= 16'd0;
        end else begin
          case (word_cnt)
            // WORD 0: Dest MAC adresi [63:16] konumundadır
            6'd0: begin
              if (s_axis.tdata[63:16] == local_mac) begin
                mac_matches <= 1'b1;
              end
            end

            // WORD 1: EtherType [31:16], IP Version [15:12], IHL [11:8]
            6'd1: begin
              if (s_axis.tdata[31:16] == 16'h0800) begin
                eth_is_ipv4 <= 1'b1;
              end
              if (s_axis.tdata[11:8] == 4'h5) begin
                ihl_valid <= 1'b1;
              end
            end

            // WORD 2: IP Protocol [7:0] konumundadır (8'h11 = UDP)
            6'd2: begin
              if (s_axis.tdata[7:0] == 8'h11) begin
                ip_is_udp <= 1'b1;
              end
            end

            // WORD 3: Source IP sonu ve Dest IP başlangıcı [15:0]
            6'd3: begin
              // IP Adresinin ilk iki byte'ı (Örn: 192.168)
              if (s_axis.tdata[15:0] == local_ip[31:16]) begin
                ip_match_part1 <= 1'b1;
              end
            end

            // WORD 4: Dest IP kalanı, UDP Dest Port, UDP Length
            6'd4: begin
              // IP Adresinin son iki byte'ı (Örn: 1.10) ve İlk yarı onayı!
              if ((s_axis.tdata[63:48] == local_ip[15:0]) && ip_match_part1) begin
                ip_matches <= 1'b1;
              end
              if (s_axis.tdata[31:16] == target_port) begin
                port_matches <= 1'b1;
              end
              udp_length_reg <= s_axis.tdata[15:0];
            end

            // WORD 5: Market Data Extraction (Msg Type & Price)
            6'd5: begin
              msg_type_reg <= s_axis.tdata[47:40];
              // BIG-ENDIAN BYTE SWAP: Ağdan gelen ters byte'ları C++ için düzelt!
              price_reg <= {
                s_axis.tdata[15:8], s_axis.tdata[23:16], s_axis.tdata[31:24], s_axis.tdata[39:32]
              };
            end

            default: ;
          endcase
        end
      end
    end
  end

  // ==========================================================================
  // PART 3: Atomic Packet Writing & Glitch-Free Valid Generator
  // ==========================================================================
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      parsed_event_reg <= '0;
      event_valid_reg  <= 1'b0;
    end else begin
      event_valid_reg <= 1'b0;

      if (s_axis.tvalid) begin
        // WORD 6: Extract Volume & Commit to FIFO if Firewall passes
        if (word_cnt == 6'd6) begin
          // GÜNCELLEME: Sadece Firewall'u geçen VE 'Yeni Emir' (Add Order) olan paketleri kabul et!
          if (firewall_passed && (msg_type_reg == MSG_ORDER_ADD)) begin
            parsed_event_reg.msg_type <= msg_type_reg;
            parsed_event_reg.price    <= price_reg;
            parsed_event_reg.volume   <= {8'h00, s_axis.tdata[63:40]};

            if ((byte_cnt + 16'd8) <= (udp_length_reg + 16'd34)) begin
              event_valid_reg <= 1'b1;
            end
          end else if (!firewall_passed) begin
            $display("[FIREWALL REJECT] Packet failed line-rate address verification.");
          end else begin
            $display("[PARSER IGNORE] Valid packet, but not an Order Add ('A') message. Ignored.");
          end
        end
      end
    end
  end
endmodule
