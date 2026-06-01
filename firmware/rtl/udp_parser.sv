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
    input  logic clk,
    input  logic rst_n,
    axis_if.slave s_axis,
    output hft_internal_event_t out_parsed_event,
    output logic                out_event_valid
);

  assign s_axis.tready = 1'b1;

  // --- Internal Processing Counters & Storage ---
  logic [5:0] word_cnt;  
  logic [ 7:0] msg_type_reg;  
  logic [31:0] price_reg;  

  // ==========================================================================
  // TEK BLOK: AXI-Stream Word Counter & Zero-Delay Data Extraction
  // ==========================================================================
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      word_cnt         <= 6'd0;
      msg_type_reg     <= 8'h0;
      price_reg        <= 32'h0;
      out_parsed_event <= '0;
      out_event_valid  <= 1'b0;
    end else begin
      out_event_valid <= 1'b0; 

      if (s_axis.tvalid) begin
        
        // TLAST ile otomatik sıfırlama
        if (s_axis.tlast) begin
          word_cnt <= 6'd0; 
        end else begin
          word_cnt <= word_cnt + 6'd1;
        end

        // Word Index'e Göre Veri Ayıklama (GÜNCELLENDİ)
        case (word_cnt)
          6'd5: begin   // Eskiden 4'tü. Şimdi 5. kelimeyi (asıl verinin başladığı yeri) okuyoruz.
            msg_type_reg <= s_axis.tdata[47:40];
            price_reg    <= {
              s_axis.tdata[15:8], s_axis.tdata[23:16], s_axis.tdata[31:24], s_axis.tdata[39:32]
            };
          end

          6'd6: begin   // Eskiden 5'ti. Şimdi 6. kelimeden Volume çekiyoruz.
            out_parsed_event.msg_type <= msg_type_reg;
            out_parsed_event.price    <= price_reg;
            out_parsed_event.volume   <= {
              s_axis.tdata[7:0], s_axis.tdata[63:56], s_axis.tdata[55:48], s_axis.tdata[47:40]
            };
            out_event_valid <= 1'b1;
          end

          default: ; 
        endcase
      end
    end
  end

endmodule
