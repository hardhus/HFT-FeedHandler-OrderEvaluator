// Binds the protocol checks into the CSR blocks (simulation only, listed in sva_csr.f).

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
