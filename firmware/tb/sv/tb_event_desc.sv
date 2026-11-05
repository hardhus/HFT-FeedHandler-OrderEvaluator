// tb_event_desc: checks the generated pack and stamp functions of event_desc_pkg against one entry of
// each kind, written by hand in memory order (firmware/tb/selftest/desc_hand.py holds the same bytes
// for the Python model). Independent of event_desc.toml and scripts/gen_desc.py.
`timescale 1ns / 1ps
module tb_event_desc;
  import event_desc_pkg::*;

  localparam logic [63:0] SEQ = 64'h0102_0304_0506_0708;
  localparam logic [63:0] SOF_TS = 64'h1112_1314_1516_1718;
  localparam logic [31:0] RING_IDX = 32'h2122_2324;
  // The first 16 bytes of every entry: seq, sof_ts.
  localparam string HEAD = "08070605040302011817161514131211";
  localparam string ZEROS16 = "00000000000000000000000000000000";

  int errors = 0;

  // hex: the entry's digits, byte 0 first. Byte i of an entry is e[8i+7:8i].
  task automatic check(input string name, input logic [DESC_W-1:0] got, input string hex);
    logic [7:0] want;
    if (hex.len() != 2 * DESC_BYTES) begin
      $display("%s: the expected entry has %0d hex digits", name, hex.len());
      errors++;
      return;
    end
    for (int unsigned i = 0; i < DESC_BYTES; i++) begin
      want = 8'(hex.substr(2 * i, 2 * i + 1).atohex());
      if (got[8*i+:8] !== want) begin
        $display("%s: byte %0d is 0x%h, want 0x%h", name, i, got[8*i+:8], want);
        errors++;
      end
    end
  endtask

  initial begin
    string              hex;
    logic  [DESC_W-1:0] e;
    logic  [     511:0] raw;

    hex = {
      HEAD,
      "2423222101015A01502C323134334645",  // ring_idx, kind, flags, epoch, class, type .. ts
      "4443424142594F005453525141424344",  // ts, side, printable, cross, pad, price, attribution
      "68676665646362610000000000000081",  // order_ref, pad, ctrl
      "78777675747372718887868584838281",  // new_ref, shares
      "98979695949392914141504C20202020",  // match, stock
      ZEROS16,
      "00000000000000000000000000000081"
    };
    e = desc_pack_itch(
        .seq(SEQ),
        .sof_ts(SOF_TS),
        .flags(8'h01),
        .cfg_epoch(8'h5A),
        .itch_class(8'h01),
        .msg_type(8'h50),
        .len(8'd44),
        .locate(16'h3132),
        .tracking(16'h3334),
        .itch_ts(48'h4142_4344_4546),
        .side(8'h42),
        .printable(8'h59),
        .cross_type(8'h4F),
        .price(32'h5152_5354),
        .attribution(32'h4443_4241),
        .order_ref(64'h6162_6364_6566_6768),
        .new_ref(64'h7172_7374_7576_7778),
        .shares(64'h8182_8384_8586_8788),
        .match(64'h9192_9394_9596_9798),
        .stock(64'h2020_2020_4C50_4141)
    );
    check("ITCH", desc_stamp(e, RING_IDX, 1'b1), hex);

    for (int unsigned i = 0; i < 64; i++) begin
      raw[8*i+:8] = 8'(i);
    end
    hex = {
      HEAD,
      "242322210200A5044000010203040506",  // ring_idx, kind, flags, epoch, class, len, message
      "0708090A0B0C0D0E0F10111213141516",  // message bytes
      "1718191A1B1C1D1E1F20212223242581",  // message bytes up to 37, ctrl
      "262728292A2B2C2D2E2F303132333435",  // message bytes from 38
      "363738393A3B3C3D3E3F000000000000",  // up to 63, pad
      ZEROS16,
      "00000000000000000000000000000081"
    };
    e = desc_pack_itch_raw(
        .seq(SEQ),
        .sof_ts(SOF_TS),
        .flags(8'h00),
        .cfg_epoch(8'hA5),
        .itch_class(8'h04),
        .len(8'd64),
        .raw_lo(raw[303:0]),
        .raw_hi(raw[511:304])
    );
    check("ITCH_RAW", desc_stamp(e, RING_IDX, 1'b1), hex);

    hex = {
      HEAD,
      "2423222103000700A8A7A6A5A4A3A2A1",  // ring_idx, kind, flags, epoch, pad, first_seq
      "B8B7B6B5B4B3B2B10000000000000000",  // gap, pad
      "00000000000000000000000000000001",  // pad, ctrl with phase 0
      ZEROS16,
      ZEROS16,
      ZEROS16,
      "00000000000000000000000000000001"
    };
    e = desc_pack_gap(
        .seq(SEQ),
        .sof_ts(SOF_TS),
        .flags(8'h00),
        .cfg_epoch(8'h07),
        .first_seq(64'hA1A2_A3A4_A5A6_A7A8),
        .gap(64'hB1B2_B3B4_B5B6_B7B8)
    );
    check("GAP", desc_stamp(e, RING_IDX, 1'b0), hex);

    hex = {
      HEAD,
      "242322210401FF000000000000000000",  // ring_idx, kind, flags, epoch, pad
      ZEROS16,
      "00000000000000000000000000000081",
      ZEROS16,
      ZEROS16,
      ZEROS16,
      "00000000000000000000000000000081"
    };
    e = desc_pack_eos(.seq(SEQ), .sof_ts(SOF_TS), .flags(8'h01), .cfg_epoch(8'hFF));
    check("EOS", desc_stamp(e, RING_IDX, 1'b1), hex);

    hex = {
      HEAD,
      "2423222105003C003433323101074245",  // ring_idx, kind, flags, epoch, pad, user_ref, source .. tif
      "42410000545352516463626100000000",  // locate, pad, qty, price, pad
      "4D534654202020200000000000000081",  // stock "MSFT    ", pad, ctrl
      "78777675747372714857373331333233",  // tx_ts, clordid "HW731323"
      "33333420202000000000000000000000",  // clordid "334   ", pad
      ZEROS16,
      "00000000000000000000000000000081"
    };
    e = desc_pack_order_sent(
        .seq(SEQ),
        .sof_ts(SOF_TS),
        .flags(8'h00),
        .cfg_epoch(8'h3C),
        .user_ref(32'h3132_3334),
        .source(8'd1),
        .rule(8'd7),
        .side(8'h42),
        .tif(8'h45),
        .locate(16'h4142),
        .qty(32'h5152_5354),
        .price(32'h6162_6364),
        .stock(64'h2020_2020_5446_534D),
        .tx_ts(64'h7172_7374_7576_7778),
        .clordid(112'h2020_2034_3333_3332_3331_3337_5748)
    );
    check("ORDER_SENT", desc_stamp(e, RING_IDX, 1'b1), hex);

    hex = {
      HEAD,
      "242322210601C3000900000002005333",  // ring_idx, kind, flags, epoch, pad, reason, pad, source .. tif
      "FEFF00003F420F009C93357700000000",  // locate, pad, qty, price, pad
      "41422020202020200000000000000001",  // stock "AB      ", pad, ctrl with phase 0
      "888786858483828141424378797A2030",  // reject_ts, clordid "ABCxyz 0"
      "31323320202000000000000000000000",  // clordid "123   ", pad
      ZEROS16,
      "00000000000000000000000000000001"
    };
    e = desc_pack_risk_reject(
        .seq(SEQ),
        .sof_ts(SOF_TS),
        .flags(8'h01),
        .cfg_epoch(8'hC3),
        .reason(8'd9),
        .source(8'd2),
        .rule(8'd0),
        .side(8'h53),
        .tif(8'h33),
        .locate(16'hFFFE),
        .qty(32'd999_999),
        .price(32'h7735_939C),
        .stock(64'h2020_2020_2020_4241),
        .reject_ts(64'h8182_8384_8586_8788),
        .clordid(112'h2020_2033_3231_3020_7A79_7843_4241)
    );
    check("RISK_REJECT", desc_stamp(e, RING_IDX, 1'b0), hex);

    if (errors == 0) begin
      $display("HFT_TB_RESULT: PASS");
    end else begin
      $display("HFT_TB_RESULT: FAIL %0d errors", errors);
    end
    $finish;
  end
endmodule
