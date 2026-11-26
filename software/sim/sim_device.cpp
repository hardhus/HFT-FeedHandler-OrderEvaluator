// hft::SimDevice: see sim_device.hpp.
#include "sim_device.hpp"

#include <algorithm>
#include <deque>
#include <random>
#include <sstream>

#include "Vhft_core.h"
#include "verilated.h"

namespace hft {
namespace {

constexpr uint64_t kTimeoutCycles = 200'000;  // a bus access or a frame that takes longer is a hang
constexpr unsigned kResetCycles = 20;         // host cycles with rst_in high
constexpr unsigned kAfterReset = 64;          // host cycles after it (both reset_syncs released)

std::string hex(uint64_t v) {
  std::ostringstream s;
  s << "0x" << std::hex << v;
  return s.str();
}

}  // namespace

struct SimDevice::Impl {
  SimConfig cfg;
  std::unique_ptr<VerilatedContext> ctx;
  std::unique_ptr<Vhft_core> top;
  std::mt19937 rng;
  std::vector<std::byte> mem;

  // Clocks: the time of each one's next edge, and its level.
  uint64_t next_net = 0, next_host = 0;
  bool net_hi = false, host_hi = false;
  uint64_t net_count = 0, host_count = 0;
  int tx_ready_pct = 100, mem_ready_pct = 100;
  // The models watch the ports only after reset: outputs start random (--x-initial unique) and are undefined
  // until the core's reset_syncs have acted.
  bool active = false;

  // AXI4-Lite master: the handshakes of the access in progress.
  bool lite_aw = false, lite_w = false, lite_b = false, lite_ar = false, lite_r = false;
  uint32_t lite_rdata = 0;
  uint8_t lite_resp = 0;

  // AXI4 write slave.
  struct Burst {
    uint64_t addr;
    unsigned beats;
    unsigned bytes;  // per beat
    unsigned beat = 0;
  };
  std::deque<Burst> bursts;
  unsigned b_pending = 0;

  // AXI4-Stream injector and capture.
  std::vector<uint8_t> rx_bytes;
  bool rx_bad = false, rx_busy = false;
  std::size_t rx_pos = 0;
  std::vector<uint8_t> tx_cur;
  std::vector<std::vector<uint8_t>> tx_done;

  // Handshakes and data seen before a rising edge.
  struct HostSample {
    bool aw, w, b, ar, r;
    uint8_t bresp, rresp;
    uint32_t rdata;
    bool m_aw, m_w, m_b, m_wlast;
    uint64_t m_awaddr, m_wstrb;
    unsigned m_awlen, m_awsize;
    std::vector<uint32_t> m_wdata;
  };
  struct NetSample {
    bool rx, tx, tx_last;
    uint64_t tx_data;
    uint8_t tx_keep;
  };

  explicit Impl(const SimConfig& c) : cfg(c), ctx(std::make_unique<VerilatedContext>()), rng(c.seed) {
    if (cfg.ring_bytes == 0 || cfg.ring_iova % 4096 != 0) {
      throw SimError("SimDevice: ring memory must be non-empty and 4 KiB aligned");
    }
    mem.assign(cfg.ring_bytes, std::byte{0});
    ctx->randReset(2);  // --x-initial unique: random initial values, as the cocotb builds
    ctx->randSeed(static_cast<int>(cfg.seed));
    top = std::make_unique<Vhft_core>(ctx.get(), "TOP");
    Vhft_core& t = *top;
    t.rst_in = 1;
    t.net_clk = 0;
    t.host_clk = 0;
    t.s_axil_awvalid = t.s_axil_wvalid = t.s_axil_bready = t.s_axil_arvalid = t.s_axil_rready = 0;
    t.s_axil_awaddr = t.s_axil_araddr = 0;
    t.s_axil_awprot = t.s_axil_arprot = 0;
    t.s_axil_wdata = 0;
    t.s_axil_wstrb = 0;
    t.s_axis_rx_tvalid = t.s_axis_rx_tlast = t.s_axis_rx_tuser = 0;
    t.s_axis_rx_tdata = 0;
    t.s_axis_rx_tkeep = 0;
    t.m_axis_tx_tready = 0;
    t.m_axi_hmw_awready = t.m_axi_hmw_wready = t.m_axi_hmw_bvalid = 0;
    t.m_axi_hmw_bresp = 0;
    t.eval();
  }

  bool roll(int pct) { return pct >= 100 || (pct > 0 && static_cast<int>(rng() % 100) < pct); }

  HostSample sample_host() {
    Vhft_core& t = *top;
    HostSample s{};
    s.aw = t.s_axil_awvalid && t.s_axil_awready;
    s.w = t.s_axil_wvalid && t.s_axil_wready;
    s.b = t.s_axil_bvalid && t.s_axil_bready;
    s.ar = t.s_axil_arvalid && t.s_axil_arready;
    s.r = t.s_axil_rvalid && t.s_axil_rready;
    s.bresp = t.s_axil_bresp;
    s.rresp = t.s_axil_rresp;
    s.rdata = t.s_axil_rdata;
    s.m_aw = t.m_axi_hmw_awvalid && t.m_axi_hmw_awready;
    s.m_w = t.m_axi_hmw_wvalid && t.m_axi_hmw_wready;
    s.m_b = t.m_axi_hmw_bvalid && t.m_axi_hmw_bready;
    s.m_awaddr = t.m_axi_hmw_awaddr;
    s.m_awlen = t.m_axi_hmw_awlen;
    s.m_awsize = t.m_axi_hmw_awsize;
    s.m_wlast = t.m_axi_hmw_wlast;
    s.m_wstrb = t.m_axi_hmw_wstrb;
    if (s.m_w) {
      constexpr std::size_t words = sizeof(t.m_axi_hmw_wdata) / sizeof(t.m_axi_hmw_wdata[0]);
      s.m_wdata.assign(words, 0);
      for (std::size_t i = 0; i < words; ++i) s.m_wdata[i] = t.m_axi_hmw_wdata[i];
    }
    return s;
  }

  NetSample sample_net() {
    Vhft_core& t = *top;
    return NetSample{t.s_axis_rx_tvalid && t.s_axis_rx_tready, t.m_axis_tx_tvalid && t.m_axis_tx_tready,
                     static_cast<bool>(t.m_axis_tx_tlast), t.m_axis_tx_tdata, t.m_axis_tx_tkeep};
  }

  void write_beat(Burst& b, const HostSample& s) {
    const uint64_t a = b.addr + uint64_t{b.beat} * b.bytes;
    if (a < cfg.ring_iova || a + b.bytes > cfg.ring_iova + mem.size()) {
      throw SimError("AXI write outside the ring memory at " + hex(a));
    }
    const uint64_t all = b.bytes >= 64 ? ~uint64_t{0} : (uint64_t{1} << b.bytes) - 1;
    if ((s.m_wstrb & all) != all) throw SimError("AXI write with a partial strobe at " + hex(a));
    if (s.m_wlast != (b.beat + 1 == b.beats)) throw SimError("WLAST not on the last beat at " + hex(a));
    for (unsigned j = 0; j < b.bytes; ++j) {
      const uint32_t word = s.m_wdata.at(j / 4);
      mem[a - cfg.ring_iova + j] = static_cast<std::byte>(word >> (8 * (j % 4)));
    }
    ++b.beat;
  }

  void update_host(const HostSample& s) {
    Vhft_core& t = *top;
    if (s.aw) {
      t.s_axil_awvalid = 0;
      lite_aw = true;
    }
    if (s.w) {
      t.s_axil_wvalid = 0;
      lite_w = true;
    }
    if (s.b) {
      lite_b = true;
      lite_resp = s.bresp;
    }
    if (s.ar) {
      t.s_axil_arvalid = 0;
      lite_ar = true;
    }
    if (s.r) {
      lite_r = true;
      lite_rdata = s.rdata;
      lite_resp = s.rresp;
    }
    if (s.m_aw) bursts.push_back(Burst{s.m_awaddr, s.m_awlen + 1, 1u << s.m_awsize});
    if (s.m_w) {
      if (bursts.empty()) throw SimError("AXI W beat taken with no burst address");
      write_beat(bursts.front(), s);
      if (bursts.front().beat == bursts.front().beats) {
        bursts.pop_front();
        ++b_pending;
      }
    }
    if (s.m_b) --b_pending;
    t.m_axi_hmw_awready = roll(mem_ready_pct);
    t.m_axi_hmw_wready = !bursts.empty() && roll(mem_ready_pct);
    t.m_axi_hmw_bvalid = b_pending > 0;
    t.m_axi_hmw_bresp = 0;  // OKAY
  }

  void drive_rx() {
    Vhft_core& t = *top;
    if (!rx_busy) {
      t.s_axis_rx_tvalid = 0;
      t.s_axis_rx_tlast = 0;
      t.s_axis_rx_tuser = 0;
      return;
    }
    const std::size_t n = std::min<std::size_t>(8, rx_bytes.size() - rx_pos);
    uint64_t data = 0;
    for (std::size_t j = 0; j < n; ++j) data |= uint64_t{rx_bytes[rx_pos + j]} << (8 * j);
    const bool last = rx_pos + n == rx_bytes.size();
    t.s_axis_rx_tdata = data;
    t.s_axis_rx_tkeep = static_cast<uint8_t>((1u << n) - 1);
    t.s_axis_rx_tlast = last;
    t.s_axis_rx_tuser = last && rx_bad;
    t.s_axis_rx_tvalid = 1;
  }

  void update_net(const NetSample& s) {
    Vhft_core& t = *top;
    if (s.rx) {
      rx_pos = std::min(rx_pos + 8, rx_bytes.size());
      if (rx_pos == rx_bytes.size()) rx_busy = false;
    }
    drive_rx();
    if (s.tx) {
      unsigned n = 0;
      while (n < 8 && (s.tx_keep >> n & 1)) ++n;
      if (s.tx_keep != ((1u << n) - 1)) throw SimError("TX beat with a non-contiguous tkeep");
      for (unsigned j = 0; j < n; ++j) tx_cur.push_back(static_cast<uint8_t>(s.tx_data >> (8 * j)));
      if (s.tx_last) {
        tx_done.push_back(std::move(tx_cur));
        tx_cur.clear();
      }
    }
    t.m_axis_tx_tready = roll(tx_ready_pct);
  }

  // Advances to the next clock edge (either clock, both if they meet).
  void step() {
    const uint64_t now = std::min(next_net, next_host);
    ctx->time(now);
    const bool net_edge = next_net == now, host_edge = next_host == now;
    const bool net_rise = net_edge && !net_hi, host_rise = host_edge && !host_hi;
    NetSample sn{};
    HostSample sh{};
    if (net_rise && active) sn = sample_net();
    if (host_rise && active) sh = sample_host();
    if (net_edge) {
      net_hi = !net_hi;
      top->net_clk = net_hi;
      next_net += cfg.net_period_ps / 2;
    }
    if (host_edge) {
      host_hi = !host_hi;
      top->host_clk = host_hi;
      next_host += cfg.host_period_ps / 2;
    }
    top->eval();
    if (net_rise) {
      ++net_count;
      if (active) update_net(sn);
    }
    if (host_rise) {
      ++host_count;
      if (active) update_host(sh);
    }
    if (net_rise || host_rise) top->eval();
    if (ctx->gotFinish()) throw SimError("the simulation finished (an assertion or $finish)");
  }

  void run_host(uint64_t n) {
    const uint64_t end = host_count + n;
    while (host_count < end) step();
  }
  void run_net(uint64_t n) {
    const uint64_t end = net_count + n;
    while (net_count < end) step();
  }
};

SimDevice::SimDevice(const SimConfig& cfg) : p_(std::make_unique<Impl>(cfg)) {
  p_->run_host(kResetCycles);
  p_->top->rst_in = 0;
  p_->run_host(kAfterReset);
  p_->active = true;
  p_->update_host(Impl::HostSample{});  // the models' first outputs
  p_->update_net(Impl::NetSample{});
  p_->top->eval();
}

SimDevice::~SimDevice() {
  if (p_ && p_->top) p_->top->final();
}

void SimDevice::csr_write32(uint32_t addr, uint32_t value) {
  Impl& p = *p_;
  Vhft_core& t = *p.top;
  p.lite_aw = p.lite_w = p.lite_b = false;
  t.s_axil_awaddr = static_cast<uint16_t>(addr);
  t.s_axil_awprot = 0;
  t.s_axil_awvalid = 1;
  t.s_axil_wdata = value;
  t.s_axil_wstrb = 0xF;
  t.s_axil_wvalid = 1;
  t.s_axil_bready = 1;
  for (uint64_t i = 0; i < kTimeoutCycles && !(p.lite_aw && p.lite_w && p.lite_b); ++i) p.run_host(1);
  t.s_axil_bready = 0;
  if (!(p.lite_aw && p.lite_w && p.lite_b)) throw SimError("CSR write " + hex(addr) + ": no response");
  if (p.lite_resp != 0) throw SimError("CSR write " + hex(addr) + ": BRESP " + std::to_string(p.lite_resp));
}

uint32_t SimDevice::csr_read32(uint32_t addr) {
  Impl& p = *p_;
  Vhft_core& t = *p.top;
  p.lite_ar = p.lite_r = false;
  t.s_axil_araddr = static_cast<uint16_t>(addr);
  t.s_axil_arprot = 0;
  t.s_axil_arvalid = 1;
  t.s_axil_rready = 1;
  for (uint64_t i = 0; i < kTimeoutCycles && !(p.lite_ar && p.lite_r); ++i) p.run_host(1);
  t.s_axil_rready = 0;
  if (!(p.lite_ar && p.lite_r)) throw SimError("CSR read " + hex(addr) + ": no response");
  if (p.lite_resp != 0) throw SimError("CSR read " + hex(addr) + ": RRESP " + std::to_string(p.lite_resp));
  return p.lite_rdata;
}

std::span<std::byte> SimDevice::event_ring_memory() { return p_->mem; }
uint64_t SimDevice::ring_iova() const { return p_->cfg.ring_iova; }
void SimDevice::poll_hook() { p_->run_host(1); }

void SimDevice::rx_frame(std::span<const uint8_t> frame, bool bad_fcs) {
  Impl& p = *p_;
  if (frame.empty()) throw SimError("rx_frame: empty frame");
  p.rx_bytes.assign(frame.begin(), frame.end());
  p.rx_bad = bad_fcs;
  p.rx_pos = 0;
  p.rx_busy = true;
  p.drive_rx();
  for (uint64_t i = 0; i < kTimeoutCycles && p.rx_busy; ++i) p.run_net(1);
  if (p.rx_busy) throw SimError("rx_frame: the core never took the frame");
}

void SimDevice::run_net_cycles(uint64_t n) { p_->run_net(n); }
void SimDevice::run_host_cycles(uint64_t n) { p_->run_host(n); }
void SimDevice::set_tx_ready_percent(int percent) { p_->tx_ready_pct = percent; }
void SimDevice::set_mem_ready_percent(int percent) { p_->mem_ready_pct = percent; }
const std::vector<std::vector<uint8_t>>& SimDevice::tx_frames() const { return p_->tx_done; }
bool SimDevice::irq() const { return p_->top->irq; }
uint64_t SimDevice::net_cycles() const { return p_->net_count; }

}  // namespace hft
