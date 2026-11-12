<!---
Markdown description for SystemRDL register map.

Don't override. Generated from: hft_csr
  - firmware/rdl/hft_csr.rdl
-->

## hft_csr address map

- Absolute Address: 0x0
- Base Offset: 0x0
- Size: 0xC000

<p>Host register map. 32-bit registers, 64 KiB window, SLVERR on unmapped addresses and illegal accesses.</p>

|Offset| Identifier |           Name           |
|------|------------|--------------------------|
|0x0000|   GLOBAL   |          Global          |
|0x0100|     NET    |      Network receive     |
|0x0200|    FEED    |      MoldUDP64 feed      |
|0x0400|    STATS   |        Statistics        |
|0x0600|    ITCH    |     ITCH 5.0 decoder     |
|0x0800|    RING    |      Host event ring     |
|0x0A00|    RISK    |      Pre-trade risk      |
|0x1000|  STRATEGY  |      Strategy rules      |
|0x2000|     TX     |Order transmit and mailbox|
|0x8000|   SYMBOL   |       Symbol bitmap      |
|0xA000|LOCATE_ALLOW|    Locate allow bitmap   |

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
|31:0|  version |   r  |0x50004|  — |

#### version field

<p>Major.minor of this map: 0x0005_0004 is the F5 map with the F3 FEED block, the F4 ITCH block and SYMBOL bitmap, the F6 RING block and the F7 RISK, STRATEGY and TX blocks and LOCATE_ALLOW bitmap. A minor step only adds registers (software for an older minor still works); a major step changes existing ones. Software checks it before use.</p>

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

<p>moldudp64_rx configuration, resync and counters. FEED_CTRL, SESSION_* and RESYNC_SEQ_* are a shadow like the NET block: FEED_COMMIT moves them to the net clock domain in one transfer. EXPECTED_SEQ, FEED_STATE and the MOLD_* counters (net clock domain, 64 bit, wrap; cleared only by reset) are updated by STATS_CTRL.snapshot in the same net clock cycle as the STATS counters; STATS_SEQ counts both. MOLD_<status> is at 0x0240 + 8 * status, status = mold_status_e in hft_pkg.sv. Each FEED_COMMIT write with commit or resync set adds 1 to an 8-bit wrapping count, 0 after reset. It travels with the configuration, so every ring entry carries, as cfg_epoch, the count of the commit in effect at its packet's first beat. The host keeps its own count to match them: every such write counts, also two in consecutive bus cycles, and a write that leaves byte 0 disabled does not.</p>

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

## ITCH register file

- Absolute Address: 0x600
- Base Offset: 0x600
- Size: 0x88

<p>itch_decoder configuration and counters. ITCH_CTRL is a shadow that FEED_COMMIT carries with the FEED block (one atomic commit; moldudp64_rx takes it at the first beat of each packet and the decoder applies it to every message of that packet). The ITCH_* counters (net clock domain, 64 bit, wrap; cleared only by reset) count committed messages only: those of OK, HEARTBEAT and EOS packets, like MOLD_MSGS. They are updated by STATS_CTRL.snapshot in the same net clock cycle as the STATS and MOLD_* counters. ITCH_<class> is at 0x0640 + 8 * class, class = itch_class_e in hft_pkg.sv. Between two snapshots: the sum of ITCH_<class> moves by MOLD_MSGS, and ITCH_OUT + ITCH_FILTERED + ITCH_MASKED by the sum of ITCH_<class>.</p>

|Offset|  Identifier  |     Name     |
|------|--------------|--------------|
| 0x00 |   ITCH_CTRL  | ITCH control |
| 0x40 |  ITCH_ORDER  |64-bit counter|
| 0x48 |  ITCH_TRADE  |64-bit counter|
| 0x50 |  ITCH_ADMIN  |64-bit counter|
| 0x58 | ITCH_AUCTION |64-bit counter|
| 0x60 | ITCH_UNKNOWN |64-bit counter|
| 0x68 |ITCH_MALFORMED|64-bit counter|
| 0x70 |   ITCH_OUT   |64-bit counter|
| 0x78 | ITCH_FILTERED|64-bit counter|
| 0x80 |  ITCH_MASKED |64-bit counter|

### ITCH_CTRL register

- Absolute Address: 0x600
- Base Offset: 0x0
- Size: 0x4

|Bits| Identifier |Access|Reset|Name|
|----|------------|------|-----|----|
| 5:0|  class_en  |  rw  | 0x0 |  — |
|13:8|class_filter|  rw  | 0x0 |  — |

#### class_en field

<p>Bit c: messages of class c (itch_class_e) are output; 0 = counted as MASKED. 0 after reset: no message is output until software has committed a configuration.</p>

#### class_filter field

<p>Bit c: messages of class c go through the SYMBOL bitmap; a message whose stock locate is not 0 and whose bitmap bit is 0 is counted as FILTERED. Locate 0 (market-wide messages) and messages shorter than the 11-byte header always pass.</p>

## ITCH_ORDER register file

- Absolute Address: 0x640
- Base Offset: 0x40
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x640
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x644
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## ITCH_TRADE register file

- Absolute Address: 0x648
- Base Offset: 0x48
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x648
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x64C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## ITCH_ADMIN register file

- Absolute Address: 0x650
- Base Offset: 0x50
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x650
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x654
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## ITCH_AUCTION register file

- Absolute Address: 0x658
- Base Offset: 0x58
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x658
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x65C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## ITCH_UNKNOWN register file

- Absolute Address: 0x660
- Base Offset: 0x60
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x660
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x664
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## ITCH_MALFORMED register file

- Absolute Address: 0x668
- Base Offset: 0x68
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x668
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x66C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## ITCH_OUT register file

- Absolute Address: 0x670
- Base Offset: 0x70
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x670
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x674
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## ITCH_FILTERED register file

- Absolute Address: 0x678
- Base Offset: 0x78
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x678
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x67C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## ITCH_MASKED register file

- Absolute Address: 0x680
- Base Offset: 0x80
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x680
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x684
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RING register file

- Absolute Address: 0x800
- Base Offset: 0x800
- Size: 0x64

<p>event_ring_writer configuration and state, in the host clock domain (no commit, no snapshot, except the net counters below). Host obligations: the region is 2^RING_SIZE_LOG2 entries of 128 B, aligned to 4 KiB; before EVERY enable the host fills it with zeros and writes CONS_IDX = 0. The hardware checks only CONS_IDX and the size, on the rising edge of RING_CTRL.enable: otherwise the enable is refused (RING_STATUS.enable_err) and the writer stays stopped. It cannot detect a region left dirty, or a CONS_IDX that passes the entries written. The host reads each 64 B line's ctrl byte with acquire and only then the fields, checks that both lines' phase match, and writes CONS_IDX from time to time (posted, for example every N/4 entries). Identities: at every snapshot RING_COMMIT_ENTRIES + RING_DROP_ENTRIES + RING_DROP_ORDERS + RING_ORDER_QUEUED = ITCH_OUT + MOLD_GAP_EVENTS + MOLD_EOS + ORDERS_SENT_HW + ORDERS_SENT_HOST + the sum of RISK_REJECT_<reason>; with the ring path idle RING_COMMIT_ENTRIES = RING_WRITTEN + RING_BRESP_ERR.</p>

|Offset|     Identifier    |          Name         |
|------|-------------------|-----------------------|
| 0x00 |     RING_CTRL     |      Ring control     |
| 0x04 |    RING_BASE_LO   | Ring base address, low|
| 0x08 |    RING_BASE_HI   |Ring base address, high|
| 0x0C |   RING_SIZE_LOG2  |       Ring size       |
| 0x10 |      CONS_IDX     |     Consumer index    |
| 0x14 |      PROD_IDX     |     Producer index    |
| 0x18 |    RING_STATUS    |      Ring status      |
| 0x20 |    RING_WRITTEN   |  64-bit live counter  |
| 0x28 |   RING_BRESP_ERR  |  64-bit live counter  |
| 0x40 |RING_COMMIT_ENTRIES|     64-bit counter    |
| 0x48 |   RING_DROP_PKTS  |     64-bit counter    |
| 0x50 | RING_DROP_ENTRIES |     64-bit counter    |
| 0x58 |  RING_DROP_ORDERS |     64-bit counter    |
| 0x60 | RING_ORDER_QUEUED |  Order entries queued |

### RING_CTRL register

- Absolute Address: 0x800
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 |  enable  |  rw  | 0x0 |  — |

#### enable field

<p>Writer enabled. A rising edge with CONS_IDX = 0 and RING_SIZE_LOG2 in 4..20 sets PROD_IDX to 0, takes RING_BASE_* and RING_SIZE_LOG2 (ignored until the next rising edge) and clears RING_STATUS; any other rising edge is refused. To retry after a refusal: write 0, then 1. Falling edge: bursts in flight finish, no new entry starts.</p>

### RING_BASE_LO register

- Absolute Address: 0x804
- Base Offset: 0x4
- Size: 0x4

| Bits|Identifier|Access|Reset|Name|
|-----|----------|------|-----|----|
|31:12|   base   |  rw  | 0x0 |  — |

#### base field

<p>Base address bits [31:12]; the region is 4 KiB aligned and bits [11:0] read 0.</p>

### RING_BASE_HI register

- Absolute Address: 0x808
- Base Offset: 0x8
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   base   |  rw  | 0x0 |  — |

#### base field

<p>Base address bits [63:32].</p>

### RING_SIZE_LOG2 register

- Absolute Address: 0x80C
- Base Offset: 0xC
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
| 4:0| size_log2|  rw  | 0x0 |  — |

#### size_log2 field

<p>log2 of the number of entries, 4..20. Any other value makes the next enable refused.</p>

### CONS_IDX register

- Absolute Address: 0x810
- Base Offset: 0x10
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   cons   |  rw  | 0x0 |  — |

#### cons field

<p>Entries the host has consumed: a wrapping 32-bit count, the same arithmetic as PROD_IDX. PROD_IDX - CONS_IDX = 2^RING_SIZE_LOG2 means full, and the writer waits. It must be 0 at each enable and must never pass PROD_IDX.</p>

### PROD_IDX register

- Absolute Address: 0x814
- Base Offset: 0x14
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   prod   |   r  |  —  |  — |

#### prod field

<p>Bursts started since the last accepted enable, live, wrapping. An entry counted here may still be on its way: the phase bits tell which entries are in memory.</p>

### RING_STATUS register

- Absolute Address: 0x818
- Base Offset: 0x18
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 | bresp_err|   r  |  —  |  — |
|  1 |enable_err|   r  |  —  |  — |

#### bresp_err field

<p>A write answered with a BRESP other than OKAY since the last accepted enable. The write is not retried; RING_BRESP_ERR counts them.</p>

#### enable_err field

<p>The last rising edge of RING_CTRL.enable was refused: CONS_IDX was not 0 or RING_SIZE_LOG2 was not in 4..20. Stays set until an accepted enable.</p>

## RING_WRITTEN register file

- Absolute Address: 0x820
- Base Offset: 0x20
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x820
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0], live. To read the pair: read HI, LO, HI again, and repeat if the two HI differ.</p>

### HI register

- Absolute Address: 0x824
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32], live.</p>

## RING_BRESP_ERR register file

- Absolute Address: 0x828
- Base Offset: 0x28
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x828
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0], live. To read the pair: read HI, LO, HI again, and repeat if the two HI differ.</p>

### HI register

- Absolute Address: 0x82C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32], live.</p>

## RING_COMMIT_ENTRIES register file

- Absolute Address: 0x840
- Base Offset: 0x40
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x840
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x844
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RING_DROP_PKTS register file

- Absolute Address: 0x848
- Base Offset: 0x48
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x848
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x84C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RING_DROP_ENTRIES register file

- Absolute Address: 0x850
- Base Offset: 0x50
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x850
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x854
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RING_DROP_ORDERS register file

- Absolute Address: 0x858
- Base Offset: 0x58
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x858
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x85C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

### RING_ORDER_QUEUED register

- Absolute Address: 0x860
- Base Offset: 0x60
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
| 4:0|  queued  |   r  |  —  |  — |

#### queued field

<p>ORDER_SENT and RISK_REJECT entries counted by ORDERS_SENT_* and RISK_REJECT_* and waiting in event_packer's order queue (0..16), at the last snapshot. It keeps the identity above in every snapshot.</p>

## RISK register file

- Absolute Address: 0xA00
- Base Offset: 0xA00
- Size: 0xB0

<p>risk_check limits, kill switch, order budget and counters. MAX_QTY .. RATE_BURST are a shadow that RISK_COMMIT moves to the net clock domain in one piece. RISK_COMMIT and BUDGET_ADD are commands: they go to the net clock domain in bus order with the STRATEGY and TX commands, after every MBOX_SEND written before them has been decided and after RISK_CTRL and TX_CTRL written before them are in effect, and their write response comes once they are in effect. While one is in flight every other register access waits. The counters (net clock domain, 64 bit, wrap; cleared only by reset) and BUDGET are updated by STATS_CTRL.snapshot in the same net clock cycle as the other snapshot counters. RISK_REJECT_<reason> is at 0x0A60 + 8 * (reason - 1), reason = risk_reason_e in hft_pkg.sv; it counts the rejects not written to the ring too (RING_DROP_ORDERS).</p>

|Offset|      Identifier     |         Name         |
|------|---------------------|----------------------|
| 0x00 |       MAX_QTY       |   Maximum quantity   |
| 0x04 |   MAX_NOTIONAL_LO   | Maximum notional, low|
| 0x08 |   MAX_NOTIONAL_HI   |Maximum notional, high|
| 0x0C |     RATE_REFILL     |     Token refill     |
| 0x10 |     RATE_PERIOD     |     Token period     |
| 0x14 |      RATE_BURST     |   Token bucket size  |
| 0x18 |     RISK_COMMIT     |      Risk commit     |
| 0x1C |      RISK_CTRL      |     Risk control     |
| 0x20 |      BUDGET_ADD     |   Add to the budget  |
| 0x24 |        BUDGET       |        Budget        |
| 0x40 |    ORDERS_SENT_HW   |    64-bit counter    |
| 0x48 |   ORDERS_SENT_HOST  |    64-bit counter    |
| 0x60 |   RISK_REJECT_KILL  |    64-bit counter    |
| 0x68 |  RISK_REJECT_TX_OFF |    64-bit counter    |
| 0x70 | RISK_REJECT_INVALID |    64-bit counter    |
| 0x78 |  RISK_REJECT_LOCATE |    64-bit counter    |
| 0x80 |   RISK_REJECT_QTY   |    64-bit counter    |
| 0x88 | RISK_REJECT_NOTIONAL|    64-bit counter    |
| 0x90 |  RISK_REJECT_BUDGET |    64-bit counter    |
| 0x98 |   RISK_REJECT_RATE  |    64-bit counter    |
| 0xA0 | RISK_REJECT_TX_BUSY |    64-bit counter    |
| 0xA8 |RISK_REJECT_RING_FULL|    64-bit counter    |

### MAX_QTY register

- Absolute Address: 0xA00
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|  max_qty |  rw  | 0x0 |  — |

#### max_qty field

<p>An order with qty &gt; max_qty is rejected (QTY). 0 after reset.</p>

### MAX_NOTIONAL_LO register

- Absolute Address: 0xA04
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |  rw  | 0x0 |  — |

#### value field

<p>Bits [31:0] of the maximum qty * price (price in ITCH Price(4) units). An order above it is rejected (NOTIONAL).</p>

### MAX_NOTIONAL_HI register

- Absolute Address: 0xA08
- Base Offset: 0x8
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |  rw  | 0x0 |  — |

#### value field

<p>Bits [63:32] of the maximum notional.</p>

### RATE_REFILL register

- Absolute Address: 0xA0C
- Base Offset: 0xC
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|  refill  |  rw  | 0x0 |  — |

#### refill field

<p>Tokens added every RATE_PERIOD net clock cycles, up to RATE_BURST.</p>

### RATE_PERIOD register

- Absolute Address: 0xA10
- Base Offset: 0x10
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|  period  |  rw  | 0x0 |  — |

#### period field

<p>Net clock cycles between refills; 0 = no refill (the bucket only empties).</p>

### RATE_BURST register

- Absolute Address: 0xA14
- Base Offset: 0x14
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   burst  |  rw  | 0x0 |  — |

#### burst field

<p>Bucket size. Each RISK_COMMIT fills the bucket to it and restarts the period. An accepted order takes one token; with none it is rejected (RATE).</p>

### RISK_COMMIT register

- Absolute Address: 0xA18
- Base Offset: 0x18
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 |  commit  |   w  |  —  |  — |

#### commit field

<p>Write 1: command that sends MAX_QTY .. RATE_BURST to risk_check in one piece; the write response comes once it is in effect. A write with 0 does nothing.</p>

### RISK_CTRL register

- Absolute Address: 0xA1C
- Base Offset: 0x1C
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 |   kill   |  rw  | 0x1 |  — |

#### kill field

<p>Kill switch, 1 after reset. Not a shadow: it reaches the net clock domain through a synchronizer, without a commit. While 1 every order is rejected (KILL); its rise cancels the TX queue and disarms every rule. Software releases it last, after the whole configuration (software contract after reset).</p>

### BUDGET_ADD register

- Absolute Address: 0xA20
- Base Offset: 0x20
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|  amount  |   w  |  —  |  — |

#### amount field

<p>Command: adds the written value to the order budget (saturating at 2^32 - 1); the write response comes once it is in effect. Each accepted order uses one unit; with 0 an order is rejected (BUDGET). The budget is 0 after reset.</p>

### BUDGET register

- Absolute Address: 0xA24
- Base Offset: 0x24
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|  budget  |   r  |  —  |  — |

#### budget field

<p>Order budget left, at the last snapshot.</p>

## ORDERS_SENT_HW register file

- Absolute Address: 0xA40
- Base Offset: 0x40
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xA40
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xA44
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## ORDERS_SENT_HOST register file

- Absolute Address: 0xA48
- Base Offset: 0x48
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xA48
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xA4C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RISK_REJECT_KILL register file

- Absolute Address: 0xA60
- Base Offset: 0x60
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xA60
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xA64
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RISK_REJECT_TX_OFF register file

- Absolute Address: 0xA68
- Base Offset: 0x68
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xA68
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xA6C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RISK_REJECT_INVALID register file

- Absolute Address: 0xA70
- Base Offset: 0x70
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xA70
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xA74
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RISK_REJECT_LOCATE register file

- Absolute Address: 0xA78
- Base Offset: 0x78
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xA78
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xA7C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RISK_REJECT_QTY register file

- Absolute Address: 0xA80
- Base Offset: 0x80
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xA80
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xA84
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RISK_REJECT_NOTIONAL register file

- Absolute Address: 0xA88
- Base Offset: 0x88
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xA88
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xA8C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RISK_REJECT_BUDGET register file

- Absolute Address: 0xA90
- Base Offset: 0x90
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xA90
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xA94
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RISK_REJECT_RATE register file

- Absolute Address: 0xA98
- Base Offset: 0x98
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xA98
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xA9C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RISK_REJECT_TX_BUSY register file

- Absolute Address: 0xAA0
- Base Offset: 0xA0
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xAA0
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xAA4
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RISK_REJECT_RING_FULL register file

- Absolute Address: 0xAA8
- Base Offset: 0xA8
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0xAA8
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0xAAC
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## STRATEGY register file

- Absolute Address: 0x1000
- Base Offset: 0x1000
- Size: 0x80

<p>strategy_engine's 8 rules, written indirectly: RULE_SEL picks a rule, RULE_CTRL .. RULE_OFFSET are one shadow, RULE_COMMIT replaces the selected rule with it in one piece and disarms it. Reads return the shadow, not the rule in effect. RULE_COMMIT, RULE_ARM and RULE_DISARM are commands, in bus order with the RISK and TX commands (see RISK); their write response comes once they are in effect. RULE_ARMED and RULE_FIRED_<n> (net clock domain) are updated by STATS_CTRL.snapshot with the other snapshot counters.</p>

|Offset|   Identifier  |        Name        |
|------|---------------|--------------------|
| 0x00 |    RULE_SEL   |     Rule select    |
| 0x04 |   RULE_CTRL   |    Rule control    |
| 0x08 |  RULE_LOCATE  |     Rule locate    |
| 0x0C | RULE_THRESHOLD|Rule price threshold|
| 0x10 |RULE_MIN_SHARES| Rule minimum shares|
| 0x14 |    RULE_QTY   |    Rule quantity   |
| 0x18 |  RULE_OFFSET  |  Rule price offset |
| 0x1C |  RULE_COMMIT  |     Rule commit    |
| 0x20 |    RULE_ARM   |      Arm rules     |
| 0x24 |  RULE_DISARM  |    Disarm rules    |
| 0x28 |  RULE_STATUS  |     Rule status    |
| 0x2C |   RULE_ARMED  |     Armed rules    |
| 0x40 |  RULE_FIRED_0 |   64-bit counter   |
| 0x48 |  RULE_FIRED_1 |   64-bit counter   |
| 0x50 |  RULE_FIRED_2 |   64-bit counter   |
| 0x58 |  RULE_FIRED_3 |   64-bit counter   |
| 0x60 |  RULE_FIRED_4 |   64-bit counter   |
| 0x68 |  RULE_FIRED_5 |   64-bit counter   |
| 0x70 |  RULE_FIRED_6 |   64-bit counter   |
| 0x78 |  RULE_FIRED_7 |   64-bit counter   |

### RULE_SEL register

- Absolute Address: 0x1000
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
| 2:0|    idx   |  rw  | 0x0 |  — |

#### idx field

<p>The rule RULE_COMMIT writes.</p>

### RULE_CTRL register

- Absolute Address: 0x1004
- Base Offset: 0x4
- Size: 0x4

| Bits|     Identifier     |Access|Reset|Name|
|-----|--------------------|------|-----|----|
|  0  |       enable       |  rw  | 0x0 |  — |
| 3:1 |      msg_mask      |  rw  | 0x0 |  — |
| 5:4 |     side_match     |  rw  | 0x0 |  — |
|  6  |      price_ge      |  rw  | 0x0 |  — |
| 9:8 |     action_side    |  rw  | 0x0 |  — |
|  10 |       qty_min      |  rw  | 0x0 |  — |
|  11 |price_threshold_mode|  rw  | 0x0 |  — |
|23:16|         tif        |  rw  | 0x0 |  — |

#### enable field

<p>Rule enabled.</p>

#### msg_mask field

<p>Message types that can fire it: bit 0 A, bit 1 F, bit 2 P.</p>

#### side_match field

<p>Event sides that match: bit 0 B, bit 1 S.</p>

#### price_ge field

<p>0: event price &lt;= RULE_THRESHOLD matches, 1: &gt;=.</p>

#### action_side field

<p>Order side: 0 buy, 1 sell, 2 the event's opposite; 3 is a static error.</p>

#### qty_min field

<p>0: the order quantity is RULE_QTY; 1: min(event shares, RULE_QTY).</p>

#### price_threshold_mode field

<p>0: the order price is the event price; 1: RULE_THRESHOLD + RULE_OFFSET.</p>

#### tif field

<p>OUCH Time In Force code of the order (0, 3, 5 or E; any other is a static error).</p>

### RULE_LOCATE register

- Absolute Address: 0x1008
- Base Offset: 0x8
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|15:0|  locate  |  rw  | 0x0 |  — |

#### locate field

<p>Stock locate the rule watches; 0 is a static error. The rule sees only the messages itch_decoder outputs: its locate needs the ORDER or TRADE class enabled in ITCH_CTRL and, if that class is filtered, its SYMBOL bit set, else the rule never fires.</p>

### RULE_THRESHOLD register

- Absolute Address: 0x100C
- Base Offset: 0xC
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0| threshold|  rw  | 0x0 |  — |

#### threshold field

<p>Price threshold, ITCH Price(4).</p>

### RULE_MIN_SHARES register

- Absolute Address: 0x1010
- Base Offset: 0x10
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|min_shares|  rw  | 0x0 |  — |

#### min_shares field

<p>An event matches only with shares &gt;= this.</p>

### RULE_QTY register

- Absolute Address: 0x1014
- Base Offset: 0x14
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    qty   |  rw  | 0x0 |  — |

#### qty field

<p>Order quantity, or its maximum with qty_min = 1.</p>

### RULE_OFFSET register

- Absolute Address: 0x1018
- Base Offset: 0x18
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|  offset  |  rw  | 0x0 |  — |

#### offset field

<p>Signed offset added to RULE_THRESHOLD with price_threshold_mode = 1.</p>

### RULE_COMMIT register

- Absolute Address: 0x101C
- Base Offset: 0x1C
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 |  commit  |   w  |  —  |  — |

#### commit field

<p>Write 1: command that replaces rule RULE_SEL with the shadow and disarms it; the write response comes once it is in effect, and RULE_STATUS then shows its static error. A write with 0 does nothing.</p>

### RULE_ARM register

- Absolute Address: 0x1020
- Base Offset: 0x20
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
| 7:0|   mask   |   w  |  —  |  — |

#### mask field

<p>Command: arms the rules whose bit is 1; the write response comes once it is in effect.</p>

### RULE_DISARM register

- Absolute Address: 0x1024
- Base Offset: 0x24
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
| 7:0|   mask   |   w  |  —  |  — |

#### mask field

<p>Command: disarms the rules whose bit is 1; the write response comes once it is in effect.</p>

### RULE_STATUS register

- Absolute Address: 0x1028
- Base Offset: 0x28
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
| 7:0|   error  |   r  |  —  |  — |

#### error field

<p>Bit n: rule n has a static error and never fires (locate 0, a TIF the core does not send, action_side 3, quantity 0 or &gt;= 1,000,000, threshold + offset outside the exchange's price range). As of the last command's response; 0xFF after reset (every rule is cleared).</p>

### RULE_ARMED register

- Absolute Address: 0x102C
- Base Offset: 0x2C
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
| 7:0|   armed  |   r  |  —  |  — |

#### armed field

<p>Bit n: rule n armed, at the last snapshot.</p>

## RULE_FIRED_0 register file

- Absolute Address: 0x1040
- Base Offset: 0x40
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x1040
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x1044
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RULE_FIRED_1 register file

- Absolute Address: 0x1048
- Base Offset: 0x48
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x1048
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x104C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RULE_FIRED_2 register file

- Absolute Address: 0x1050
- Base Offset: 0x50
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x1050
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x1054
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RULE_FIRED_3 register file

- Absolute Address: 0x1058
- Base Offset: 0x58
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x1058
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x105C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RULE_FIRED_4 register file

- Absolute Address: 0x1060
- Base Offset: 0x60
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x1060
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x1064
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RULE_FIRED_5 register file

- Absolute Address: 0x1068
- Base Offset: 0x68
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x1068
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x106C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RULE_FIRED_6 register file

- Absolute Address: 0x1070
- Base Offset: 0x70
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x1070
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x1074
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## RULE_FIRED_7 register file

- Absolute Address: 0x1078
- Base Offset: 0x78
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x1078
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0] at the last snapshot.</p>

### HI register

- Absolute Address: 0x107C
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32] at the last snapshot.</p>

## TX register file

- Absolute Address: 0x2000
- Base Offset: 0x2000
- Size: 0x98

<p>order_tx_builder configuration, the UserRefNum counter and the host order mailbox. TX_OUCH and TX_TEMPLATE_* are a shadow that TX_COMMIT sends in one piece; TX_COMMIT and TX_USERREF_NEXT are commands in bus order with the RISK and STRATEGY commands (see RISK). MBOX_* fields are a shadow that MBOX_SEND copies into a 16-entry FIFO to the net clock domain (posted).</p>

|Offset|    Identifier   |            Name           |
|------|-----------------|---------------------------|
| 0x00 |     TX_CTRL     |      Transmit control     |
| 0x04 |     TX_OUCH     |       OUCH constants      |
| 0x08 |    TX_COMMIT    |      Transmit commit      |
| 0x0C |    TX_STATUS    |      Transmit status      |
| 0x10 | TX_USERREF_NEXT |  Set the next UserRefNum  |
| 0x14 |    TX_USERREF   |      Next UserRefNum      |
| 0x20 |  TX_TEMPLATE[0] |       Frame template      |
| 0x24 |  TX_TEMPLATE[1] |       Frame template      |
| 0x28 |  TX_TEMPLATE[2] |       Frame template      |
| 0x2C |  TX_TEMPLATE[3] |       Frame template      |
| 0x30 |  TX_TEMPLATE[4] |       Frame template      |
| 0x34 |  TX_TEMPLATE[5] |       Frame template      |
| 0x38 |  TX_TEMPLATE[6] |       Frame template      |
| 0x3C |  TX_TEMPLATE[7] |       Frame template      |
| 0x40 |  TX_TEMPLATE[8] |       Frame template      |
| 0x44 |  TX_TEMPLATE[9] |       Frame template      |
| 0x48 | TX_TEMPLATE_TAIL| Frame template, last bytes|
| 0x50 |    MBOX_SIDE    |        Mailbox side       |
| 0x54 |     MBOX_QTY    |      Mailbox quantity     |
| 0x58 |    MBOX_PRICE   |       Mailbox price       |
| 0x5C |   MBOX_LOCATE   |       Mailbox locate      |
| 0x60 |   MBOX_STOCK_0  | Mailbox stock, bytes 0..3 |
| 0x64 |   MBOX_STOCK_1  | Mailbox stock, bytes 4..7 |
| 0x68 |     MBOX_TIF    |   Mailbox time in force   |
| 0x70 | MBOX_CLORDID[0] |      Mailbox ClOrdID      |
| 0x74 | MBOX_CLORDID[1] |      Mailbox ClOrdID      |
| 0x78 | MBOX_CLORDID[2] |      Mailbox ClOrdID      |
| 0x7C |MBOX_CLORDID_TAIL|Mailbox ClOrdID, last bytes|
| 0x80 |    MBOX_SEND    |        Mailbox send       |
| 0x84 |    MBOX_SPACE   |       Mailbox space       |
| 0x88 |   MBOX_STATUS   |       Mailbox status      |
| 0x90 |    MBOX_DROP    |    64-bit live counter    |

### TX_CTRL register

- Absolute Address: 0x2000
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 |   tx_en  |  rw  | 0x0 |  — |

#### tx_en field

<p>Order transmit enabled, 0 after reset. Not a shadow: it reaches the net clock domain through a synchronizer. In effect only after an accepted TX_COMMIT. While 0 every order is rejected (TX_OFF); its fall cancels the TX queue.</p>

### TX_OUCH register

- Absolute Address: 0x2004
- Base Offset: 0x4
- Size: 0x4

| Bits|Identifier|Access|Reset|Name|
|-----|----------|------|-----|----|
| 7:0 |  display |  rw  | 0x0 |  — |
| 15:8| capacity |  rw  | 0x0 |  — |
|23:16|    ise   |  rw  | 0x0 |  — |
|31:24|cross_type|  rw  | 0x0 |  — |

#### display field

<p>Display: Y, N or A.</p>

#### capacity field

<p>Capacity: A, P, R or O.</p>

#### ise field

<p>Intermarket Sweep Eligibility: N only.</p>

#### cross_type field

<p>CrossType: N only.</p>

### TX_COMMIT register

- Absolute Address: 0x2008
- Base Offset: 0x8
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 |  commit  |   w  |  —  |  — |

#### commit field

<p>Write 1: command that sends TX_OUCH and TX_TEMPLATE_* to order_tx_builder; the write response comes once it is decided. Accepted only while the order path is closed (RISK_CTRL.kill = 1 or TX_CTRL.tx_en = 0) and the values are valid (OUCH constants as above; template: EtherType 0x0800, 0x45, total length 78, flags/fragment 0x4000, protocol 17, a correct IPv4 header checksum, UDP length 58, UDP checksum 0); else refused and the previous configuration stays. TX_STATUS shows the outcome. A write with 0 does nothing.</p>

### TX_STATUS register

- Absolute Address: 0x200C
- Base Offset: 0xC
- Size: 0x4

|Bits|   Identifier  |Access|Reset|Name|
|----|---------------|------|-----|----|
|  0 |   cfg_valid   |   r  |  —  |  — |
|  1 |  cfg_refused  |   r  |  —  |  — |
|  2 |userref_ignored|   r  |  —  |  — |

#### cfg_valid field

<p>A TX_COMMIT has been accepted since reset (needed for transmit).</p>

#### cfg_refused field

<p>The last TX_COMMIT was refused. Cleared by an accepted one; 0 after reset.</p>

#### userref_ignored field

<p>The last TX_USERREF_NEXT write was ignored (kill not 1, orders still queued, or 0). Cleared by one that takes effect; 0 after reset.</p>

### TX_USERREF_NEXT register

- Absolute Address: 0x2010
- Base Offset: 0x10
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   w  |  —  |  — |

#### value field

<p>Command: sets the UserRefNum the next order takes; the write response comes once it is decided. It takes effect only while RISK_CTRL.kill = 1, no order is queued and the value is not 0, else TX_STATUS.userref_ignored. Software writes it before releasing kill (software contract after reset).</p>

### TX_USERREF register

- Absolute Address: 0x2014
- Base Offset: 0x14
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>The UserRefNum the next order takes, live (read through the command channel, in bus order); 1 after reset, 0 once none is left (2^32).</p>

### TX_TEMPLATE register

- Absolute Address: 0x2020
- Base Offset: 0x20
- Size: 0x4
- Array Dimensions: [10]
- Array Stride: 0x4
- Total Size: 0x28

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Template bytes 4i .. 4i+3 of the 42-byte Ethernet/IPv4/UDP header, byte 4i in [31:24] (wire order).</p>

### TX_TEMPLATE register

- Absolute Address: 0x2024
- Base Offset: 0x20
- Size: 0x4
- Array Dimensions: [10]
- Array Stride: 0x4
- Total Size: 0x28

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Template bytes 4i .. 4i+3 of the 42-byte Ethernet/IPv4/UDP header, byte 4i in [31:24] (wire order).</p>

### TX_TEMPLATE register

- Absolute Address: 0x2028
- Base Offset: 0x20
- Size: 0x4
- Array Dimensions: [10]
- Array Stride: 0x4
- Total Size: 0x28

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Template bytes 4i .. 4i+3 of the 42-byte Ethernet/IPv4/UDP header, byte 4i in [31:24] (wire order).</p>

### TX_TEMPLATE register

- Absolute Address: 0x202C
- Base Offset: 0x20
- Size: 0x4
- Array Dimensions: [10]
- Array Stride: 0x4
- Total Size: 0x28

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Template bytes 4i .. 4i+3 of the 42-byte Ethernet/IPv4/UDP header, byte 4i in [31:24] (wire order).</p>

### TX_TEMPLATE register

- Absolute Address: 0x2030
- Base Offset: 0x20
- Size: 0x4
- Array Dimensions: [10]
- Array Stride: 0x4
- Total Size: 0x28

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Template bytes 4i .. 4i+3 of the 42-byte Ethernet/IPv4/UDP header, byte 4i in [31:24] (wire order).</p>

### TX_TEMPLATE register

- Absolute Address: 0x2034
- Base Offset: 0x20
- Size: 0x4
- Array Dimensions: [10]
- Array Stride: 0x4
- Total Size: 0x28

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Template bytes 4i .. 4i+3 of the 42-byte Ethernet/IPv4/UDP header, byte 4i in [31:24] (wire order).</p>

### TX_TEMPLATE register

- Absolute Address: 0x2038
- Base Offset: 0x20
- Size: 0x4
- Array Dimensions: [10]
- Array Stride: 0x4
- Total Size: 0x28

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Template bytes 4i .. 4i+3 of the 42-byte Ethernet/IPv4/UDP header, byte 4i in [31:24] (wire order).</p>

### TX_TEMPLATE register

- Absolute Address: 0x203C
- Base Offset: 0x20
- Size: 0x4
- Array Dimensions: [10]
- Array Stride: 0x4
- Total Size: 0x28

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Template bytes 4i .. 4i+3 of the 42-byte Ethernet/IPv4/UDP header, byte 4i in [31:24] (wire order).</p>

### TX_TEMPLATE register

- Absolute Address: 0x2040
- Base Offset: 0x20
- Size: 0x4
- Array Dimensions: [10]
- Array Stride: 0x4
- Total Size: 0x28

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Template bytes 4i .. 4i+3 of the 42-byte Ethernet/IPv4/UDP header, byte 4i in [31:24] (wire order).</p>

### TX_TEMPLATE register

- Absolute Address: 0x2044
- Base Offset: 0x20
- Size: 0x4
- Array Dimensions: [10]
- Array Stride: 0x4
- Total Size: 0x28

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Template bytes 4i .. 4i+3 of the 42-byte Ethernet/IPv4/UDP header, byte 4i in [31:24] (wire order).</p>

### TX_TEMPLATE_TAIL register

- Absolute Address: 0x2048
- Base Offset: 0x48
- Size: 0x4

| Bits|Identifier|Access|Reset|Name|
|-----|----------|------|-----|----|
|31:16|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Template bytes 40 and 41, byte 40 in [31:24].</p>

### MBOX_SIDE register

- Absolute Address: 0x2050
- Base Offset: 0x50
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
| 7:0|   side   |  rw  | 0x0 |  — |

#### side field

<p>OUCH Side: B or S (any other is rejected INVALID).</p>

### MBOX_QTY register

- Absolute Address: 0x2054
- Base Offset: 0x54
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|    qty   |  rw  | 0x0 |  — |

#### qty field

<p>Order quantity.</p>

### MBOX_PRICE register

- Absolute Address: 0x2058
- Base Offset: 0x58
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   price  |  rw  | 0x0 |  — |

#### price field

<p>Limit price, ITCH Price(4).</p>

### MBOX_LOCATE register

- Absolute Address: 0x205C
- Base Offset: 0x5C
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|15:0|  locate  |  rw  | 0x0 |  — |

#### locate field

<p>Stock locate (for LOCATE_ALLOW and the ring entry; matching it with MBOX_STOCK_* is the host's rule).</p>

### MBOX_STOCK_0 register

- Absolute Address: 0x2060
- Base Offset: 0x60
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Symbol bytes 0..3 in wire order, byte 0 in [31:24] (space padded, as ITCH Stock).</p>

### MBOX_STOCK_1 register

- Absolute Address: 0x2064
- Base Offset: 0x64
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>Symbol bytes 4..7, byte 4 in [31:24].</p>

### MBOX_TIF register

- Absolute Address: 0x2068
- Base Offset: 0x68
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
| 7:0|    tif   |  rw  | 0x0 |  — |

#### tif field

<p>OUCH Time In Force code.</p>

### MBOX_CLORDID register

- Absolute Address: 0x2070
- Base Offset: 0x70
- Size: 0x4
- Array Dimensions: [3]
- Array Stride: 0x4
- Total Size: 0xC

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>ClOrdID bytes 4i .. 4i+3, byte 4i in [31:24]; every byte in [0-9A-Za-z ] (else INVALID).</p>

### MBOX_CLORDID register

- Absolute Address: 0x2074
- Base Offset: 0x70
- Size: 0x4
- Array Dimensions: [3]
- Array Stride: 0x4
- Total Size: 0xC

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>ClOrdID bytes 4i .. 4i+3, byte 4i in [31:24]; every byte in [0-9A-Za-z ] (else INVALID).</p>

### MBOX_CLORDID register

- Absolute Address: 0x2078
- Base Offset: 0x70
- Size: 0x4
- Array Dimensions: [3]
- Array Stride: 0x4
- Total Size: 0xC

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>ClOrdID bytes 4i .. 4i+3, byte 4i in [31:24]; every byte in [0-9A-Za-z ] (else INVALID).</p>

### MBOX_CLORDID_TAIL register

- Absolute Address: 0x207C
- Base Offset: 0x7C
- Size: 0x4

| Bits|Identifier|Access|Reset|Name|
|-----|----------|------|-----|----|
|31:16|   bytes  |  rw  | 0x0 |  — |

#### bytes field

<p>ClOrdID bytes 12 and 13, byte 12 in [31:24].</p>

### MBOX_SEND register

- Absolute Address: 0x2080
- Base Offset: 0x80
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 |   send   |   w  | 0x0 |  — |

#### send field

<p>Write 1: copy MBOX_SIDE .. MBOX_CLORDID_TAIL into the mailbox FIFO (posted). With the FIFO full it is dropped: MBOX_STATUS.dropped and MBOX_DROP. Each such write sends once, also two in consecutive bus cycles.</p>

### MBOX_SPACE register

- Absolute Address: 0x2084
- Base Offset: 0x84
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
| 4:0|   space  |   r  |  —  |  — |

#### space field

<p>Free FIFO entries, live (0 during reset): this many MBOX_SEND writes from now on all fit.</p>

### MBOX_STATUS register

- Absolute Address: 0x2088
- Base Offset: 0x88
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|  0 |  dropped |   r  |  —  |  — |

#### dropped field

<p>The last MBOX_SEND was dropped. Cleared by one that fits; 0 after reset.</p>

## MBOX_DROP register file

- Absolute Address: 0x2090
- Base Offset: 0x90
- Size: 0x8

|Offset|Identifier|   Name  |
|------|----------|---------|
|  0x0 |    LO    | Low word|
|  0x4 |    HI    |High word|

### LO register

- Absolute Address: 0x2090
- Base Offset: 0x0
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [31:0], live. To read the pair: read HI, LO, HI again, and repeat if the two HI differ.</p>

### HI register

- Absolute Address: 0x2094
- Base Offset: 0x4
- Size: 0x4

|Bits|Identifier|Access|Reset|Name|
|----|----------|------|-----|----|
|31:0|   value  |   r  |  —  |  — |

#### value field

<p>Counter bits [63:32], live.</p>

## SYMBOL memory

- Absolute Address: 0x8000
- Base Offset: 0x8000
- Size: 0x2000

<p>Stock-locate subscription bitmap of itch_decoder: bit i of word w is stock locate 32 * w + i (1 = subscribed). Written only by software; the hardware never writes it. After a reset the hardware clears every word (2048 net clock cycles, about 13 us); an access in that time waits until the clearing is done, so the bitmap reads 0 after reset. A write's response (BRESP) comes once it is in effect: every message looked up after it sees the new value. Software keeps its own copy and rewrites all 2048 words after every reset (see the software contract after reset).</p>

No supported members.


## LOCATE_ALLOW memory

- Absolute Address: 0xA000
- Base Offset: 0xA000
- Size: 0x2000

<p>risk_check's allow bitmap: bit i of word w is stock locate 32 * w + i (1 = orders allowed; 0 = rejected LOCATE). Written only by software. After a reset the hardware clears every word (2048 net clock cycles); an access in that time waits until the clearing is done. An access is in bus order with the RISK, STRATEGY and TX commands, and a write's response comes once it is in effect: every order decided after it sees the new value. Software keeps its own copy and rewrites it after every reset (software contract after reset).</p>

No supported members.

