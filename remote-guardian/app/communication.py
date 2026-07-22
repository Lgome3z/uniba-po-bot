"""
communication.py — Python port of the M5's binary communication protocol.

Implements both sides of the packet protocol:

  Read:
    - crc16_ccitt_false(): CRC-16/CCITT-FALSE checksum
    - read_packet(): parse a binary packet from a serial stream
    - assemble_message(): convert a Packet into a Message
    - handle_message(): dispatch a Message based on its type

  Write:
    - PacketWriter: stateful writer that fragments data, assembles packets,
      computes CRC, and sends them over serial.

Packet wire format (big-endian):
  [0xAA][0x55][type:1][seq:2][itemID:4][fragIdx:2][fragCnt:2][payLen:2][payload:N][crc:2]
  |---- HEADER (15 bytes) ----|                                        |-- CRC --|
"""

import struct
import json
import time
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Optional
from ctypes import c_uint8, c_uint16, c_uint32

from state import gateway_state

MAX_PAYLOAD  = 2048
HEADER_SIZE  = 15
CRC_SIZE     = 2
START_MARKER = b'\xAA\x55'
HEADER_FORMAT = '>BHIHHH'

class PacketType(IntEnum):
    Text    = 0
    Sensors = 1
    Audio   = 2
    Video   = 3
    Command = 4
    JSON    = 5

@dataclass
class Packet:
    type: PacketType
    sequence: c_uint16
    item_id: c_uint32
    fragment_index: c_uint16
    fragment_count: c_uint16
    payload_length: c_uint16
    payload: bytes


@dataclass
class Message:
    type: PacketType
    item_id: c_uint16
    length: c_uint16
    data: bytes

def crc16_ccitt_false(data: bytes) -> c_uint16:
    """Compute CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _scan_for_start_marker(ser) -> bool:
    """Consume bytes from *ser* until the 0xAA 0x55 start marker is found."""
    while ser.in_waiting >= 2:
        if ser.read(1) == b'\xAA' and ser.read(1) == b'\x55':
            return True
    return False


def read_packet(ser) -> Optional[Packet]:
    """Read and parse a single binary packet from the serial port.

    Scans for the start marker, reads the header and payload, validates
    the CRC, and returns a Packet — or None if no valid packet is available.

    Args:
        ser: A serial.Serial instance (or any object with
             .in_waiting and .read()).
    """
    if ser.in_waiting < 2:
        return None

    if not _scan_for_start_marker(ser):
        return None

    remaining_header = HEADER_SIZE - len(START_MARKER)
    if ser.in_waiting < remaining_header:
        return None

    header_rest = ser.read(remaining_header)
    if len(header_rest) < remaining_header:
        return None

    pkt_type_raw, sequence, item_id, frag_idx, frag_cnt, payload_length = struct.unpack(
        HEADER_FORMAT, header_rest
    )

    if frag_cnt > 1:
        return None
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
    calculated_crc = crc16_ccitt_false(START_MARKER + header_rest + payload)

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


def assemble_message(packet: Packet) -> Message:
    """Convert a parsed Packet into a Message.

    Multi-fragment reassembly is not yet supported; fragment_count is
    validated to be <= 1 in read_packet.
    """
    return Message(
        type=packet.type,
        item_id=packet.item_id,
        length=packet.payload_length,
        data=bytes(packet.payload),
    )


def handle_message(message: Message) -> None:
    """Dispatch a Message based on its PacketType."""
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


# PacketWriter

class PacketWriter:
    """Stateful packet writer that mirrors the C++ Communication class.

    Maintains incrementing sequence and item_id counters and handles
    fragmenting large payloads across multiple packets.
    """

    def __init__(self):
        self._sequence: c_uint16 = 0
        self._item_id: c_uint32 = 0

    def next_sequence(self) -> c_uint16:
        """Increment and return the next 16-bit sequence number."""
        self._sequence = (self._sequence + 1) & 0xFFFF
        return self._sequence

    def next_item_id(self) -> c_uint32:
        """Increment and return the next 32-bit item ID."""
        self._item_id = (self._item_id + 1) & 0xFFFFFFFF
        return self._item_id

    @staticmethod
    def _calculate_fragment_count(length: c_uint32) -> c_uint16:
        """Return the number of MAX_PAYLOAD-sized fragments needed for *length* bytes."""
        return (length + MAX_PAYLOAD - 1) // MAX_PAYLOAD if length > 0 else 1

    @staticmethod
    def _assemble_packet(
        packet_type: PacketType,
        sequence: c_uint16,
        item_id: c_uint32,
        fragment_index: c_uint16,
        fragment_count: c_uint16,
        payload: bytes,
    ) -> bytes:
        """Build a complete packet (header + payload + CRC) as bytes.

        The returned bytes are ready to be written directly to the serial port.
        """
        header = START_MARKER + struct.pack(
            '>BHIHHH',
            int(packet_type),
            sequence,
            item_id,
            fragment_index,
            fragment_count,
            len(payload),
        )
        body = header + payload
        crc = crc16_ccitt_false(body)
        return body + struct.pack('>H', crc)

    def write_packet(self, ser, packet_type: PacketType, data: bytes) -> None:
        """Fragment *data* and write one or more packets to the serial port.

        This is a direct port of Communication::writePacket().
        Each fragment is assembled into a full packet with CRC and written
        immediately.

        Args:
            ser: An open serial.Serial instance (or any object with .write()).
            packet_type: The PacketType enum value for this data.
            data: The raw payload bytes to send.
        """
        item_id = self.next_item_id()
        fragment_count = self._calculate_fragment_count(len(data))

        for i in range(fragment_count):
            offset = i * MAX_PAYLOAD
            fragment = data[offset:offset + MAX_PAYLOAD]

            packet = self._assemble_packet(
                packet_type,
                self.next_sequence(),
                item_id,
                i,
                fragment_count,
                fragment,
            )

            ser.write(packet)
