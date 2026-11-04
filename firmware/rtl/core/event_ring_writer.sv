// event_ring_writer: writes committed entries from the commit FIFO into the host RAM ring, one 128-byte AXI4
// burst each, in order, stamping ring_idx and the two ctrl bytes. Lossless while enabled: a full ring stops it.
// Clock domain : clk (host_clk)
// Latency      : FIFO pop -> AWVALID and WVALID 2 cycles
// Throughput   : one entry every 1024/DATA_W cycles while the ring has room, no gap in W, if the memory has
//                AWREADY and WREADY high and answers within MAX_OUTSTANDING entries' time
// Input        : first-word-fall-through FIFO read side, read only while enabled. The word goes straight
//                into a register (the LUTRAM read path has no slack for logic), where one popped entry may
//                wait for the ring or for a re-enable.
// Ring         : entry prod goes to base + (prod mod N) * 128, ring_idx = prod, phase = ~bit(prod, size_log2).
//                Full: prod - cons == N (32-bit, both wrap). cons must never pass prod.
// Enable       : the host writes cfg_cons = 0 and zeroes the region before each enable (its rule, audit/13 S5).
//                A rising edge of cfg_enable with cfg_cons != 0 is refused: status_enable_err is set and the
//                writer stays stopped, since a stale cons would overwrite unread entries in silence. Disable and
//                enable again to retry. An accepted rising edge sets prod to 0 and takes base and size_log2, which
//                are ignored until the next one, and clears both status bits. Base bits 11:0 are ignored (4 KiB
//                aligned, so no burst crosses 4 KiB). Falling edge: bursts in flight finish, no new entry starts.
// Errors       : a BRESP other than OKAY counts in cnt_bresp_err and sets status_bresp_err; no retry.
// Counters     : CNT_W bits each, wrapping, cleared only by rst. cnt_written + cnt_bresp_err = answered bursts;
//                prod_idx counts bursts started.
// Reset        : rst clears everything; AWVALID and WVALID go low. Reset the memory side with it.
module event_ring_writer
  import event_desc_pkg::*;
#(
    parameter int unsigned DATA_W          = 512,
    parameter int unsigned MAX_OUTSTANDING = 4,
    parameter int unsigned CNT_W           = 64
) (
    input logic clk,
    input logic rst,

    // cdc_async_fifo read side (FWFT)
    output logic              fifo_rd_en,
    input  logic [DESC_W-1:0] fifo_dout,
    input  logic              fifo_empty,
    input  logic              fifo_rd_rst_busy,

    // RING registers
    input  logic        cfg_enable,
    /* verilator lint_off UNUSEDSIGNAL */  // bits 11:0: see Enable
    input  logic [63:0] cfg_base,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic [ 4:0] cfg_size_log2,
    input  logic [31:0] cfg_cons,
    output logic [31:0] prod_idx,
    output logic        status_bresp_err,
    output logic        status_enable_err,

    // AXI4 write master
    output logic [        63:0] m_axi_hmw_awaddr,
    output logic [         7:0] m_axi_hmw_awlen,
    output logic [         2:0] m_axi_hmw_awsize,
    output logic [         1:0] m_axi_hmw_awburst,
    output logic                m_axi_hmw_awvalid,
    input  logic                m_axi_hmw_awready,
    output logic [  DATA_W-1:0] m_axi_hmw_wdata,
    output logic [DATA_W/8-1:0] m_axi_hmw_wstrb,
    output logic                m_axi_hmw_wlast,
    output logic                m_axi_hmw_wvalid,
    input  logic                m_axi_hmw_wready,
    input  logic [         1:0] m_axi_hmw_bresp,
    input  logic                m_axi_hmw_bvalid,
    output logic                m_axi_hmw_bready,

    output logic [CNT_W-1:0] cnt_written,
    output logic [CNT_W-1:0] cnt_bresp_err
);
  localparam int unsigned BEATS = DESC_W / DATA_W;
  localparam int unsigned BEAT_W = $clog2(BEATS);
  localparam int unsigned OUT_W = $clog2(MAX_OUTSTANDING + 1);
  localparam logic [1:0] AXI_BURST_INCR = 2'b01;
  localparam logic [1:0] AXI_RESP_OKAY = 2'b00;
  localparam logic [56:0] ENTRY_STEP = 57'd1;  // address bits 63:7

  assign m_axi_hmw_awlen   = 8'(BEATS - 1);
  assign m_axi_hmw_awsize  = 3'($clog2(DATA_W / 8));
  assign m_axi_hmw_awburst = AXI_BURST_INCR;
  assign m_axi_hmw_wstrb   = '1;
  assign m_axi_hmw_bready  = 1'b1;

  logic enable_q, running_q, run, start, start_ok;
  logic [51:0] base_q;  // address bits 63:12
  logic [31:0] mask_q;  // N - 1
  logic [31:0] cons_q, prod_q, used;
  logic [56:0] addr_q;  // address bits 63:7 of entry prod_q
  logic phase_q, last_slot, full_q;
  logic raw_valid_q, take, w_free, aw_free;
  logic [DESC_W-1:0] raw_q, stamped, shift_q;
  logic [BEAT_W-1:0] beat_q;
  logic [ OUT_W-1:0] out_q;
  logic b_fire, b_ok;

  assign start = cfg_enable && !enable_q;
  assign start_ok = start && cfg_cons == '0;
  assign run = cfg_enable && running_q;
  assign used = prod_q - cons_q;
  assign last_slot = (prod_q & mask_q) == mask_q;
  assign prod_idx = prod_q;

  // A new burst needs both channels free: AW and W start together and then go on their own.
  assign w_free = !m_axi_hmw_wvalid || (m_axi_hmw_wready && m_axi_hmw_wlast);
  assign aw_free = !m_axi_hmw_awvalid || m_axi_hmw_awready;
  assign take       = run && raw_valid_q && !full_q && w_free && aw_free && out_q != OUT_W'(MAX_OUTSTANDING);
  assign fifo_rd_en = run && !full_q && !fifo_empty && !fifo_rd_rst_busy && (!raw_valid_q || take);

  always_comb begin
    stamped                                            = raw_q;
    stamped[8*DESC_RING_IDX_OFS+:8*DESC_RING_IDX_SIZE] = prod_q;
    stamped[8*DESC_CTRL0_OFS+:8]                       = {phase_q, 3'b000, DESC_VERSION};
    stamped[8*DESC_CTRL1_OFS+:8]                       = {phase_q, 3'b000, DESC_VERSION};
  end

  assign m_axi_hmw_wdata = shift_q[DATA_W-1:0];
  assign b_fire          = m_axi_hmw_bvalid && m_axi_hmw_bready;
  assign b_ok            = m_axi_hmw_bresp == AXI_RESP_OKAY;

  always_ff @(posedge clk) begin
    if (fifo_rd_en) begin
      raw_q <= fifo_dout;
    end
    if (take) begin
      shift_q          <= stamped;
      beat_q           <= '0;
      m_axi_hmw_awaddr <= {addr_q, 7'd0};
    end else if (m_axi_hmw_wvalid && m_axi_hmw_wready) begin
      shift_q <= shift_q >> DATA_W;
      beat_q  <= beat_q + BEAT_W'(1);
    end
    cons_q <= cfg_cons;

    if (rst) begin
      enable_q          <= 1'b0;
      running_q         <= 1'b0;
      status_enable_err <= 1'b0;
      base_q            <= '0;
      mask_q            <= '0;
      prod_q            <= '0;
      addr_q            <= '0;
      phase_q           <= 1'b1;
      full_q            <= 1'b0;
      raw_valid_q       <= 1'b0;
      out_q             <= '0;
      m_axi_hmw_awvalid <= 1'b0;
      m_axi_hmw_wvalid  <= 1'b0;
      m_axi_hmw_wlast   <= 1'b0;
      status_bresp_err  <= 1'b0;
      cnt_written       <= '0;
      cnt_bresp_err     <= '0;
    end else begin
      enable_q  <= cfg_enable;
      running_q <= start ? start_ok : running_q && cfg_enable;
      if (start) begin
        status_enable_err <= !start_ok;
      end
      if (start_ok) begin
        base_q  <= cfg_base[63:12];
        mask_q  <= (32'd1 << cfg_size_log2) - 32'd1;
        prod_q  <= '0;
        addr_q  <= {cfg_base[63:12], 5'd0};
        phase_q <= 1'b1;
        full_q  <= 1'b0;
      end else begin
        // used is registered on both sides: only the take of this cycle has to be added.
        full_q <= take ? used == mask_q : used == mask_q + 32'd1;
        if (take) begin
          prod_q  <= prod_q + 32'd1;
          addr_q  <= last_slot ? {base_q, 5'd0} : addr_q + ENTRY_STEP;
          phase_q <= phase_q ^ last_slot;
        end
      end

      if (fifo_rd_en) begin
        raw_valid_q <= 1'b1;
      end else if (take) begin
        raw_valid_q <= 1'b0;
      end

      out_q <= out_q + OUT_W'(take) - OUT_W'(b_fire);

      if (take) begin
        m_axi_hmw_awvalid <= 1'b1;
      end else if (m_axi_hmw_awready) begin
        m_axi_hmw_awvalid <= 1'b0;
      end

      if (take) begin
        m_axi_hmw_wvalid <= 1'b1;
        m_axi_hmw_wlast  <= 1'b0;
      end else if (m_axi_hmw_wvalid && m_axi_hmw_wready) begin
        m_axi_hmw_wvalid <= !m_axi_hmw_wlast;
        m_axi_hmw_wlast  <= beat_q == BEAT_W'(BEATS - 2);
      end

      if (b_fire) begin
        if (b_ok) begin
          cnt_written <= cnt_written + CNT_W'(1);
        end else begin
          cnt_bresp_err <= cnt_bresp_err + CNT_W'(1);
        end
      end
      if (start_ok) begin
        status_bresp_err <= 1'b0;
      end else if (b_fire && !b_ok) begin
        status_bresp_err <= 1'b1;
      end
    end
  end
endmodule
