// cdc_async_fifo: asynchronous FIFO with gray-code pointers, FWFT, XPM_FIFO_ASYNC-compatible ports.
// Clock domain : wr_clk (rst, wr_*, full, overflow, wr_rst_busy)
//                rd_clk (rd_*, dout, empty, underflow, rd_rst_busy)
// Latency      : write -> !empty within SYNC_STAGES+3 rd cycles
// Throughput   : 1 word/cycle on each side
// Reset        : rst is synchronous to wr_clk. After power-up hold it for at least
//                2*SYNC_STAGES+2 cycles of the slower clock; later a 1-cycle pulse is enough.
//                While wr_rst_busy / rd_rst_busy are high, writes / reads are ignored and
//                pulse overflow / underflow (audit 08 §2.2).
// Note         : FWFT: dout is valid whenever empty is low; rd_en pops it. Memory is an
//                async-read array (LUTRAM). IMPL="xpm" selects cdc_async_fifo_xpm (Vivado only).
module cdc_async_fifo #(
    parameter int unsigned WIDTH       = 64,
    parameter int unsigned DEPTH_LOG2  = 4,
    parameter int unsigned SYNC_STAGES = 2,
    parameter string       IMPL        = "portable"
) (
    input  logic             rst,
    input  logic             wr_clk,
    input  logic             wr_en,
    input  logic [WIDTH-1:0] din,
    output logic             full,
    output logic             overflow,
    output logic             wr_rst_busy,

    input  logic             rd_clk,
    input  logic             rd_en,
    output logic [WIDTH-1:0] dout,
    output logic             empty,
    output logic             underflow,
    output logic             rd_rst_busy
);
  if (DEPTH_LOG2 < 2 || SYNC_STAGES < 2) begin : g_check
    $error("cdc_async_fifo: DEPTH_LOG2 and SYNC_STAGES must be >= 2");
  end

  if (IMPL == "xpm") begin : g_xpm
`ifdef HFT_HAVE_XPM
    cdc_async_fifo_xpm #(
        .WIDTH      (WIDTH),
        .DEPTH_LOG2 (DEPTH_LOG2),
        .SYNC_STAGES(SYNC_STAGES)
    ) u_xpm (
        .rst        (rst),
        .wr_clk     (wr_clk),
        .wr_en      (wr_en),
        .din        (din),
        .full       (full),
        .overflow   (overflow),
        .wr_rst_busy(wr_rst_busy),
        .rd_clk     (rd_clk),
        .rd_en      (rd_en),
        .dout       (dout),
        .empty      (empty),
        .underflow  (underflow),
        .rd_rst_busy(rd_rst_busy)
    );
`else
    $error("cdc_async_fifo: IMPL=\"xpm\" needs HFT_HAVE_XPM (Vivado flow)");
`endif  // HFT_HAVE_XPM
  end else begin : g_portable
    localparam int unsigned N = DEPTH_LOG2;

    // Each side's gray pointer feeds the other side's synchronizer.
    logic [N:0] wbin_q, wgray_q, wbin_next, wgray_next, rgray_s;
    logic [N:0] rbin_q, rgray_q, rbin_next, rgray_next, wgray_s;
    logic wr_fire, rd_fire;
    logic rst_req_q, ack_s, req_s, ack_q;
    // LUTRAM on purpose: async read for FWFT; also keeps a consumer's output register from
    // pulling the array into a BRAM.
    (* ram_style = "distributed", hft_cdc_data = "TRUE" *) logic [WIDTH-1:0] mem[2**N];

    // ---- reset handshake: wr side requests, rd side resets and acknowledges ----
    always_ff @(posedge wr_clk) begin
      if (rst) begin
        rst_req_q <= 1'b1;
      end else if (ack_s) begin
        rst_req_q <= 1'b0;
      end
    end

    sync_bit #(
        .STAGES(SYNC_STAGES)
    ) u_req_sync (
        .clk(rd_clk),
        .d  (rst_req_q),
        .q  (req_s)
    );

    always_ff @(posedge rd_clk) begin
      ack_q <= req_s;
    end

    sync_bit #(
        .STAGES(SYNC_STAGES)
    ) u_ack_sync (
        .clk(wr_clk),
        .d  (ack_q),
        .q  (ack_s)
    );

    assign wr_rst_busy = rst_req_q || ack_s;
    assign rd_rst_busy = req_s || ack_q;

    // ---- write side ----
    assign wr_fire     = wr_en && !full && !wr_rst_busy && !rst;
    assign wbin_next   = wbin_q + (N + 1)'(wr_fire);
    assign wgray_next  = wbin_next ^ (wbin_next >> 1);

    always_ff @(posedge wr_clk) begin
      if (rst_req_q) begin
        wbin_q  <= '0;
        wgray_q <= '0;
        full    <= 1'b1;  // like XPM FULL_RESET_VALUE=1: no writes during reset
      end else begin
        wbin_q  <= wbin_next;
        wgray_q <= wgray_next;
        full    <= wgray_next == {~rgray_s[N:N-1], rgray_s[N-2:0]};
      end
    end

    // Rejected write (full, reset or busy): one overflow pulse, also during reset.
    always_ff @(posedge wr_clk) begin
      overflow <= wr_en && (full || wr_rst_busy || rst);
    end

    always_ff @(posedge wr_clk) begin
      if (wr_fire) begin
        mem[wbin_q[N-1:0]] <= din;
      end
    end

    sync_bit #(
        .WIDTH (N + 1),
        .STAGES(SYNC_STAGES)
    ) u_rptr_sync (
        .clk(wr_clk),
        .d  (rgray_q),
        .q  (rgray_s)
    );

    // ---- read side (FWFT) ----
    assign rd_fire    = rd_en && !empty && !rd_rst_busy;
    assign rbin_next  = rbin_q + (N + 1)'(rd_fire);
    assign rgray_next = rbin_next ^ (rbin_next >> 1);

    always_ff @(posedge rd_clk) begin
      if (req_s) begin
        rbin_q  <= '0;
        rgray_q <= '0;
        empty   <= 1'b1;
      end else begin
        rbin_q  <= rbin_next;
        rgray_q <= rgray_next;
        empty   <= rgray_next == wgray_s;
      end
    end

    // Rejected read (empty or busy): one underflow pulse, also during reset.
    always_ff @(posedge rd_clk) begin
      underflow <= rd_en && (empty || rd_rst_busy);
    end

    sync_bit #(
        .WIDTH (N + 1),
        .STAGES(SYNC_STAGES)
    ) u_wptr_sync (
        .clk(rd_clk),
        .d  (wgray_q),
        .q  (wgray_s)
    );

    assign dout = mem[rbin_q[N-1:0]];  // async read; stable while !empty and not popped
  end
endmodule
