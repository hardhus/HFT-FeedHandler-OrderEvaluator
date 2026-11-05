# Order message (F7)

The order path sends each order as one Ethernet frame: a host-written Ethernet/IPv4/UDP header, a SoupBinTCP
Unsequenced Data envelope, and one Nasdaq OUCH 5.0 Enter Order message, field for field, with no appendage. The
frame is the real OUCH client message carried over UDP instead of a SoupBinTCP TCP session (the session, login,
heartbeats and exchange responses are out of scope). Every frame is exactly **92 bytes**.

Sources (the tables below are transcribed from them):

- Nasdaq, *OUCH 5.0 Order Entry Specification*, updated October 2025, revision 1.05 (10/7/2025): section 1.2 "Data
  Types" and section 2.1 "Type O – Enter Order".
  <https://www.nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/OUCH5.0.pdf>
- Nasdaq, *SoupBinTCP*, version 3.00 (revision 02/22/2017): section 1.1 "SoupBinTCP Logical Packets" and section
  2.3.2 "Unsequenced Data Packets".
  <https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/soupbintcp.pdf>

## Data types (OUCH 5.0 section 1.2, SoupBinTCP section 1.5)

- Numeric fields are binary, **big-endian**, unsigned unless stated: Long 8 B, Integer 4 B, Short 2 B, Byte 1 B.
- Alpha fields are left-justified and padded on the right with spaces.
- **Price** is an 8-byte numeric with **4 implied decimal places**, unsigned. The largest limit price is
  $199,999.9900 (`0x7735939C`). The value `0x7FFFFFFF` ($214,748.3647) marks a market order for a cross, and a price
  of $200,000.00 or more is also treated as a market order.
- **UserRefNum** is an unsigned 4-byte number. Per OUCH port, it must be unique and strictly increasing for the
  trading day. It starts at 1, and the exchange ignores new orders whose UserRefNum is not larger than the last one
  processed (it reads them as retransmissions).
- **ClOrdID** is alphanumeric and non-binary: letters, digits and spaces.

## Frame layout

| Frame bytes | Length | Part | Written by |
|-------------|--------|------|------------|
| 0–13 | 14 | Ethernet II header (destination MAC, source MAC, EtherType `0x0800`) | host template |
| 14–33 | 20 | IPv4 header, no options | host template |
| 34–41 | 8 | UDP header | host template |
| 42–44 | 3 | SoupBinTCP Unsequenced Data envelope | core |
| 45–91 | 47 | OUCH 5.0 Enter Order, fixed part | core |

The frame is 92 B, above the 60-byte Ethernet minimum (without FCS), so it is never padded. The MAC appends the FCS.

### Ethernet, IPv4 and UDP header (host template)

The host writes all 42 header bytes into the TX template. The core sends them unchanged and computes nothing in
them, so the host fills in every field, including the lengths and the IPv4 checksum:

| Field | Value |
|-------|-------|
| IPv4 version and IHL | `0x45` |
| IPv4 total length | 78 (`0x004E`): 20 + 8 + 3 + 47 |
| IPv4 Identification | 0 |
| IPv4 flags and fragment offset | `0x4000` (DF set, offset 0) |
| IPv4 protocol | 17 (UDP) |
| IPv4 header checksum | computed by the host. All header fields are fixed, so the checksum is fixed too |
| UDP length | 58 (`0x003A`): 8 + 3 + 47 |
| UDP checksum | 0 (no checksum; allowed for UDP over IPv4) |

The MAC addresses, the DSCP/ECN byte, the TTL, the IP addresses and the UDP ports are the host's choice.

### SoupBinTCP Unsequenced Data envelope (SoupBinTCP section 2.3.2)

| Name | Offset | Frame byte | Length | Value | Notes |
|------|--------|------------|--------|-------|-------|
| Packet Length | 0 | 42 | 2 | 48 (`0x0030`) | Big-endian. The length of the rest of the packet: the packet type byte plus the message (1 + 47) |
| Packet Type | 2 | 44 | 1 | `'U'` | Unsequenced Data Packet |
| Message | 3 | 45 | 47 | | The OUCH Enter Order message below |

### OUCH 5.0 Enter Order (OUCH 5.0 section 2.1)

| Name | Offset | Frame byte | Length | Type | Values sent by the core |
|------|--------|------------|--------|------|-------------------------|
| Type | 0 | 45 | 1 | `'O'` | `'O'` |
| UserRefNum | 1 | 46 | 4 | UserRefNum | The TX counter (see below) |
| Side | 5 | 50 | 1 | Alpha | `'B'` buy or `'S'` sell. The spec also defines `'T'` (sell short) and `'E'` (sell short exempt); the core never sends them |
| Quantity | 6 | 51 | 4 | Integer | Total shares. The spec requires 0 < Quantity < 1,000,000 |
| Symbol | 10 | 55 | 8 | Alpha | Stock symbol, left-justified, space-padded (the same bytes as the ITCH `Stock` field) |
| Price | 18 | 63 | 8 | Price | 4 implied decimals; 1 to `0x7735939C` |
| Time In Force | 26 | 71 | 1 | Alpha | `'0'` Day, `'3'` IOC, `'5'` GTX, `'E'` After hours. The spec also defines `'6'` GTT, which needs the ExpireTime appendage; the core never sends it |
| Display | 27 | 72 | 1 | Alpha | From the TX configuration. Spec values: `'Y'` visible, `'N'` hidden, `'A'` attributable |
| Capacity | 28 | 73 | 1 | Alpha | From the TX configuration. Spec values: `'A'` agency, `'P'` principal, `'R'` riskless, `'O'` other |
| InterMarket Sweep Eligibility | 29 | 74 | 1 | Alpha | From the TX configuration. Spec values: `'Y'` eligible, `'N'` not eligible |
| CrossType | 30 | 75 | 1 | Alpha | From the TX configuration. Spec values: `'N'` continuous market, `'O'` opening cross, `'C'` closing cross, `'H'` halt/IPO, `'S'` supplemental, `'R'` retail (BX only), `'E'` extended life, `'A'` after hours close |
| ClOrdID | 31 | 76 | 14 | Alpha | Customer order identifier (see below). The exchange does not check it for uniqueness |
| Appendage Length | 45 | 90 | 2 | Integer | 0 |
| Optional Appendage | 47 | | 0 | TagValue | Not sent |

## Field rules in this design

- **UserRefNum.** One hardware counter per TX port, shared by hardware and host orders. Each sent order takes the
  current value, and the counter then increments by 1. After reset the counter is 1. The host writes the next value
  (`TX_USERREF_NEXT`) before it releases the kill switch, so an intraday reset does not reuse numbers the exchange
  has already seen. The counter stops at `0xFFFFFFFF`: it does not wrap, and every later order is rejected
  (`INVALID`). The host learns each order's number from its `ORDER_SENT` ring entry.
- **ClOrdID.** Host orders carry the host's 14 bytes. Hardware orders carry the ASCII text `"HW"`, the rule index
  (one decimal digit, 0–7), the UserRefNum as 8 upper-case hexadecimal digits, then 3 spaces. Example: rule 0,
  UserRefNum 1 → `"HW000000001   "`.
- **Price.** Prices inside the core are ITCH `Price(4)` values: 32 bits with 4 implied decimals. OUCH uses the same
  scale in 8 bytes, so the core zero-extends the value to 64 bits. Every valid limit price fits in 32 bits.
- **Symbol.** Hardware orders copy the 8-byte ITCH `Stock` field of the triggering message. Host orders carry the
  host's 8 bytes.

### Orders that never leave the core

`risk_check` rejects every order that the exchange would reject or read in a different way. Its reason is
`INVALID`, a fixed check that cannot be configured:

- the price is 0, or it is above `0x7735939C`, or a rule's threshold plus offset overflows or underflows;
- the quantity is 0, or it is 1,000,000 or more;
- the side is not `'B'` or `'S'`;
- the time in force is not `'0'`, `'3'`, `'5'` or `'E'`;
- a byte of a host ClOrdID is not a letter, a digit or a space;
- the UserRefNum counter is exhausted.

A rule whose static fields are invalid (time in force, action side code, a fixed quantity) is flagged when it is
committed. It never fires, and `RULE_STATUS` shows an error bit.

## Worked example (bytes 42–91)

The order has UserRefNum 1, side buy, 100 shares of `AAPL` at $150.2500 (`1502500` = `0x0016ED24`), time in force
Day, Display `'Y'`, Capacity `'P'`, ISE `'N'` and CrossType `'N'`, and it comes from hardware rule 0:

```
offset  bytes
00:     00 30 55 4f 00 00 00 01 42 00 00 00 64 41 41 50
16:     4c 20 20 20 20 00 00 00 00 00 16 ed 24 30 59 50
32:     4e 4e 48 57 30 30 30 30 30 30 30 30 31 20 20 20
48:     00 00
```

Offset 0 here is frame byte 42. The bytes are: `00 30` Packet Length 48, `55` `'U'`, `4f` `'O'`, `00 00 00 01`
UserRefNum, `42` `'B'`, `00 00 00 64` Quantity, `41 41 50 4c 20 20 20 20` `"AAPL    "`, `00 00 00 00 00 16 ed 24`
Price, `30` `'0'`, `59` `'Y'`, `50` `'P'`, `4e` `'N'`, `4e` `'N'`, `"HW000000001   "` ClOrdID, `00 00` Appendage
Length.
