# PCIe platform shell for `HFT_Core` (documentation)

How a board shell connects `HFT_Core` to an AMD PCIe bridge and a 10G MAC (audit/15 §2.9, S7). There is no RTL here
yet: no board is in the project (simulation only), and this file fixes the contract so the core does not change when
one comes. Statements from vendor documents carry their source (end of file); what still needs a board to confirm is
marked **verify on hardware**.

Target: an UltraScale+ part such as the `xcku5p` of the timing gates (PCIe Gen3, GTY transceivers for 10GBASE-R).
The Artix-7 profile (`xc7a100tcsg324-1`) is a timing gate for the core only: the CSG324 package has no transceivers,
so it can carry neither PCIe nor 10GBASE-R.

```
   host (PCIe)                                   shell                                   HFT_Core
 ───────────────  ┌──────────────────────────────────────────────────────────┐  ┌──────────────────────┐
  BAR (64 KiB) ──►│ bridge AXI4 master ─► AXI4 → AXI4-Lite converter ────────┼─►│ s_axil_*  (host_clk) │
  host memory  ◄──│ bridge AXI4 slave  ◄──────────────────────────────────────┼──│ m_axi_hmw_*          │
  MSI / INTx   ◄──│ interrupt port     ◄── edge → request (if a pulse port) ◄─┼──│ irq                  │
                  │ axi_aclk ─────────────────────────────────────────────────┼─►│ host_clk             │
                  │ axi_aresetn ─► (inverted) ────────────────────────────────┼─►│ rst_in               │
                  │ free-running 156.25 MHz ──────────────────────────────────┼─►│ net_clk              │
  SFP+ ◄─► GTY ◄─►│ 10G MAC/PCS ─► RX async FIFO (frame mode) ────────────────┼─►│ s_axis_rx_*          │
                  │ 10G MAC TX  ◄──────────────────────────────────────────────┼──│ m_axis_tx_*          │
                  └──────────────────────────────────────────────────────────┘  └──────────────────────┘
```

## Which bridge

The core needs two things from PCIe: the host's register accesses (a BAR into `s_axil_*`) and FPGA-initiated writes
into host memory (`m_axi_hmw_*`, the event ring). The second is an AXI slave bridge, which the descriptor DMA mode of
XDMA does not have.

| Part | IP | Host → registers | Ring writes | Interrupt |
|------|----|------------------|-------------|-----------|
| UltraScale+ | DMA/Bridge Subsystem (XDMA) in AXI Bridge mode, documented in PG194 (bridge mode moved there from PG195) | AXI4 master bridge | AXI4 slave bridge, up to 32 active writes | see Interrupt |
| UltraScale | AXI Bridge for PCIe Gen3 (PG194) | AXI4 master | AXI4 slave, up to 8 active writes | `intx_msi_request` (pulse), MSI-X through `cfg_interrupt_msix_*` |
| 7 series | AXI Memory Mapped to PCI Express, Gen2 (PG055) | AXI4 master | AXI4 slave, up to 2 active writes | MSI (no MSI-X) |

XDMA's DMA mode (PG195) has a PCIe-to-AXI4-Lite master BAR and the level `usr_irq_req`, but no slave bridge: it can
reach the registers but not write the ring without descriptors, so it is not this shell's bridge.

## Clocks

- `host_clk` = the bridge's `axi_aclk` (PG194: the clock of its AXI interfaces and of every interrupt sideband
  signal). `s_axil_*`, `m_axi_hmw_*` and `irq` are on it.
- `net_clk` = a **free-running** 156.25 MHz clock: a local reference through an MMCM, or the GT's TX user clock from
  a QPLL locked to the board's reference. Never the recovered RX clock (`rxusrclk2`): it can stop when the fibre is
  pulled, and then the commands that answer on the bus only once they are in effect at `net_clk` (`RISK_COMMIT`,
  `BUDGET_ADD`, the rule and TX commands, `TX_USERREF_NEXT`, `SYMBOL` and `LOCATE_ALLOW` accesses) never answer: the
  host's register access hangs until a PCIe completion timeout.
- The MAC's RX side crosses into `net_clk` through an asynchronous AXI4-Stream FIFO; the TX side runs on `net_clk`
  (the core gives and takes both directions on one clock).

## Reset

`rst_in` = `~axi_aresetn` and nothing else (audit/15 S7; the earlier "OR of PCIe, MAC and user resets" is replaced).

- Both AXI interfaces of the core face the bridge, and AXI wants the two ends of an interface reset together (AMD
  SmartConnect: devices on one interconnect are reset concurrently). A reset of the core alone drops what it has in
  flight: `axil_cpuif2` forgets an access it took, so the bridge's master waits for a response that never comes;
  `event_ring_writer` drops a burst before WLAST (its header: "Reset the memory side with it"). Either can hang the
  bridge. With `axi_aresetn` the bridge and the core restart together.
- A MAC or PCS reset (link down) resets only the MAC. A frame it cuts must still end on the core's input: the RX FIFO
  either drops the frame or closes it with `tlast` and `tuser` = 1 (`eth_ip_udp_rx` leaves its frame state only at
  `tlast`, and counts a `tuser` frame as `DROP_FCS`). A frame-mode FIFO that drops bad frames does this.
- Software reset of the device: a hot reset or a link-down event resets the bridge and so `axi_aresetn`. To reset
  without dropping the link, PG194's optional `dma_bridge_resetn` input (`CONFIG.soft_reset_en`) resets the bridge
  engines and registers together with the AXI side driven by `axi_aresetn`; follow PG194's Resets section for when
  to assert it. A Function Level Reset alone is not known to reset `axi_aresetn` in these bridges: do not rely on it
  (**verify on hardware**).
- Length: hold `rst_in` for at least 2·`SYNC_STAGES`+2 = 6 cycles of the slower clock after power-up, with **both**
  clocks running (`cdc_async_fifo`'s reset handshake; `reset_sync` releases each domain on its own clock).
  `axi_aresetn` is far longer; `net_clk` must already run, which a free-running clock does.
- After any reset `GLOBAL.RESET_SEEN` reads 1 and the whole configuration is at its reset values (core README,
  Software contract after reset).

## Host → registers (`s_axil_*`)

- One BAR of 64 KiB, mapped to AXI address 0 (the core takes the low 16 bits of the address). The bridge's AXI4
  master goes through an AXI4 → AXI4-Lite protocol converter into `s_axil_*`; accesses are 32 bit, aligned.
- `AxPROT` is ignored. Unmapped addresses and illegal accesses answer SLVERR.
- The bus waits while a command is in flight (decision 33): accesses answer within microseconds, far below a PCIe
  completion timeout, as long as `net_clk` runs (Clocks).

## Ring writes (`m_axi_hmw_*`)

- One INCR burst of 128 bytes per entry (`AWLEN` = 1024/`RING_DATA_W` − 1, full strobes), entries aligned to 128 B in
  a 4 KiB aligned region: no burst crosses 4 KiB. PG194's slave bridge accepts INCR only and answers other burst types
  with SLVERR.
- `RING_DATA_W` = the slave bridge's data width (parameter of `HFT_Core`, 512 by default), or a width converter in
  between. The core has no AXI ID: tie the bridge's ID inputs to a constant.
- The core keeps at most 4 bursts unanswered (`MAX_OUTSTANDING`); a bridge with fewer active writes (PG055: 2) holds
  AWREADY low and the writer waits.
- The AXI address is the host's bus address (`RING_BASE_*`, an IOVA): set the bridge's AXI-to-PCIe address
  translation to pass it through.
- BRESP: PG194 answers a write once its TLP will leave before any later TLP. `RING_DONE_IDX` counts answered bursts,
  so the entries it counts are in host memory before an interrupt that follows (core README, Ring interrupt).

## Interrupt (`irq`)

`irq` is a level on `host_clk`: `IRQ_EN.ring` && the ring running && `RING_DONE_IDX` ≠ `IRQ_RING_IDX`, registered.
The host clears it by writing `IRQ_RING_IDX`. How the shell turns it into a PCIe interrupt depends on the bridge:

- **XDMA `usr_irq_req`** (a level): connect `irq` directly. PG195: the request must stay asserted until
  `usr_irq_ack` and until the host has serviced it, which the level does. With MSI or MSI-X the block sends one
  message per **rising** edge (PG195, IRQ Block User Interrupt Enable Mask): a request still high after the handler
  sends nothing more. The core README's handler rule covers this (re-read `IRQ_STATUS` after writing `IRQ_RING_IDX`
  and go on while it is 1). The driver may also clear and set the user interrupt's enable mask: setting it while the
  source is high sends a new message (same table).
- **PG194 / PG055 `intx_msi_request`** (a one-cycle request, `intx_msi_grant` a one-cycle accept): the shell makes
  one request on each rising edge of `irq` and holds it until the grant (a small edge-to-request circuit on
  `axi_aclk`), with `msi_vector_num` constant. A rising edge while a request waits merges with it.
- **MSI-X through `cfg_interrupt_msix_*`** (PG194): one message per 0→1 of `cfg_interrupt_msix_int`; wait for
  `cfg_interrupt_msix_sent` or `_fail` (retry on fail) before the next; the vector table and PBA are in user logic.
- Legacy INTx: level semantics end to end; the same handler works.

## Ethernet (`s_axis_rx_*`, `m_axis_tx_*`)

- A 10G MAC/PCS with a 64-bit datapath at 156.25 MHz: for example `taxi` (Forencich; MAC, PHY and GTY wrappers for
  UltraScale+) or its predecessor `verilog-ethernet` (`eth_mac_10g`, XGMII, 64-bit). The core takes frames without
  FCS: `tuser` on the last beat = bad FCS (or a cut frame), counted as `DROP_FCS`. RX has no backpressure towards the
  wire, and none from the core: `s_axis_rx_tready` is constant 1 (`eth_ip_udp_rx`, line rate).
- TX: `m_axis_tx_*` straight into the MAC's TX input on `net_clk`; the MAC adds preamble, FCS and the interframe gap.
  The core holds `tvalid` and the beat while `tready` is low.
- "Order fired" (decision 8): if a board wants a pulse, the shell derives it from the first beat of each TX frame
  (`tvalid && tready` with no frame in progress). The core has no such port.

## Linux side

- Driver: AMD's `dma_ip_drivers` (XDMA) for bring-up, or `vfio-pci` for a user-space driver (BAR `mmap`, the ring in a
  hugepage mapped through the IOMMU; `ring_iova()` is that mapping's bus address). `hft::Device` (software/include)
  is the interface either one implements.
- Reset handling: at probe, in the health check and in the PCI `reset_done` callback (`pci_error_handlers`, the
  kernel's re-initialisation point after a reset), read `GLOBAL.RESET_SEEN`; on 1, run the whole configuration again
  (core README, Software contract after reset), and read `RESET_SEEN` once more at its end: 1 there means a reset hit
  the configuration and it starts over.
- Interrupts are optional (the hot path polls); a monitoring thread can use `irq` through MSI and an eventfd.

## Sources

- AMD PG194 v3.0, AXI Bridge for PCI Express Gen3 Subsystem:
  [Product Specification](https://docs.amd.com/r/en-US/pg194-axi-bridge-pcie-gen3/Product-Specification) (slave
  bridge active writes),
  [Global Signals](https://docs.amd.com/r/en-US/pg194-axi-bridge-pcie-gen3/Global-Signals) (`axi_aclk`),
  [Legacy Interrupts](https://docs.amd.com/r/en-US/pg194-axi-bridge-pcie-gen3/Legacy-Interrupts) and the MSI signal
  table (`intx_msi_request` "asserted for one clock period", `intx_msi_grant`),
  [External MSI-X Interrupts](https://docs.amd.com/r/en-US/pg194-axi-bridge-pcie-gen3/External-MSI-X-Interrupts),
  [Illegal Burst Type](https://docs.amd.com/r/en-US/pg194-axi-bridge-pcie-gen3/Illegal-Burst-Type),
  [Resets](https://docs.amd.com/r/en-US/pg194-axi-bridge-pcie-gen3/Resets) (`dma_bridge_resetn`), Transaction
  Ordering (BRESP after the MemWr is ordered, [PDF](https://www.amd.com/content/dam/xilinx/support/documents/ip_documentation/axi_pcie3/v3_0/pg194-axi-bridge-pcie-gen3.pdf)).
- AMD PG195 v4.1, DMA/Bridge Subsystem for PCIe ([PDF](https://www.xilinx.com/content/dam/xilinx/support/documents/ip_documentation/xdma/v4_1/pg195-pcie-dma.pdf)):
  IRQ Module p. 17–18, IRQ Block User Interrupt Enable Mask (table 80) p. 60;
  [v4.2 Revision History](https://docs.amd.com/r/en-US/pg195-pcie-dma/Revision-History) (bridge mode moved to PG194).
- AMD PG055 v2.9, AXI Memory Mapped to PCI Express (Gen2):
  [Product Specification](https://docs.amd.com/r/en-US/pg055-axi-bridge-pcie/Product-Specification),
  [Unsupported Features](https://docs.amd.com/r/en-US/pg055-axi-bridge-pcie/Unsupported-Features) (no MSI-X).
- AMD PG247, SmartConnect: [Resets](https://docs.amd.com/r/en-US/pg247-smartconnect/Resets) (concurrent reset).
- Linux: [PCI Error Recovery](https://docs.kernel.org/PCI/pci-error-recovery.html) (`reset_prepare`/`reset_done`).
- MAC: [taxi](https://git.byronlathi.com/bslathi19/taxi/src/commit/6e4988f010235ce418178f526404276b566163e6) (mirror),
  [verilog-ethernet](https://awesome.ecosyste.ms/projects/github.com%2Falexforencich%2Fverilog-ethernet).
