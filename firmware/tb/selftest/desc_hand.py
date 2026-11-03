"""One ring entry of each kind, written by hand from the layout in audit/13 §2.4, independently of
event_desc.toml and scripts/gen_desc.py, so the generator cannot share a mistake with them. Multi-byte
values use distinct bytes, so a byte-order bug changes the entry. firmware/tb/sv/tb_event_desc.sv holds
the same entries for the SV pack functions.
"""

HEAD = "08 07 06 05 04 03 02 01  18 17 16 15 14 13 12 11  24 23 22 21"  # seq, sof_ts, ring_idx


def _zeros(n: int) -> str:
    return "00 " * n


HAND_ITCH = bytes.fromhex(
    HEAD + "01 01 5A 01"  # kind, flags, cfg_epoch, itch_class TRADE
    "50 2C 32 31 34 33  46 45 44 43 42 41"  # type P, len 44, locate, tracking, itch_ts
    "42 59 4F 00"  # side, printable, cross_type, pad
    "54 53 52 51  41 42 43 44"  # price, attribution "ABCD"
    "68 67 66 65 64 63 62 61" + _zeros(7) + "81"  # order_ref, pad, ctrl
    "78 77 76 75 74 73 72 71  88 87 86 85 84 83 82 81"  # new_ref, shares
    "98 97 96 95 94 93 92 91  41 41 50 4C 20 20 20 20" + _zeros(31) + "81"  # match, stock "AAPL    ", pad, ctrl
)
HAND_RAW = bytes.fromhex(
    HEAD + "02 00 A5 04  40"  # kind, flags, cfg_epoch, itch_class UNKNOWN, len 64
    "00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 10 11 12 13 14 15 16 17 18 19 1A 1B 1C 1D 1E 1F"
    "20 21 22 23 24 25  81"  # message bytes 0..37, ctrl
    "26 27 28 29 2A 2B 2C 2D 2E 2F 30 31 32 33 34 35 36 37 38 39 3A 3B 3C 3D 3E 3F" + _zeros(37) + "81"  # 38..63
)
HAND_GAP = bytes.fromhex(
    HEAD + "03 00 07 00"  # kind, flags, cfg_epoch, pad
    "A8 A7 A6 A5 A4 A3 A2 A1  B8 B7 B6 B5 B4 B3 B2 B1" + _zeros(23) + "01"  # first_seq, gap, pad, ctrl phase 0
    + _zeros(63) + "01"
)  # fmt: skip
HAND_EOS = bytes.fromhex(HEAD + "04 01 FF 00" + _zeros(39) + "81" + _zeros(63) + "81")
