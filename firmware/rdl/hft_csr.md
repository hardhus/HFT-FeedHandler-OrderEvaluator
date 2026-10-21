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
| 0x200|   FEED   | MoldUDP64 feed|
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
|31:0|  version |   r  |0x50001|  — |

#### version field

<p>Major.minor of this map: 0x0005_0001 is the F5 map with the F3 FEED block. A minor step only adds registers (software for an older minor still works); a major step changes existing ones. Software checks it before use.</p>

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

## FEED register file

- Absolute Address: 0x200
- Base Offset: 0x200
- Size: 0xC0

<p>moldudp64_rx configuration, resync and counters. FEED_CTRL, SESSION_* and RESYNC_SEQ_* are a shadow like the NET block: FEED_COMMIT moves them to the net clock domain in one transfer. EXPECTED_SEQ, FEED_STATE and the MOLD_* counters (net clock domain, 64 bit, wrap; cleared only by reset) are updated by STATS_CTRL.snapshot in the same net clock cycle as the STATS counters; STATS_SEQ counts both. MOLD_<status> is at 0x0240 + 8 * status, status = mold_status_e in hft_pkg.sv.</p>

|Offset|    Identifier    |            Name            |
|------|------------------|----------------------------|
| 0x00 |     FEED_CTRL    |        Feed control        |
| 0x04 |     SESSION_0    |     Session, bytes 0..3    |
| 0x08 |     SESSION_1    |     Session, bytes 4..7    |
| 0x0C |     SESSION_2    |     Session, bytes 8..9    |
| 0x10 |   RESYNC_SEQ_LO  | Resync sequence number, low|
| 0x14 |   RESYNC_SEQ_HI  |Resync sequence number, high|
| 0x18 |    FEED_COMMIT   |           Commit           |
| 0x1C |    FEED_STATUS   |           Status           |
| 0x20 |   EXPECTED_SEQ   |       64-bit counter       |
| 0x28 |    FEED_STATE    |         Feed state         |
| 0x40 |      MOLD_OK     |       64-bit counter       |
| 0x48 |  MOLD_HEARTBEAT  |       64-bit counter       |
| 0x50 |     MOLD_EOS     |       64-bit counter       |
| 0x58 |     MOLD_DUP     |       64-bit counter       |
| 0x60 |   MOLD_DISABLED  |       64-bit counter       |
| 0x68 |  MOLD_OTHER_FEED |       64-bit counter       |
| 0x70 |    MOLD_ABORT    |       64-bit counter       |
| 0x78 |     MOLD_RUNT    |       64-bit counter       |
| 0x80 |   MOLD_SESSION   |       64-bit counter       |
| 0x88 | MOLD_UNSUPPORTED |       64-bit counter       |
| 0x90 |  MOLD_MALFORMED  |       64-bit counter       |
| 0x98 |     MOLD_MSGS    |       64-bit counter       |
| 0xA0 |   MOLD_DUP_MSGS  |       64-bit counter       |
| 0xA8 |MOLD_OVERSIZE_MSGS|       64-bit counter       |
| 0xB0 |  MOLD_GAP_EVENTS |       64-bit counter       |
| 0xB8 |   MOLD_GAP_MSGS  |       64-bit counter       |

### FEED_CTRL register

- Absolute Address: 0x200
- Base Offset: 0x0
- Size: 0x4

|Bits|  Identifier |Access|Reset|Name|
|----|-------------|------|-----|----|
|  0 |    enable   |  rw  | 0x0 |  — |
|  1 |session_check|  rw  | 0x0 |  — |
|  2 | resync_learn|  rw  | 0x0 |  — |
|15:8| filter_mask |  rw  | 0x0 |  — |

#### enable field

<p>Feed enabled. 0 after reset: every packet is counted as DISABLED until software has committed a configuration with enable = 1.</p>

#### session_check field

<p>Compare each packet's session with SESSION_0..2; a different one makes the packet SESSION.</p>

#### resync_learn field

<p>What FEED_COMMIT.resync does: 1 = learn (out of sync until the next accepted packet, which then sets the expected sequence number), 0 = set the expected sequence number to RESYNC_SEQ.</p>

#### filter_mask field

<p>Bit i: eth_ip_udp_rx filter entry i belongs to this feed (the A and B lines in the same mask).</p>

### SESSION_0 register

- Absolute Address: 0x204
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Session bytes 0..3 in wire order, byte 0 in [31:24] ("SESSION001" -&gt; 0x53455353).</p>

### SESSION_1 register

- Absolute Address: 0x208
- Base Offset: 0x8
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Session bytes 4..7, byte 4 in [31:24] ("SESSION001" -&gt; 0x494F4E30).</p>

### SESSION_2 register

- Absolute Address: 0x20C
- Base Offset: 0xC
- Size: 0x4

| Bits|Identifier|Access|Reset|Name|
|-----|----------|------|-----|----|
|31:16|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Session bytes 8 and 9, byte 8 in [31:24] ("SESSION001" -&gt; 0x3031).</p>

### RESYNC_SEQ_LO register

- Absolute Address: 0x210
- Base Offset: 0x10
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    seq   |  rw  | 0x0 |  — |

#### seq field

<p>Expected sequence number set by a resync with resync_learn = 0, bits [31:0].</p>

### RESYNC_SEQ_HI register

- Absolute Address: 0x214
- Base Offset: 0x14
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    seq   |  rw  | 0x0 |  — |

#### seq field

<p>Bits [63:32] of the resync sequence number.</p>

### FEED_COMMIT register

- Absolute Address: 0x218
- Base Offset: 0x18
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 |  commit  |   w  | 0x0 |  — |
|  1 |  resync  |   w  | 0x0 |  — |

#### commit field

<p>Write 1: send FEED_CTRL, SESSION_* and RESYNC_SEQ_* to moldudp64_rx (atomic; it takes them at the first beat of each packet). A commit while the previous one is in flight is not lost: the newest shadow is sent when the transfer is free.</p>

#### resync field

<p>Write 1: commit as above and resync the feed with resync_learn and RESYNC_SEQ of that commit. moldudp64_rx applies it between packets, or after the packet in progress. Like a self-clearing command bit, a request stays pending until a transfer carries it: it is never lost, and requests made before that merge into one resync with the newest shadow. FEED_STATUS.commit_busy reads 0 once it is in moldudp64_rx; software that wants two separate resyncs waits for that between them.</p>

### FEED_STATUS register

- Absolute Address: 0x21C
- Base Offset: 0x1C
- Size: 0x4

|Bits| Identifier|Access|Reset|Name|
|----|-----------|------|-----|----|
|  0 |commit_busy|   r  |  —  |  — |

#### commit_busy field

<p>1 while a commit or resync is pending or in flight. 0: every write before the last FEED_COMMIT is in effect in moldudp64_rx.</p>

## EXPECTED_SEQ register file

- Absolute Address: 0x220
- Base Offset: 0x20
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x220
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x224
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

### FEED_STATE register

- Absolute Address: 0x228
- Base Offset: 0x28
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 |  synced  |   r  |  —  |  — |
|  1 |    eos   |   r  |  —  |  — |

#### synced field

<p>In sync (an accepted packet set the expected sequence number), at the last snapshot.</p>

#### eos field

<p>End of session seen: set by an accepted end-of-session packet, kept by heartbeats, cleared by an accepted normal packet or a resync; at the last snapshot.</p>

## MOLD_OK register file

- Absolute Address: 0x240
- Base Offset: 0x40
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x240
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x244
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_HEARTBEAT register file

- Absolute Address: 0x248
- Base Offset: 0x48
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x248
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x24C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_EOS register file

- Absolute Address: 0x250
- Base Offset: 0x50
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x250
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x254
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_DUP register file

- Absolute Address: 0x258
- Base Offset: 0x58
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x258
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x25C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_DISABLED register file

- Absolute Address: 0x260
- Base Offset: 0x60
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x260
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x264
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_OTHER_FEED register file

- Absolute Address: 0x268
- Base Offset: 0x68
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x268
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x26C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_ABORT register file

- Absolute Address: 0x270
- Base Offset: 0x70
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x270
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x274
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_RUNT register file

- Absolute Address: 0x278
- Base Offset: 0x78
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x278
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x27C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_SESSION register file

- Absolute Address: 0x280
- Base Offset: 0x80
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x280
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x284
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_UNSUPPORTED register file

- Absolute Address: 0x288
- Base Offset: 0x88
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x288
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x28C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_MALFORMED register file

- Absolute Address: 0x290
- Base Offset: 0x90
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x290
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x294
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_MSGS register file

- Absolute Address: 0x298
- Base Offset: 0x98
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x298
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x29C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_DUP_MSGS register file

- Absolute Address: 0x2A0
- Base Offset: 0xA0
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x2A0
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x2A4
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_OVERSIZE_MSGS register file

- Absolute Address: 0x2A8
- Base Offset: 0xA8
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x2A8
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x2AC
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_GAP_EVENTS register file

- Absolute Address: 0x2B0
- Base Offset: 0xB0
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x2B0
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x2B4
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## MOLD_GAP_MSGS register file

- Absolute Address: 0x2B8
- Base Offset: 0xB8
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x2B8
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x2BC
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

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
