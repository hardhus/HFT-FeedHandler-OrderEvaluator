<!---
Markdown description for SystemRDL register map.

Don't override. Generated from: hft_csr
  - firmware/rdl/hft_csr.rdl
-->

## hft_csr address map

- Absolute Address: 0x0
- Base Offset: 0x0
- Size: 0xC

<p>Host register map. 32-bit registers, 64 KiB window, SLVERR on unmapped addresses and illegal accesses.</p>

|Offset|Identifier| Name |
|------|----------|------|
|  0x0 |  GLOBAL  |Global|

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
