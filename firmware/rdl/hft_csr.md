<!---
Markdown description for SystemRDL register map.

Don't override. Generated from: hft_csr
  - firmware/rdl/hft_csr.rdl
-->

## hft_csr address map

- Absolute Address: 0x0
- Base Offset: 0x0
- Size: 0x468

<p>Host register map. 32-bit registers, 64 KiB window, SLVERR on unmapped addresses and illegal accesses.</p>

|Offset|Identifier|      Name     |
|------|----------|---------------|
| 0x000|  GLOBAL  |     Global    |
| 0x100|    NET   |Network receive|
| 0x400|   STATS  |   Statistics  |

## GLOBAL register file

- Absolute Address: 0x0
- Base Offset: 0x0
- Size: 0xC

<p>Identification and a scratch register.</p>

|Offset|Identifier|        Name        |
|------|----------|--------------------|
|  0x0 |    ID    |   Identification   |
|  0x4 |  VERSION |Register map version|
|  0x8 |  SCRATCH |       Scratch      |

### ID register

- Absolute Address: 0x0
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|   Reset  |Name|
|----|----------|------|----------|----|
|31:0|    id    |   r  |0x48465431|  — |

#### id field

<p>Constant "HFT1" (0x48465431): the right device is mapped.</p>

### VERSION register

- Absolute Address: 0x4
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access| Reset |Name|
|----|----------|------|-------|----|
|31:0|  version |   r  |0x50000|  — |

#### version field

<p>Major.minor of this map: 0x0005_0000 is the F5 map. Software checks it before use.</p>

### SCRATCH register

- Absolute Address: 0x8
- Base Offset: 0x8
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |  rw  | 0x0 |  — |

#### value field

<p>Read/write, no hardware effect: bus read-back test.</p>

## NET register file

- Absolute Address: 0x100
- Base Offset: 0x100
- Size: 0x60

<p>eth_ip_udp_rx configuration. Every register here is a shadow: a write changes nothing in the receiver until NET_COMMIT, which moves the whole block to the net clock domain in one transfer; the receiver then switches to it in a single cycle. Reads return the shadow (what was written); NET_STATUS.commit_busy tells whether the last commit has been applied.</p>

|Offset| Identifier |       Name       |
|------|------------|------------------|
| 0x00 |  NET_CTRL  |  Receive control |
| 0x04 |LOCAL_MAC_LO|  Local MAC, low  |
| 0x08 |LOCAL_MAC_HI|  Local MAC, high |
| 0x0C | NET_COMMIT |      Commit      |
| 0x10 | NET_STATUS |      Status      |
| 0x20 |  FILTER[0] |Destination filter|
| 0x28 |  FILTER[1] |Destination filter|
| 0x30 |  FILTER[2] |Destination filter|
| 0x38 |  FILTER[3] |Destination filter|
| 0x40 |  FILTER[4] |Destination filter|
| 0x48 |  FILTER[5] |Destination filter|
| 0x50 |  FILTER[6] |Destination filter|
| 0x58 |  FILTER[7] |Destination filter|

### NET_CTRL register

- Absolute Address: 0x100
- Base Offset: 0x0
- Size: 0x4

|Bits| Identifier |Access|Reset|Name|
|----|------------|------|-----|----|
|  0 |    rx_en   |  rw  | 0x0 |  — |
|  1 |accept_bcast|  rw  | 0x0 |  — |

#### rx_en field

<p>Receiver enabled. 0 after reset: frames are dropped (reason DISABLED) until software has written a complete configuration and committed it with rx_en = 1.</p>

#### accept_bcast field

<p>Accept frames to the broadcast MAC ff:ff:ff:ff:ff:ff.</p>

### LOCAL_MAC_LO register

- Absolute Address: 0x104
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    mac   |  rw  | 0x0 |  — |

#### mac field

<p>MAC address bits [31:0]: the last four bytes (02:00:00:00:00:01 -&gt; 0x00000001).</p>

### LOCAL_MAC_HI register

- Absolute Address: 0x108
- Base Offset: 0x8
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|15:0|    mac   |  rw  | 0x0 |  — |

#### mac field

<p>MAC address bits [47:32]: the first two bytes (02:00:00:00:00:01 -&gt; 0x0200).</p>

### NET_COMMIT register

- Absolute Address: 0x10C
- Base Offset: 0xC
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 |  commit  |   w  | 0x0 |  — |

#### commit field

<p>Write 1: send the whole NET block to the receiver (atomic). A commit while the previous one is in flight is not lost: the newest shadow is sent when the transfer is free.</p>

### NET_STATUS register

- Absolute Address: 0x110
- Base Offset: 0x10
- Size: 0x4

|Bits| Identifier|Access|Reset|Name|
|----|-----------|------|-----|----|
|  0 |commit_busy|   r  |  —  |  — |

#### commit_busy field

<p>1 while a commit is pending or in flight. 0: every write before the last NET_COMMIT is in effect in the receiver.</p>

## FILTER register file

- Absolute Address: 0x120
- Base Offset: 0x20
- Size: 0x8
- Array Dimensions: [8]
- Array Stride: 0x8
- Total Size: 0x40

<p>A frame passes when its destination IP and UDP port equal an enabled entry. The lowest matching index is reported with the payload.</p>

|Offset|Identifier|         Name         |
|------|----------|----------------------|
|  0x0 |    IP    |       Filter IP      |
|  0x4 |  PORT_EN |Filter port and enable|

### IP register

- Absolute Address: 0x120
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    ip    |  rw  | 0x0 |  — |

#### ip field

<p>Destination IPv4 address (10.0.0.2 -&gt; 0x0A000002), unicast or multicast group.</p>

### PORT_EN register

- Absolute Address: 0x124
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|15:0|   port   |  rw  | 0x0 |  — |
| 16 |    en    |  rw  | 0x0 |  — |

#### port field

<p>Destination UDP port.</p>

#### en field

<p>Entry enabled.</p>

## FILTER register file

- Absolute Address: 0x128
- Base Offset: 0x20
- Size: 0x8
- Array Dimensions: [8]
- Array Stride: 0x8
- Total Size: 0x40

<p>A frame passes when its destination IP and UDP port equal an enabled entry. The lowest matching index is reported with the payload.</p>

|Offset|Identifier|         Name         |
|------|----------|----------------------|
|  0x0 |    IP    |       Filter IP      |
|  0x4 |  PORT_EN |Filter port and enable|

### IP register

- Absolute Address: 0x128
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    ip    |  rw  | 0x0 |  — |

#### ip field

<p>Destination IPv4 address (10.0.0.2 -&gt; 0x0A000002), unicast or multicast group.</p>

### PORT_EN register

- Absolute Address: 0x12C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|15:0|   port   |  rw  | 0x0 |  — |
| 16 |    en    |  rw  | 0x0 |  — |

#### port field

<p>Destination UDP port.</p>

#### en field

<p>Entry enabled.</p>

## FILTER register file

- Absolute Address: 0x130
- Base Offset: 0x20
- Size: 0x8
- Array Dimensions: [8]
- Array Stride: 0x8
- Total Size: 0x40

<p>A frame passes when its destination IP and UDP port equal an enabled entry. The lowest matching index is reported with the payload.</p>

|Offset|Identifier|         Name         |
|------|----------|----------------------|
|  0x0 |    IP    |       Filter IP      |
|  0x4 |  PORT_EN |Filter port and enable|

### IP register

- Absolute Address: 0x130
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    ip    |  rw  | 0x0 |  — |

#### ip field

<p>Destination IPv4 address (10.0.0.2 -&gt; 0x0A000002), unicast or multicast group.</p>

### PORT_EN register

- Absolute Address: 0x134
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|15:0|   port   |  rw  | 0x0 |  — |
| 16 |    en    |  rw  | 0x0 |  — |

#### port field

<p>Destination UDP port.</p>

#### en field

<p>Entry enabled.</p>

## FILTER register file

- Absolute Address: 0x138
- Base Offset: 0x20
- Size: 0x8
- Array Dimensions: [8]
- Array Stride: 0x8
- Total Size: 0x40

<p>A frame passes when its destination IP and UDP port equal an enabled entry. The lowest matching index is reported with the payload.</p>

|Offset|Identifier|         Name         |
|------|----------|----------------------|
|  0x0 |    IP    |       Filter IP      |
|  0x4 |  PORT_EN |Filter port and enable|

### IP register

- Absolute Address: 0x138
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    ip    |  rw  | 0x0 |  — |

#### ip field

<p>Destination IPv4 address (10.0.0.2 -&gt; 0x0A000002), unicast or multicast group.</p>

### PORT_EN register

- Absolute Address: 0x13C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|15:0|   port   |  rw  | 0x0 |  — |
| 16 |    en    |  rw  | 0x0 |  — |

#### port field

<p>Destination UDP port.</p>

#### en field

<p>Entry enabled.</p>

## FILTER register file

- Absolute Address: 0x140
- Base Offset: 0x20
- Size: 0x8
- Array Dimensions: [8]
- Array Stride: 0x8
- Total Size: 0x40

<p>A frame passes when its destination IP and UDP port equal an enabled entry. The lowest matching index is reported with the payload.</p>

|Offset|Identifier|         Name         |
|------|----------|----------------------|
|  0x0 |    IP    |       Filter IP      |
|  0x4 |  PORT_EN |Filter port and enable|

### IP register

- Absolute Address: 0x140
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    ip    |  rw  | 0x0 |  — |

#### ip field

<p>Destination IPv4 address (10.0.0.2 -&gt; 0x0A000002), unicast or multicast group.</p>

### PORT_EN register

- Absolute Address: 0x144
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|15:0|   port   |  rw  | 0x0 |  — |
| 16 |    en    |  rw  | 0x0 |  — |

#### port field

<p>Destination UDP port.</p>

#### en field

<p>Entry enabled.</p>

## FILTER register file

- Absolute Address: 0x148
- Base Offset: 0x20
- Size: 0x8
- Array Dimensions: [8]
- Array Stride: 0x8
- Total Size: 0x40

<p>A frame passes when its destination IP and UDP port equal an enabled entry. The lowest matching index is reported with the payload.</p>

|Offset|Identifier|         Name         |
|------|----------|----------------------|
|  0x0 |    IP    |       Filter IP      |
|  0x4 |  PORT_EN |Filter port and enable|

### IP register

- Absolute Address: 0x148
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    ip    |  rw  | 0x0 |  — |

#### ip field

<p>Destination IPv4 address (10.0.0.2 -&gt; 0x0A000002), unicast or multicast group.</p>

### PORT_EN register

- Absolute Address: 0x14C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|15:0|   port   |  rw  | 0x0 |  — |
| 16 |    en    |  rw  | 0x0 |  — |

#### port field

<p>Destination UDP port.</p>

#### en field

<p>Entry enabled.</p>

## FILTER register file

- Absolute Address: 0x150
- Base Offset: 0x20
- Size: 0x8
- Array Dimensions: [8]
- Array Stride: 0x8
- Total Size: 0x40

<p>A frame passes when its destination IP and UDP port equal an enabled entry. The lowest matching index is reported with the payload.</p>

|Offset|Identifier|         Name         |
|------|----------|----------------------|
|  0x0 |    IP    |       Filter IP      |
|  0x4 |  PORT_EN |Filter port and enable|

### IP register

- Absolute Address: 0x150
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    ip    |  rw  | 0x0 |  — |

#### ip field

<p>Destination IPv4 address (10.0.0.2 -&gt; 0x0A000002), unicast or multicast group.</p>

### PORT_EN register

- Absolute Address: 0x154
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|15:0|   port   |  rw  | 0x0 |  — |
| 16 |    en    |  rw  | 0x0 |  — |

#### port field

<p>Destination UDP port.</p>

#### en field

<p>Entry enabled.</p>

## FILTER register file

- Absolute Address: 0x158
- Base Offset: 0x20
- Size: 0x8
- Array Dimensions: [8]
- Array Stride: 0x8
- Total Size: 0x40

<p>A frame passes when its destination IP and UDP port equal an enabled entry. The lowest matching index is reported with the payload.</p>

|Offset|Identifier|         Name         |
|------|----------|----------------------|
|  0x0 |    IP    |       Filter IP      |
|  0x4 |  PORT_EN |Filter port and enable|

### IP register

- Absolute Address: 0x158
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    ip    |  rw  | 0x0 |  — |

#### ip field

<p>Destination IPv4 address (10.0.0.2 -&gt; 0x0A000002), unicast or multicast group.</p>

### PORT_EN register

- Absolute Address: 0x15C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|15:0|   port   |  rw  | 0x0 |  — |
| 16 |    en    |  rw  | 0x0 |  — |

#### port field

<p>Destination UDP port.</p>

#### en field

<p>Entry enabled.</p>

## STATS register file

- Absolute Address: 0x400
- Base Offset: 0x400
- Size: 0x68

<p>Receive counters (net clock domain, 64 bit, wrap; cleared only by reset). Software reads them through a snapshot: read STATS_SEQ, write STATS_CTRL.snapshot, wait until STATS_SEQ changes, then read. All counters of one snapshot are taken in the same net clock cycle. DROP_<reason> is at 0x0420 + 8 * (reason - 1), reason = rx_reason_e in hft_pkg.sv.</p>

|Offset|  Identifier  |       Name       |
|------|--------------|------------------|
| 0x00 |  STATS_CTRL  |Statistics control|
| 0x04 |   STATS_SEQ  | Snapshot sequence|
| 0x10 |    RX_PKTS   |  64-bit counter  |
| 0x18 |   RX_BYTES   |  64-bit counter  |
| 0x20 | DROP_DISABLED|  64-bit counter  |
| 0x28 |   DROP_FCS   |  64-bit counter  |
| 0x30 |   DROP_MAC   |  64-bit counter  |
| 0x38 |DROP_ETHERTYPE|  64-bit counter  |
| 0x40 |  DROP_IP_HDR |  64-bit counter  |
| 0x48 |   DROP_FRAG  |  64-bit counter  |
| 0x50 |  DROP_PROTO  |  64-bit counter  |
| 0x58 |  DROP_FILTER |  64-bit counter  |
| 0x60 |   DROP_LEN   |  64-bit counter  |

### STATS_CTRL register

- Absolute Address: 0x400
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 | snapshot |   w  | 0x0 |  — |

#### snapshot field

<p>Write 1: take a snapshot of all counters. A request while one is in flight is not lost: it is served when the transfer is free.</p>

### STATS_SEQ register

- Absolute Address: 0x404
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    seq   |   r  | 0x0 |  — |

#### seq field

<p>Incremented (and wraps) each time a snapshot lands in the registers below.</p>

## RX_PKTS register file

- Absolute Address: 0x410
- Base Offset: 0x10
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x410
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x414
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RX_BYTES register file

- Absolute Address: 0x418
- Base Offset: 0x18
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x418
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x41C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## DROP_DISABLED register file

- Absolute Address: 0x420
- Base Offset: 0x20
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x420
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x424
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## DROP_FCS register file

- Absolute Address: 0x428
- Base Offset: 0x28
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x428
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x42C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## DROP_MAC register file

- Absolute Address: 0x430
- Base Offset: 0x30
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x430
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x434
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## DROP_ETHERTYPE register file

- Absolute Address: 0x438
- Base Offset: 0x38
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x438
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x43C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## DROP_IP_HDR register file

- Absolute Address: 0x440
- Base Offset: 0x40
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x440
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x444
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## DROP_FRAG register file

- Absolute Address: 0x448
- Base Offset: 0x48
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x448
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x44C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## DROP_PROTO register file

- Absolute Address: 0x450
- Base Offset: 0x50
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x450
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x454
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## DROP_FILTER register file

- Absolute Address: 0x458
- Base Offset: 0x58
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x458
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x45C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## DROP_LEN register file

- Absolute Address: 0x460
- Base Offset: 0x60
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x460
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x464
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>
