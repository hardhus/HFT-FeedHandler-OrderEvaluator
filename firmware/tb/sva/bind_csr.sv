// Binds the protocol checks into the CSR blocks (simulation only, listed in sva_csr.f).

// AXI4-Lite rules on hft_csr_regs' port (the register blocks behind it have passthrough ports: route_props).
bind hft_csr_regs axil_props #(
    .ADDR_W(16),
    .DATA_W(32)
) u_axil_props (
    .clk    (clk),
    .rst    (rst),
    .awvalid(s_axil_awvalid),
    .awready(s_axil_awready),
    .awaddr (s_axil_awaddr),
    .awprot (s_axil_awprot),
    .wvalid (s_axil_wvalid),
    .wready (s_axil_wready),
    .wdata  (s_axil_wdata),
    .wstrb  (s_axil_wstrb),
    .bvalid (s_axil_bvalid),
    .bready (s_axil_bready),
    .bresp  (s_axil_bresp),
    .arvalid(s_axil_arvalid),
    .arready(s_axil_arready),
    .araddr (s_axil_araddr),
    .arprot (s_axil_arprot),
    .rvalid (s_axil_rvalid),
    .rready (s_axil_rready),
    .rdata  (s_axil_rdata),
    .rresp  (s_axil_rresp)
);

// Generated assertions (regblock) stay off until the block's first reset is released (assert_arm).
bind hft_csr assert_arm u_assert_arm (
    .clk(clk),
    .rst(rst)
);

// Generated assertions (regblock) stay off until the block's first reset is released (assert_arm).
bind hft_order_csr assert_arm u_assert_arm (
    .clk(clk),
    .rst(rst)
);

// Bridge rules (decision 41, 43, A40): each request to the block of its address, never to both, a switch only after
// every response, responses only from the block in flight, a stalled request held unchanged, and hft_csr's external
// order blocks never addressed.
bind hft_csr_regs route_props #(
    .ADDR_W  (16),
    .DATA_W  (32),
    .N_WIN   (hft_pkg::CSR_ORDER_N_WIN),
    .WIN_BASE(hft_pkg::CSR_ORDER_WIN_BASE),
    .WIN_MASK(hft_pkg::CSR_ORDER_WIN_MASK)
) u_route_props (
    .clk        (clk),
    .rst        (rst),
    .s_bvalid   (s_axil_bvalid),
    .s_bready   (s_axil_bready),
    .s_rvalid   (s_axil_rvalid),
    .s_rready   (s_axil_rready),
    .m_req      (req),
    .m_req_is_wr(req_is_wr),
    .m_addr     (addr),
    .m_wr_data  (wr_data),
    .m_wr_biten (wr_biten),
    .m_stall_wr (stall_wr),
    .m_stall_rd (stall_rd),
    .m_rd_ack   (rd_ack),
    .m_wr_ack   (wr_ack),
    .m0_win_req (hwif_out.RISK.req || hwif_out.STRATEGY.req || hwif_out.TX.req)
);

// Mailbox output (decision 12, audit/15): an order stays unchanged until risk_check takes it, and the register is
// empty for one cycle after each take. W = side 8 + qty 32 + price 32 + locate 16 + stock 64 + tif 8 + ClOrdID 112.
bind hft_csr_wrap mbox_props #(
    .W(272)
) u_mbox_props (
    .clk(net_clk),
    .rst(net_rst),
    .valid(m_host_valid),
    .ready(m_host_ready),
    .data({
      m_host_side, m_host_qty, m_host_price, m_host_locate, m_host_stock, m_host_tif, m_host_clordid
    })
);
