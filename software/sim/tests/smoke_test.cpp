// Co-simulation smoke test (audit/15 §2.7, S6): runs a vector file from firmware/tb/common/cosim_vectors.py
// through hft::SimDevice and checks every expected value. The expectations come from the Python models (decision
// 6); this file only executes and compares. Ring entries are read as host software reads them (both ctrl bytes
// with the expected phase), through the generated CSR header for every address it needs itself.
//
// Masked fields (timestamps) are checked for order instead: each one non-decreasing in ring order and between
// TS_SNAPSHOT of the first and the last snapshot. Every other byte must be equal. After the drain the test runs on
// and checks that no further entry or frame appears.
//
// Usage: hft_smoke <vector file> [seed]
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "gen/hft_csr.h"
#include "sim_device.hpp"

namespace {

using Bytes = std::vector<uint8_t>;

constexpr std::size_t kEntry = 128;
constexpr std::size_t kCtrl[2] = {63, 127};
constexpr uint64_t kDrainCycles = 2'000'000;  // host cycles
constexpr uint64_t kQuietCycles = 4'000;       // net cycles after the drain with nothing new allowed
constexpr int kPollReads = 1'000;

#define CSR(member) static_cast<uint32_t>(offsetof(hft_csr_t, member))

struct Failure : std::runtime_error {
  using std::runtime_error::runtime_error;
};

uint64_t num(const std::string& s) { return std::stoull(s, nullptr, 16); }

Bytes from_hex(const std::string& s) {
  if (s.size() % 2) throw Failure("odd hex length");
  Bytes out(s.size() / 2);
  for (std::size_t i = 0; i < out.size(); ++i) out[i] = static_cast<uint8_t>(std::stoul(s.substr(2 * i, 2), nullptr, 16));
  return out;
}

std::string to_hex(const Bytes& b) {
  static const char* d = "0123456789abcdef";
  std::string s;
  for (uint8_t x : b) {
    s += d[x >> 4];
    s += d[x & 15];
  }
  return s;
}

uint64_t le(const Bytes& b, std::size_t ofs, std::size_t n) {
  uint64_t v = 0;
  for (std::size_t i = 0; i < n; ++i) v |= uint64_t{b.at(ofs + i)} << (8 * i);
  return v;
}

struct Mask {
  std::string name;
  std::size_t ofs, size;
};

class Runner {
 public:
  explicit Runner(hft::SimDevice& dev) : dev_(dev) {
    const auto mem = dev_.event_ring_memory();
    slots_ = mem.size() / kEntry;
  }

  void csr_r(uint32_t a, uint32_t want, uint32_t mask) {
    const uint32_t got = dev_.csr_read32(a);
    if ((got & mask) != want) fail("CSR " + hexs(a) + " read " + hexs(got) + ", want " + hexs(want) + " mask " + hexs(mask));
  }

  void poll(uint32_t a, uint32_t want, uint32_t mask) {
    for (int i = 0; i < kPollReads; ++i) {
      if ((dev_.csr_read32(a) & mask) == want) return;
    }
    fail("CSR " + hexs(a) + " never read " + hexs(want) + " under mask " + hexs(mask));
  }

  // The STATS snapshot protocol (core README), then TS_SNAPSHOT.
  void snapshot() {
    const uint32_t before = dev_.csr_read32(CSR(STATS.STATS_SEQ));
    dev_.csr_write32(CSR(STATS.STATS_CTRL), 1);
    for (int i = 0; i < kPollReads; ++i) {
      if (dev_.csr_read32(CSR(STATS.STATS_SEQ)) != before) {
        const uint64_t lo = dev_.csr_read32(CSR(GLOBAL.TS_SNAPSHOT.LO));
        const uint64_t hi = dev_.csr_read32(CSR(GLOBAL.TS_SNAPSHOT.HI));
        ts_.push_back(hi << 32 | lo);
        return;
      }
    }
    fail("STATS_SEQ never changed after a snapshot request");
  }

  // Reads entries as the host does until `entries` are in memory and `frames` were sent.
  void drain(std::size_t entries, std::size_t frames) {
    for (uint64_t i = 0; i < kDrainCycles; ++i) {
      while (got_.size() < entries && poll_entry()) {
      }
      if (got_.size() >= entries && dev_.tx_frames().size() >= frames) break;
      dev_.poll_hook();
    }
    if (got_.size() < entries || dev_.tx_frames().size() < frames) {
      fail("drain: " + std::to_string(got_.size()) + " entries and " + std::to_string(dev_.tx_frames().size()) +
           " frames, want " + std::to_string(entries) + " and " + std::to_string(frames));
    }
    dev_.run_net_cycles(kQuietCycles);
    if (poll_entry()) fail("an entry after the expected ones: " + to_hex(got_.back()));
    if (dev_.tx_frames().size() != frames) fail("a frame after the expected ones");
  }

  void expect_ring(const Bytes& want, const std::vector<Mask>& masks) {
    if (ring_k_ >= got_.size()) fail("expect_ring " + std::to_string(ring_k_) + ": not read");
    Bytes got = got_[ring_k_];
    for (const Mask& m : masks) {
      masked_[m.name].push_back(le(got, m.ofs, m.size));
      for (std::size_t i = 0; i < m.size; ++i) got[m.ofs + i] = 0;
    }
    if (got != want) {
      fail("ring entry " + std::to_string(ring_k_) + " (masked fields zeroed)\n got  " + to_hex(got) + "\n want " +
           to_hex(want));
    }
    ++ring_k_;
  }

  void expect_tx(const Bytes& want) {
    const auto& frames = dev_.tx_frames();
    if (tx_k_ >= frames.size()) fail("expect_tx " + std::to_string(tx_k_) + ": not sent");
    if (frames[tx_k_] != want) {
      fail("TX frame " + std::to_string(tx_k_) + "\n got  " + to_hex(frames[tx_k_]) + "\n want " + to_hex(want));
    }
    ++tx_k_;
  }

  void end() {
    if (ring_k_ != got_.size()) fail(std::to_string(got_.size() - ring_k_) + " entries read and not expected");
    if (tx_k_ != dev_.tx_frames().size()) fail("frames sent and not expected");
    if (ts_.size() < 2) fail("the timestamp check needs two snapshots");
    const uint64_t lo = ts_.front(), hi = ts_.back();
    for (const auto& [name, values] : masked_) {
      for (std::size_t i = 0; i < values.size(); ++i) {
        if (values[i] < lo || values[i] > hi) {
          fail(name + " " + std::to_string(values[i]) + " outside TS_SNAPSHOT " + std::to_string(lo) + ".." +
               std::to_string(hi));
        }
        if (i && values[i] < values[i - 1]) fail(name + " goes back in ring order");
      }
    }
  }

  static std::string hexs(uint64_t v) {
    std::ostringstream s;
    s << "0x" << std::hex << v;
    return s.str();
  }

 private:
  [[noreturn]] static void fail(const std::string& msg) { throw Failure(msg); }

  // The next entry if both ctrl bytes show the expected phase (the NVMe pattern, host rule 2).
  bool poll_entry() {
    const auto mem = dev_.event_ring_memory();
    const std::size_t idx = got_.size();
    const std::size_t base = (idx % slots_) * kEntry;
    const uint8_t phase = (idx / slots_) % 2 ? 0 : 1;
    for (std::size_t c : kCtrl) {
      if ((static_cast<uint8_t>(mem[base + c]) >> 7) != phase) return false;
    }
    Bytes e(kEntry);
    for (std::size_t i = 0; i < kEntry; ++i) e[i] = static_cast<uint8_t>(mem[base + i]);
    if (le(e, 16, 4) != (idx & 0xFFFF'FFFF)) fail("entry " + std::to_string(idx) + ": ring_idx " + std::to_string(le(e, 16, 4)));
    got_.push_back(std::move(e));
    return true;
  }

  hft::SimDevice& dev_;
  std::size_t slots_ = 0;
  std::vector<Bytes> got_;
  std::size_t ring_k_ = 0, tx_k_ = 0;
  std::vector<uint64_t> ts_;
  std::map<std::string, std::vector<uint64_t>> masked_;
};

std::vector<std::vector<std::string>> read_lines(const char* path) {
  std::ifstream in(path);
  if (!in) throw Failure(std::string("cannot open ") + path);
  std::vector<std::vector<std::string>> out;
  std::string line;
  while (std::getline(in, line)) {
    if (auto h = line.find('#'); h != std::string::npos) line.resize(h);
    std::istringstream s(line);
    std::vector<std::string> words;
    for (std::string w; s >> w;) words.push_back(w);
    if (!words.empty()) out.push_back(std::move(words));
  }
  return out;
}

Mask parse_mask(const std::string& m) {
  const auto at = m.find('@'), colon = m.find(':');
  if (at == std::string::npos || colon == std::string::npos) throw Failure("bad mask " + m);
  return Mask{m.substr(0, at), std::stoul(m.substr(at + 1, colon - at - 1)), std::stoul(m.substr(colon + 1))};
}

int run(const char* path, uint32_t seed) {
  const auto lines = read_lines(path);
  if (lines.empty() || lines[0][0] != "memory" || lines[0].size() != 3) throw Failure("the first line must be memory");
  hft::SimConfig cfg;
  cfg.ring_iova = num(lines[0][1]);
  cfg.ring_bytes = num(lines[0][2]);
  cfg.seed = seed;
  hft::SimDevice dev(cfg);
  Runner r(dev);
  bool ended = false;
  for (std::size_t i = 1; i < lines.size(); ++i) {
    const auto& w = lines[i];
    const std::string& c = w[0];
    try {
      if (c == "csr_w") {
        dev.csr_write32(static_cast<uint32_t>(num(w.at(1))), static_cast<uint32_t>(num(w.at(2))));
      } else if (c == "csr_r") {
        r.csr_r(static_cast<uint32_t>(num(w.at(1))), static_cast<uint32_t>(num(w.at(2))), static_cast<uint32_t>(num(w.at(3))));
      } else if (c == "poll") {
        r.poll(static_cast<uint32_t>(num(w.at(1))), static_cast<uint32_t>(num(w.at(2))), static_cast<uint32_t>(num(w.at(3))));
      } else if (c == "tx_ready") {
        dev.set_tx_ready_percent(static_cast<int>(num(w.at(1))));
      } else if (c == "rx") {
        const Bytes f = from_hex(w.at(1));
        dev.rx_frame(f, w.size() > 2 && w[2] == "bad_fcs");
      } else if (c == "idle") {
        dev.run_net_cycles(num(w.at(1)));
      } else if (c == "snapshot") {
        r.snapshot();
      } else if (c == "drain") {
        r.drain(num(w.at(1)), num(w.at(2)));
      } else if (c == "expect_ring") {
        std::vector<Mask> masks;
        for (std::size_t k = 2; k < w.size(); ++k) masks.push_back(parse_mask(w[k]));
        r.expect_ring(from_hex(w.at(1)), masks);
      } else if (c == "expect_tx") {
        r.expect_tx(from_hex(w.at(1)));
      } else if (c == "end") {
        r.end();
        ended = true;
        break;
      } else {
        throw Failure("unknown command " + c);
      }
    } catch (const std::exception& e) {
      std::fprintf(stderr, "%s: line %zu (%s): %s\n", path, i + 1, c.c_str(), e.what());
      return 1;
    }
  }
  if (!ended) {
    std::fprintf(stderr, "%s: no end line\n", path);
    return 1;
  }
  std::printf("cosim smoke: PASS (%zu lines, %llu net cycles, %zu TX frames)\n", lines.size(),
              static_cast<unsigned long long>(dev.net_cycles()), dev.tx_frames().size());
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: %s <vector file> [seed]\n", argv[0]);
    return 2;
  }
  try {
    return run(argv[1], argc > 2 ? static_cast<uint32_t>(std::stoul(argv[2])) : 1u);
  } catch (const std::exception& e) {
    std::fprintf(stderr, "cosim smoke: %s\n", e.what());
    return 1;
  }
}
