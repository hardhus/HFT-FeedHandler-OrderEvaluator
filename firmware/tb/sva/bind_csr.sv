// Binds the protocol checks into the CSR blocks (simulation only, listed in sva_csr.f).

// AXI4-Lite rules on hft_csr_regs' port and on both router outputs (the two register blocks' ports).
bind hft_csr axil_props #(
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

bind hft_order_csr axil_props #(
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

// Router rules (decision 41, A39): each request to the slave of its address, never to both, a switch only after every
// response, responses only from the slave in flight, and hft_csr's external order blocks never addressed.
bind hft_csr_regs route_props #(
    .ADDR_W  (16),
    .N_WIN   (hft_pkg::CSR_ORDER_N_WIN),
    .WIN_BASE(hft_pkg::CSR_ORDER_WIN_BASE),
    .WIN_MASK(hft_pkg::CSR_ORDER_WIN_MASK)
) u_route_props (
    .clk       (clk),
    .rst       (rst),
    .s_bvalid  (s_axil_bvalid),
    .s_bready  (s_axil_bready),
    .s_rvalid  (s_axil_rvalid),
    .s_rready  (s_axil_rready),
    .m_awvalid (awvalid),
    .m_awready (awready),
    .m_awaddr  (awaddr),
    .m_wvalid  (wvalid),
    .m_arvalid (arvalid),
    .m_arready (arready),
    .m_araddr  (araddr),
    .m_bvalid  (bvalid),
    .m_rvalid  (rvalid),
    .m0_win_req(hwif_out.RISK.req || hwif_out.STRATEGY.req || hwif_out.TX.req)
);
