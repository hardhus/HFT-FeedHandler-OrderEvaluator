// hft::Device: the FPGA as host software sees it (audit/06 §1, audit/15 §2.7). One interface for the
// Verilator model (SimDevice), a board behind PCIe (PcieDevice, later) and pure C++ unit tests (MockDevice,
// later). The order mailbox is written field by field through csr_write32 and MBOX_SEND (F7), so there is no
// separate 64-byte mailbox write.
#pragma once

#include <cstddef>
#include <cstdint>
#include <span>

namespace hft {

class Device {
 public:
  virtual ~Device() = default;

  // 32-bit CSR access at a byte address of the map (software/include/gen/hft_csr.h). Reads are for the slow
  // path only (bring-up, snapshots, health checks).
  virtual void csr_write32(uint32_t addr, uint32_t value) = 0;
  virtual uint32_t csr_read32(uint32_t addr) = 0;

  // The host memory of the event ring, and the address the FPGA writes it at (RING_BASE_*).
  virtual std::span<std::byte> event_ring_memory() = 0;
  virtual uint64_t ring_iova() const = 0;

  // Called by a polling loop: SimDevice advances the simulation; a real device does nothing.
  virtual void poll_hook() {}
};

}  // namespace hft
