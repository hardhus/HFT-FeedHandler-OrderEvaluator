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
    // Connected to clk_net (156.25 MHz) from MAC IP
    input logic clk,

    // Async assertion, synchronous de-assertion recommended
    input logic reset_n,

    // Incoming network data stream; standard AMBA AXI-Stream slave
    axis_if.slave s_axis,

    // Outgoing synchronized market event straight to CDC FIFO
    output hft_internal_event_t out_parsed_event,
    output logic                out_event_valid
);

  // --- HFT Network Constraint: Line rate cannot be stopped ---
  assign s_axis.tready = 1'b1;

  // --- State Machine Type Definitions ---
  typedef enum logic [1:0] {
    ST_IDLE         = 2'b00,
    ST_SKIP_HEADERS = 2'b01,
    ST_PARSE_DATA   = 2'b10
  } state_e;

  state_e current_state, next_state;

  // --- Internal Processing Counters & Storage ---
  logic [ 5:0] byte_cnt;  // Track protocol headers up to 42 bytes
  logic [ 2:0] data_cnt;  // Track internal payload byte indexing
  logic [ 7:0] msg_type_reg;  // Buffered message type identifier from the parsed packet header
  logic [31:0] price_reg;  // Shift register to accumulate big-endian price
  logic [31:0] volume_reg;  // Shift register to accumulate big-endian volume

  // ==========================================================================
  // PART 1: Sequential State & Counter Controls (Word-Aligned)
  // ==========================================================================
  always_ff @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
      current_state <= ST_IDLE;
      byte_cnt      <= 6'd0;  // WORD (8-byte block) counter
      data_cnt      <= 3'd0;
    end else begin
      current_state <= next_state;

      // TODO: Implement the byte_cnt and data_cnt logic based on s_axis.tvalid
      // Focus on hardware tracking of the exact 64-bit word alignment

      if (s_axis.tvalid) begin
        case (current_state)
          ST_IDLE: begin
            byte_cnt <= 6'd0;
            data_cnt <= 3'd0;
          end
          ST_SKIP_HEADERS: begin
            byte_cnt <= byte_cnt + 6'd1;  // Increment by 1 word (8 bytes) per cycle
          end
          ST_PARSE_DATA: begin
            data_cnt <= data_cnt + 3'd1;  // Track processing cycles inside payload
          end
          default: begin
            byte_cnt <= 6'd0;
            data_cnt <= 3'd0;
          end
        endcase
      end
    end
  end  // always_ff

  // ==========================================================================
  // PART 2: Combinational Next-State Logic (10G Compliant)
  // ==========================================================================
  always_comb begin
    next_state = current_state;

    case (current_state)
      ST_IDLE: begin
        if (s_axis.tvalid) begin
          next_state = ST_SKIP_HEADERS;
        end
      end

      ST_SKIP_HEADERS: begin
        // 42 bytes / 8 bytes = 5.25 cycles. 
        // At word count 5 (the 6th cycle), we have seen 48 bytes total.
        // This cycle contains the last 2 bytes of UDP and the first 6 bytes of market data.
        if (s_axis.tvalid && (byte_cnt == 6'd5)) begin
          next_state = ST_PARSE_DATA;
        end
      end

      ST_PARSE_DATA: begin
        // Back to IDLE when MAC IP asserts the last cycle of the current packet
        if (s_axis.tvalid && s_axis.tlast) begin
          next_state = ST_IDLE;
        end
      end

      default: next_state = ST_IDLE;
    endcase
  end  // always_comb

  // ==========================================================================
  // PART 3: Big-Endian Byte Slicing & FIFO Packet Packing
  // ==========================================================================
  always_ff @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
      msg_type_reg     <= 8'h0;
      price_reg        <= 32'h0;
      volume_reg       <= 32'h0;
      out_parsed_event <= '0;
      out_event_valid  <= 1'b0;
    end else begin
      out_event_valid <= 1'b0;  // Automated default assignment to prevent latches

      if (current_state == ST_PARSE_DATA && s_axis.tvalid) begin
        case (data_cnt)
          3'd0: begin
            // Latch message type from tdata[47:40]
            msg_type_reg <= s_axis.tdata[47:40];

            // Reassemble Big-Endian Price to Local Little-Endian format
            price_reg <= {
              s_axis.tdata[15:8], s_axis.tdata[23:16], s_axis.tdata[31:24], s_axis.tdata[39:32]
            };

            // Latch the first byte of volume (MSB from network perspective)
            volume_reg[31:24] <= s_axis.tdata[7:0];
          end

          3'd1: begin
            // Complete atomic assignment of the packed structure inside a single cycle
            out_parsed_event.msg_type <= msg_type_reg;
            out_parsed_event.price <= price_reg;

            // Extract remaining 3 bytes from network stream and join with buffered byte
            out_parsed_event.volume <= {
              s_axis.tdata[47:40], s_axis.tdata[55:48], s_axis.tdata[63:56], volume_reg[31:24]
            };

            // Commit transaction to CDC FIFO
            out_event_valid <= 1'b1;
          end

          default: ;  // Discard any subsequent payload padding
        endcase
      end
    end
  end  // always_ff

endmodule

// // Sayaç ve Durum Tanımlamaları
// typedef enum logic [1:0] {
//   ST_IDLE         = 2'b00,
//   ST_SKIP_HEADERS = 2'b01,
//   ST_PARSE_DATA   = 2'b10
// } state_t;

// state_t current_state, next_state;
// logic [ 5:0] byte_cnt;  // Başlıkları saymak için sayaç (Maks 42 byte sayacak)
// logic [ 2:0] data_cnt;  // 8 byte payload'u kendi içinde saymak için sayaç

// // Geçici veri biriktirme yazmaçları (Shift Register)
// logic [31:0] price_reg;
// logic [31:0] volume_reg;

// // AXI-Stream Ready sinyalini sürekli yukarıda tutuyoruz (Hat hızında işleme)
// assign s_axis_tready = 1'b1;

// // 1. KISIM: Durum Geçişleri (Sequential State Transition)
// always_ff @(posedge clk or negedge reset_n) begin
//   if (!reset_n) begin
//     current_state <= ST_IDLE;
//     byte_cnt      <= 6'd0;
//     data_cnt      <= 3'd0;
//   end else begin
//     current_state <= next_state;

//     // Header byte sayacı kontrolü
//     if (current_state == ST_SKIP_HEADERS && s_axis_tvalid) begin
//       byte_cnt <= byte_cnt + 1'b1;
//     end else if (current_state == ST_IDLE) begin
//       byte_cnt <= 6'd0;
//     end

//     // Payload data byte sayacı kontrolü
//     if (current_state == ST_PARSE_DATA && s_axis_tvalid) begin
//       data_cnt <= data_cnt + 1'b1;
//     end else if (current_state == ST_IDLE) begin
//       data_cnt <= 3'd0;
//     end
//   end
// end

// // 2. KISIM: Sonraki Durum Mantığı (Combinational Next State Logic)
// always_comb begin
//   next_state = current_state;
//   case (current_state)
//     ST_IDLE: begin
//       // Geçerli bir paket akışı başladığı an başlıkları atlamaya geç
//       if (s_axis_tvalid) next_state = ST_SKIP_HEADERS;
//     end

//     ST_SKIP_HEADERS: begin
//       // Toplam başlık boyutu: 14 (MAC) + 20 (IP) + 8 (UDP) = 42 Byte.
//       // 41. byte'ı gördüğümüzde bir sonraki vuruşta payload başlayacak demektir.
//       if (s_axis_tvalid && byte_cnt == 6'd41) next_state = ST_PARSE_DATA;
//     end

//     ST_PARSE_DATA: begin
//       // 4 byte fiyat + 4 byte hacim = 8 Byte veri okunduğunda veya paket bittiğinde çık
//       if (s_axis_tvalid && (data_cnt == 3'd7 || s_axis_tlast)) next_state = ST_IDLE;
//     end

//     default: next_state = ST_IDLE;
//   endcase
// end

// // 3. KISIM: Veri Yakalama ve Bit-Slicing Mantığı
// always_ff @(posedge clk or negedge reset_n) begin
//   if (!reset_n) begin
//     price_reg         <= 32'h0;
//     volume_reg        <= 32'h0;
//     parser_price      <= 32'h0;
//     parser_volume     <= 32'h0;
//     parser_data_valid <= 1'b0;
//   end else begin
//     parser_data_valid <= 1'b0;  // Default olarak pasif

//     if (current_state == ST_PARSE_DATA && s_axis_tvalid) begin
//       // Ağdan gelen büyük-endian (Big-Endian) veriyi shift ederek dolduruyoruz
//       case (data_cnt)
//         // İlk 4 byte: Fiyat (Price)
//         3'd0: price_reg[31:24] <= s_axis_tdata;
//         3'd1: price_reg[23:16] <= s_axis_tdata;
//         3'd2: price_reg[15:8] <= s_axis_tdata;
//         3'd3: price_reg[7:0] <= s_axis_tdata;

//         // Sonraki 4 byte: Hacim (Volume)
//         3'd4: volume_reg[31:24] <= s_axis_tdata;
//         3'd5: volume_reg[23:16] <= s_axis_tdata;
//         3'd6: volume_reg[15:8] <= s_axis_tdata;
//         3'd7: begin
//           volume_reg[7:0]   <= s_axis_tdata;

//           // Paket başarıyla tamamlandı, çıkışları güncelle ve register bankasını tetikle
//           parser_price      <= price_reg;
//           parser_volume     <= {volume_reg[31:8], s_axis_tdata};  // Son byte'ı anlık yakala
//           parser_data_valid <= 1'b1;  // Register bankasına "YAZ" emri
//         end
//         default: begin
//         end
//       endcase
//     end
//   end
// end

// endmodule
