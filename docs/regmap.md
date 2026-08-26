# pktforge AXI-Lite register map

Data width: 32 bits. Address width: 8 bits (256 bytes, 64 registers). All accesses are 32-bit aligned. Byte strobes (`wstrb`) honored per byte lane. Unaligned accesses return `SLVERR`. Writes to read-only registers return `OKAY` and are silently dropped. Reads/writes to reserved addresses return `SLVERR`.

Endianness: register fields are stored in host order (little-endian on the AXI bus). Fields that map to network-byte-order wire bits (IP addresses, ports) are byte-swapped by the header builder before transmission.

## Register summary

| Offset | Name              | Access | Reset       | Purpose |
|--------|-------------------|--------|-------------|---------|
| 0x00   | ID                | RO     | 0x504B5446  | Magic 'PKTF', identifies core |
| 0x04   | VERSION           | RO     | 0x00010000  | MAJOR[31:16].MINOR[15:0] |
| 0x08   | CTRL              | RW1S   | 0x00000000  | Start/stop/one-shot triggers |
| 0x0C   | STATUS            | RO     | 0x00000000  | Running/done/error flags |
| 0x10   | FRAME_TYPE        | RW     | 0x00000000  | 0=RoCEv2, 1=TCP+HTTP |
| 0x14   | ETH_SRC_MAC_LO    | RW     | 0x00000002  | src MAC bytes 3..0 |
| 0x18   | ETH_SRC_MAC_HI    | RW     | 0x00000000  | src MAC bytes 5..4 in [15:0] |
| 0x1C   | ETH_DST_MAC_LO    | RW     | 0x00000002  | dst MAC bytes 3..0 |
| 0x20   | ETH_DST_MAC_HI    | RW     | 0x00000000  | dst MAC bytes 5..4 in [15:0] |
| 0x24   | ETH_VLAN          | RW     | 0x00000000  | VLAN TCI + enable |
| 0x28   | IP_SRC            | RW     | 0x0A000001  | src IPv4, byte 0 in [31:24] |
| 0x2C   | IP_DST            | RW     | 0x0A000002  | dst IPv4, byte 0 in [31:24] |
| 0x30   | IP_MISC           | RW     | 0x00000040  | TTL / DSCP / ECN |
| 0x34   | TRANSPORT_PORTS   | RW     | 0x12B78000  | src port [15:0], dst port [31:16] |
| 0x38   | ROCE_OP_PKEY      | RW     | 0x00FFFF64  | BTH opcode + pkey |
| 0x3C   | ROCE_DEST_QP      | RW     | 0x00000010  | dest QPN [23:0] |
| 0x40   | ROCE_PSN_ACK      | RW     | 0x00000000  | PSN start [23:0], ack_req [24] |
| 0x50   | SWEEP_SIP_MIN     | RW     | 0x00000000  | sweep src IP min |
| 0x54   | SWEEP_SIP_MAX     | RW     | 0x00000000  | sweep src IP max |
| 0x58   | SWEEP_SIP_STEP    | RW     | 0x00000001  | sweep src IP step, 0 disables |
| 0x60   | SWEEP_DIP_MIN     | RW     | 0x00000000  | sweep dst IP min |
| 0x64   | SWEEP_DIP_MAX     | RW     | 0x00000000  | sweep dst IP max |
| 0x68   | SWEEP_DIP_STEP    | RW     | 0x00000000  | sweep dst IP step, 0 disables |
| 0x70   | SWEEP_SPORT_MIN   | RW     | 0x00000000  | sweep src port min |
| 0x74   | SWEEP_SPORT_MAX   | RW     | 0x00000000  | sweep src port max |
| 0x78   | SWEEP_SPORT_STEP  | RW     | 0x00000000  | sweep src port step |
| 0x80   | SWEEP_DPORT_MIN   | RW     | 0x00000000  | sweep dst port min |
| 0x84   | SWEEP_DPORT_MAX   | RW     | 0x00000000  | sweep dst port max |
| 0x88   | SWEEP_DPORT_STEP  | RW     | 0x00000000  | sweep dst port step |
| 0x90   | SWEEP_SIZE_MIN    | RW     | 0x00000040  | sweep frame size min (bytes) |
| 0x94   | SWEEP_SIZE_MAX    | RW     | 0x00000040  | sweep frame size max (bytes) |
| 0x98   | SWEEP_SIZE_STEP   | RW     | 0x00000000  | sweep frame size step |
| 0xA0   | RATE_MODE         | RW     | 0x00000000  | 0=line %, 1=IFG bytes |
| 0xA4   | RATE_LINE_PERCENT | RW     | 0x00000064  | 1..100 |
| 0xA8   | RATE_IFG_BYTES    | RW     | 0x0000000C  | IFG in bytes, when mode=1 |
| 0xAC   | OUTPUT_OPTS       | RW     | 0x00000003  | bit0=append_fcs, bit1=append_icrc |
| 0xB0   | PACKET_COUNT      | RW     | 0x00000000  | 0 = free-running |
| 0xB4   | SEED              | RW     | 0x00000000  | PRNG seed |
| 0xB8   | PACKETS_SENT      | RO     | 0x00000000  | live counter, wraps at 2^32 |

## Field detail

### CTRL (0x08, RW1S)

Writing 1 to a bit triggers the action. The bit self-clears after one clock. Writing 0 has no effect. Reads always return 0.

| Bit  | Name       | Meaning |
|------|------------|---------|
| 0    | START      | Start transmission |
| 1    | STOP       | Stop transmission after the in-flight packet |
| 2    | ONE_SHOT   | Send exactly one packet, ignore PACKET_COUNT |
| 31:3 | RSVD       | Read as 0, writes ignored |

### STATUS (0x0C, RO)

| Bit  | Name       | Meaning |
|------|------------|---------|
| 0    | RUNNING    | 1 while the core is emitting packets |
| 1    | DONE       | 1 when PACKET_COUNT is reached and RUNNING=0. Clears on next START |
| 2    | ERROR      | Sticky. Set on downstream tready timeout or config validation failure. Clears on next START |
| 31:3 | RSVD       | Read as 0 |

### FRAME_TYPE (0x10)

| Value | Meaning |
|-------|---------|
| 0     | Ethernet + IPv4 + UDP + RoCEv2 (BTH) |
| 1     | Ethernet + IPv4 + TCP + HTTP |

Other values reserved; writing them sets STATUS.ERROR.

### ETH_SRC_MAC_LO / ETH_SRC_MAC_HI / ETH_DST_MAC_LO / ETH_DST_MAC_HI

Wire byte 0 in bits [7:0] of the LO register. Byte 5 in bits [15:8] of the HI register. Upper 16 bits of HI are reserved.

Example: MAC `02:00:00:00:00:01` → LO=`0x00000002`, HI=`0x00000000`.

### ETH_VLAN (0x24)

| Bit    | Name      | Meaning |
|--------|-----------|---------|
| 0      | ENABLE    | Insert VLAN tag when set |
| 15:1   | RSVD      | 0 |
| 31:16  | TCI       | Raw TCI: PCP[15:13], DEI[12], VID[11:0] |

### IP_SRC / IP_DST (0x28, 0x2C)

IPv4 in dotted order. Byte 0 (first on the wire) in bits [31:24], byte 3 in bits [7:0].

Example: `10.0.0.1` → `0x0A000001`.

### IP_MISC (0x30)

| Bit    | Name  | Meaning |
|--------|-------|---------|
| 7:0    | TTL   | |
| 13:8   | DSCP  | |
| 15:14  | ECN   | |
| 31:16  | RSVD  | 0 |

### TRANSPORT_PORTS (0x34)

| Bit    | Name      | Meaning |
|--------|-----------|---------|
| 15:0   | SRC_PORT  | UDP/TCP source port, host order |
| 31:16  | DST_PORT  | UDP/TCP destination port, host order |

### ROCE_OP_PKEY (0x38)

| Bit    | Name    | Meaning |
|--------|---------|---------|
| 7:0    | OPCODE  | BTH opcode (see IB spec BTH.OpCode encoding) |
| 23:8   | PKEY    | BTH P_Key |
| 31:24  | RSVD    | 0 |

### ROCE_DEST_QP (0x3C)

| Bit    | Name    | Meaning |
|--------|---------|---------|
| 23:0   | DEST_QP | BTH dest QPN |
| 31:24  | RSVD    | 0 |

### ROCE_PSN_ACK (0x40)

| Bit    | Name    | Meaning |
|--------|---------|---------|
| 23:0   | PSN     | First packet's PSN. Increments per packet by header builder |
| 24     | ACK_REQ | BTH ackreq bit |
| 31:25  | RSVD    | 0 |

### SWEEP_*_STEP

Step of 0 disables that sweep dimension. Non-zero step means the field advances by STEP each packet, wrapping from MAX back to MIN when the next value would exceed MAX.

### PACKETS_SENT (0xB8, RO)

Increments once per emitted packet. Wraps at 2^32. Cleared to 0 by START.

## Reserved regions

- 0x44..0x4F: reserved (RoCEv2 growth)
- 0x9C: reserved (sweep growth)
- 0xBC..0xFC: reserved (future extensions)

Access to reserved addresses returns `SLVERR`.

## Change log

- 2026-08-26: initial map. Covers Phase 1 (regfile) and defines everything Phase 2 header builder / Phase 3 sweep engine will consume.
