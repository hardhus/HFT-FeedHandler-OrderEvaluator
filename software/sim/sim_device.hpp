// hft::SimDevice: HFT_Core under Verilator as an hft::Device (audit/15 §2.7 P10). The C++ loop drives both
// clocks (net and host, their own periods); bus functional models sit on the core's ports:
//   - an AXI4-Lite master on s_axil_* (one access at a time, blocking; a response other than OKAY throws);
//   - an AXI4 write slave on m_axi_hmw_* into host memory (the ring region at ring_iova, AWREADY/WREADY with a
//     settable pause probability, BRESP OKAY; a write outside the region or with a partial strobe throws);
//   - an AXI4-Stream frame injector on s_axis_rx_* (tuser on the last beat = bad FCS);
//   - a frame capture on m_axis_tx_* (TREADY with a settable probability).
// Inputs change right after the rising edge of their clock; handshakes are the values before that edge.
// Verilator is hidden behind the implementation (no Verilator header here).
#pragma once

#include <cstdint>
#include <memory>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>

#include "hft/device.hpp"

namespace hft {

struct SimError : std::runtime_error {
  using std::runtime_error::runtime_error;
};

struct SimConfig {
  uint64_t net_period_ps = 6400;   // 156.25 MHz
  uint64_t host_period_ps = 4000;  // 250 MHz
  uint64_t ring_iova = 0;          // host memory the write slave answers
  std::size_t ring_bytes = 0;
  uint32_t seed = 1;  // Verilator's random reset values (--x-initial unique) and the BFMs' pauses
};

class SimDevice final : public Device {
 public:
  explicit SimDevice(const SimConfig& cfg);
  ~SimDevice() override;
  SimDevice(const SimDevice&) = delete;
  SimDevice& operator=(const SimDevice&) = delete;

  void csr_write32(uint32_t addr, uint32_t value) override;
  uint32_t csr_read32(uint32_t addr) override;
  std::span<std::byte> event_ring_memory() override;
  uint64_t ring_iova() const override;
  void poll_hook() override;  // one host clock cycle

  // Simulation only.
  void rx_frame(std::span<const uint8_t> frame, bool bad_fcs);  // returns once the last beat is taken
  void run_net_cycles(uint64_t n);
  void run_host_cycles(uint64_t n);
  void set_tx_ready_percent(int percent);
  void set_mem_ready_percent(int percent);
  const std::vector<std::vector<uint8_t>>& tx_frames() const;
  bool irq() const;
  uint64_t net_cycles() const;

 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};

}  // namespace hft
