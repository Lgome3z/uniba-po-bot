"""
communication.py — Python port of the M5's binary communication protocol.

Implements the read side of the packet protocol:
  - crc16_ccitt_false(): CRC-16/CCITT-FALSE checksum
  - read_packet(): parse a binary packet from a serial stream
  - assemble_message(): convert a Packet into a Message
  - handle_message(): dispatch a Message based on its type

Packet wire format (big-endian):
  [0xAA][0x55][type:1][seq:2][itemID:4][fragIdx:2][fragCnt:2][payLen:2][payload:N][crc:2]
  |---- HEADER (15 bytes) ----|                                        |-- CRC --|
"""

import struct
import json
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Optional

from state import gateway_state

# Protocol constants (must match Communication.h)

MAX_PAYLOAD  = 2048
HEADER_SIZE  = 15
CRC_SIZE     = 2
START_MARKER = b'\xAA\x55'

# PacketType enum

class PacketType(IntEnum):
    Text    = 0
    Sensors = 1
    Audio   = 2
    Video   = 3
    Command = 4
    JSON    = 5

# Data structures

@dataclass
class Packet:
    type: PacketType
    sequence: int
    item_id: int
    fragment_index: int
    fragment_count: int
    payload_length: int
    payload: bytes


@dataclass
class Message:
    type: PacketType
    item_id: int
    length: int
    data: bytes

# CRC-16/CCITT-FALSE

def crc16_ccitt_false(data: bytes) -> int:
    """
    Compute CRC-16/CCITT-FALSE.

    Polynomial: 0x1021
    Initial value: 0xFFFF
    No final XOR, no bit reversal.

    This is a direct port of the C++ crc16() function.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# read_packet

def read_packet(ser) -> Optional[Packet]:
    """
    Read and parse a single binary packet from the serial port.

    Port of Communication::readPacket().

    Steps:
      1. Scan for the 0xAA 0x55 start marker.
      2. Read the remaining 13 header bytes.
      3. Parse and validate header fields.
      4. Read payload and CRC.
      5. Verify CRC over header + payload.

    Args:
        ser: A serial.Serial instance (or any object with
             .in_waiting, .read(), .peek()-like behaviour).

    Returns:
        A Packet on success, or None if no valid packet is available.
    """

    # We need at least 2 bytes to look for the marker.
    if ser.in_waiting < 2:
        return None

    found_marker = False
    while ser.in_waiting >= 2:
        b = ser.read(1)
        if b == b'\xAA':
            # Peek at the next byte without consuming it if possible,
            # but pyserial doesn't have peek(). Read and check.
            next_b = ser.read(1)
            if next_b == b'\x55':
                found_marker = True
                break
            # If next_b wasn't 0x55, it might itself be 0xAA,
            # so we need to check it. But since we already consumed it,
            # we can only continue scanning.
    if not found_marker:
        return None

    remaining_header_size = HEADER_SIZE - 2  # 13 bytes
    if ser.in_waiting < remaining_header_size:
        return None

    header_rest = ser.read(remaining_header_size)
    if len(header_rest) < remaining_header_size:
        return None

    # Layout after start marker (13 bytes):
    #   B  = uint8  (type)        = 1 byte
    #   H  = uint16 (sequence)    = 2 bytes
    #   I  = uint32 (itemID)      = 4 bytes
    #   H  = uint16 (fragIndex)   = 2 bytes
    #   H  = uint16 (fragCount)   = 2 bytes
    #   H  = uint16 (payloadLen)  = 2 bytes
    #   Total = 1+2+4+2+2+2 = 13 bytes
    pkt_type_raw, sequence, item_id, frag_idx, frag_cnt, payload_length = struct.unpack(
        '>BHIHHH', header_rest
    )

    # Validate: no multi-fragment support yet (matches C++ behaviour)
    if frag_cnt > 1:
        return None

    # Validate: payload length within bounds
    if payload_length > MAX_PAYLOAD:
        return None

    try:
        pkt_type = PacketType(pkt_type_raw)
    except ValueError:
        return None

    if ser.in_waiting < payload_length + CRC_SIZE:
        return None

    payload = ser.read(payload_length)
    if len(payload) < payload_length:
        return None

    crc_bytes = ser.read(CRC_SIZE)
    if len(crc_bytes) < CRC_SIZE:
        return None

    received_crc = struct.unpack('>H', crc_bytes)[0]

    # CRC is computed over the full header (including start marker) + payload
    full_header = START_MARKER + header_rest
    calculated_crc = crc16_ccitt_false(full_header + payload)

    if received_crc != calculated_crc:
        return None

    return Packet(
        type=pkt_type,
        sequence=sequence,
        item_id=item_id,
        fragment_index=frag_idx,
        fragment_count=frag_cnt,
        payload_length=payload_length,
        payload=payload,
    )


# assemble_message

def assemble_message(packet: Packet) -> Message:
    """
    Convert a parsed Packet into a Message.

    Port of Communication::assembleMessage().

    Currently a straightforward copy since multi-fragment reassembly
    is not yet supported (fragment_count is validated to be <= 1 in read_packet).
    """
    return Message(
        type=packet.type,
        item_id=packet.item_id,
        length=packet.payload_length,
        data=bytes(packet.payload),  # defensive copy
    )


# handle_message

def handle_message(message: Message) -> None:
    """
    Dispatch a Message based on its type.

    Port of Communication::handleMessage(), extended to also handle
    Sensors and JSON types for gateway_state integration.
    """
    import time

    if message.type == PacketType.Text:
        text = message.data.decode('utf-8', errors='replace')
        print(f"Received Text: {text}")

    elif message.type == PacketType.Sensors:
        try:
            sensor_data = json.loads(message.data.decode('utf-8'))
            gateway_state["sensor_data"] = sensor_data
            gateway_state["last_seen_at"] = int(time.time())
            gateway_state["online"] = True
            print(f"Received Sensors: {sensor_data}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"Failed to decode Sensors payload: {e}")

    elif message.type == PacketType.JSON:
        try:
            json_data = json.loads(message.data.decode('utf-8'))
            print(f"Received JSON: {json_data}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"Failed to decode JSON payload: {e}")

    else:
        print(f"Received unhandled packet type: {message.type.name} "
              f"({message.length} bytes)")
