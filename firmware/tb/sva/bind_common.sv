// Binds the protocol checks into the common blocks (simulation only, listed in sva.f).

bind axis_register axis_props #(
    .DATA_W(DATA_W),
    .USER_W(USER_W)
) u_m_axis_props (
    .clk   (clk),
    .rst   (rst),
    .tvalid(m_axis_tvalid),
    .tready(m_axis_tready),
    .tlast (m_axis_tlast),
    .tdata (m_axis_tdata),
    .tkeep (m_axis_tkeep),
    .tuser (m_axis_tuser)
);

bind axis_skid_buffer axis_props #(
    .DATA_W(DATA_W),
    .USER_W(USER_W)
) u_m_axis_props (
    .clk   (clk),
    .rst   (rst),
    .tvalid(m_axis_tvalid),
    .tready(m_axis_tready),
    .tlast (m_axis_tlast),
    .tdata (m_axis_tdata),
    .tkeep (m_axis_tkeep),
    .tuser (m_axis_tuser)
);

bind cdc_async_fifo fifo_props #(
    .WIDTH(WIDTH)
) u_fifo_props (
    .rst        (rst),
    .wr_clk     (wr_clk),
    .wr_en      (wr_en),
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
