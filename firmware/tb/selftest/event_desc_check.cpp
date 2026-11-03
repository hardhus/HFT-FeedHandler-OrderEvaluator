// Compile-time check of the generated descriptor header (software/include/gen/event_desc.h) against
// offsets and sizes written by hand here, independently of the schema. Built by
// firmware/tb/selftest/test_event_desc.py with g++ -std=c++20 -fsyntax-only -Wpadded.
#include <cstddef>

#include "gen/event_desc.h"

using namespace hft::desc;

#define OFS(type, member, ofs, size)                       \
  static_assert(offsetof(type, member) == (ofs), #member); \
  static_assert(sizeof(type::member) == (size), #member)

static_assert(alignof(ItchEntry) == 128 && sizeof(ItchEntry) == 128);
static_assert(alignof(ItchRawEntry) == 128 && sizeof(ItchRawEntry) == 128);
static_assert(alignof(GapEntry) == 128 && sizeof(GapEntry) == 128);
static_assert(alignof(EosEntry) == 128 && sizeof(EosEntry) == 128);
static_assert(static_cast<int>(Kind::ITCH) == 1 && static_cast<int>(Kind::ITCH_RAW) == 2);
static_assert(static_cast<int>(Kind::GAP) == 3 && static_cast<int>(Kind::EOS) == 4);
static_assert(kCtrlPhase == 0x80 && kCtrlVersionMask == 0x0F && kVersion == 1);
static_assert(kFlag_overflow_before == 0x01);

OFS(ItchEntry, seq, 0, 8);
OFS(ItchEntry, sof_ts, 8, 8);
OFS(ItchEntry, ring_idx, 16, 4);
OFS(ItchEntry, kind, 20, 1);
OFS(ItchEntry, flags, 21, 1);
OFS(ItchEntry, cfg_epoch, 22, 1);
OFS(ItchEntry, itch_class, 23, 1);
OFS(ItchEntry, msg_type, 24, 1);
OFS(ItchEntry, len, 25, 1);
OFS(ItchEntry, locate, 26, 2);
OFS(ItchEntry, tracking, 28, 2);
OFS(ItchEntry, itch_ts, 30, 6);
OFS(ItchEntry, side, 36, 1);
OFS(ItchEntry, printable, 37, 1);
OFS(ItchEntry, cross_type, 38, 1);
OFS(ItchEntry, price, 40, 4);
OFS(ItchEntry, attribution, 44, 4);
OFS(ItchEntry, order_ref, 48, 8);
OFS(ItchEntry, ctrl0, 63, 1);
OFS(ItchEntry, new_ref, 64, 8);
OFS(ItchEntry, shares, 72, 8);
OFS(ItchEntry, match, 80, 8);
OFS(ItchEntry, stock, 88, 8);
OFS(ItchEntry, ctrl1, 127, 1);

OFS(ItchRawEntry, seq, 0, 8);
OFS(ItchRawEntry, sof_ts, 8, 8);
OFS(ItchRawEntry, ring_idx, 16, 4);
OFS(ItchRawEntry, kind, 20, 1);
OFS(ItchRawEntry, flags, 21, 1);
OFS(ItchRawEntry, cfg_epoch, 22, 1);
OFS(ItchRawEntry, itch_class, 23, 1);
OFS(ItchRawEntry, len, 24, 1);
OFS(ItchRawEntry, raw_lo, 25, 38);
OFS(ItchRawEntry, ctrl0, 63, 1);
OFS(ItchRawEntry, raw_hi, 64, 26);
OFS(ItchRawEntry, ctrl1, 127, 1);

OFS(GapEntry, seq, 0, 8);
OFS(GapEntry, sof_ts, 8, 8);
OFS(GapEntry, ring_idx, 16, 4);
OFS(GapEntry, kind, 20, 1);
OFS(GapEntry, flags, 21, 1);
OFS(GapEntry, cfg_epoch, 22, 1);
OFS(GapEntry, first_seq, 24, 8);
OFS(GapEntry, gap, 32, 8);
OFS(GapEntry, ctrl0, 63, 1);
OFS(GapEntry, ctrl1, 127, 1);

OFS(EosEntry, seq, 0, 8);
OFS(EosEntry, sof_ts, 8, 8);
OFS(EosEntry, ring_idx, 16, 4);
OFS(EosEntry, kind, 20, 1);
OFS(EosEntry, flags, 21, 1);
OFS(EosEntry, cfg_epoch, 22, 1);
OFS(EosEntry, ctrl0, 63, 1);
OFS(EosEntry, ctrl1, 127, 1);
