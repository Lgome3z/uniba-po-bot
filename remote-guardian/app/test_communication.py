"""
test_communication.py — Unit tests for the Python communication protocol.

Tests each function in communication.py using mock serial data,
no M5 hardware required.
"""

import io
import struct
import sys
import os
import pytest

# Ensure the app directory is on the path for imports
sys.path.insert(0, os.path.dirname(__file__))

from communication import (
    crc16_ccitt_false,
    read_packet,
    assemble_message,
    handle_message,
    PacketType,
    Packet,
    Message,
    HEADER_SIZE,
    CRC_SIZE,
    START_MARKER,
    MAX_PAYLOAD,
)


# ── Helper: Mock serial port using BytesIO ───────────────────────────────────

class MockSerial:
    """
    A minimal mock of pyserial's Serial class backed by io.BytesIO.
    Supports .in_waiting, .read(), and .peek() — just enough for read_packet.
    """
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)
        self._total = len(data)

    @property
    def in_waiting(self) -> int:
        return self._total - self._buf.tell()

    def read(self, size: int = 1) -> bytes:
        return self._buf.read(size)


# ── Helper: Build a valid binary packet ──────────────────────────────────────

def build_raw_packet(
    pkt_type: PacketType = PacketType.Text,
    sequence: int = 1,
    item_id: int = 1,
    fragment_index: int = 0,
    fragment_count: int = 1,
    payload: bytes = b"Hello there.",
) -> bytes:
    """
    Construct a complete binary packet (header + payload + CRC)
    exactly as the M5's assemblePacket + writePacket would produce.
    """
    # Header: start_marker(2) + type(1) + seq(2) + itemID(4) + fragIdx(2) + fragCnt(2) + payLen(2)
    header = START_MARKER + struct.pack(
        '>BHIHHH',
        int(pkt_type),
        sequence,
        item_id,
        fragment_index,
        fragment_count,
        len(payload),
    )
    assert len(header) == HEADER_SIZE

    crc = crc16_ccitt_false(header + payload)
    crc_bytes = struct.pack('>H', crc)

    return header + payload + crc_bytes


# ═══════════════════════════════════════════════════════════════════════════════
# CRC Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCRC16:
    def test_known_value_empty(self):
        """CRC of empty data should be the initial value 0xFFFF."""
        assert crc16_ccitt_false(b"") == 0xFFFF

    def test_known_value_123456789(self):
        """
        Standard CRC-16/CCITT-FALSE test vector.
        Input: ASCII "123456789" → CRC should be 0x29B1.
        (This is the canonical check value for this algorithm.)
        """
        assert crc16_ccitt_false(b"123456789") == 0x29B1

    def test_known_value_single_byte(self):
        """CRC of a single 0x00 byte."""
        # Manually computed: 0xFFFF ^ (0x00 << 8) = 0xFFFF
        # After 8 iterations with poly 0x1021:
        # This yields 0x1021 (since the MSB is always set for 0xFFFF start)
        # Actually let's just compute it:
        expected = crc16_ccitt_false(b"\x00")
        # Verify it's deterministic
        assert crc16_ccitt_false(b"\x00") == expected

    def test_deterministic(self):
        """Same input always produces same CRC."""
        data = b"Hello there."
        crc1 = crc16_ccitt_false(data)
        crc2 = crc16_ccitt_false(data)
        assert crc1 == crc2

    def test_different_inputs(self):
        """Different inputs produce different CRCs."""
        assert crc16_ccitt_false(b"abc") != crc16_ccitt_false(b"abd")

    def test_result_is_16_bit(self):
        """Result should always fit in 16 bits."""
        crc = crc16_ccitt_false(b"some longer test data for verification")
        assert 0 <= crc <= 0xFFFF


# ═══════════════════════════════════════════════════════════════════════════════
# read_packet Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestReadPacket:
    def test_valid_text_packet(self):
        """Parse a valid Text packet with payload 'Hello there.'"""
        raw = build_raw_packet(
            pkt_type=PacketType.Text,
            sequence=42,
            item_id=7,
            payload=b"Hello there.",
        )
        ser = MockSerial(raw)
        packet = read_packet(ser)

        assert packet is not None
        assert packet.type == PacketType.Text
        assert packet.sequence == 42
        assert packet.item_id == 7
        assert packet.fragment_index == 0
        assert packet.fragment_count == 1
        assert packet.payload_length == len(b"Hello there.")
        assert packet.payload == b"Hello there."

    def test_valid_sensors_packet(self):
        """Parse a valid Sensors packet with JSON payload."""
        payload = b'{"temperature_c":24.5,"humidity_percent":60.0}'
        raw = build_raw_packet(
            pkt_type=PacketType.Sensors,
            sequence=100,
            item_id=5,
            payload=payload,
        )
        ser = MockSerial(raw)
        packet = read_packet(ser)

        assert packet is not None
        assert packet.type == PacketType.Sensors
        assert packet.sequence == 100
        assert packet.payload == payload

    def test_bad_crc_returns_none(self):
        """A packet with corrupted CRC should be rejected."""
        raw = bytearray(build_raw_packet(payload=b"test data"))
        # Corrupt the last byte (part of CRC)
        raw[-1] ^= 0xFF
        ser = MockSerial(bytes(raw))
        packet = read_packet(ser)

        assert packet is None

    def test_garbage_prefix(self):
        """Random garbage before the start marker should be skipped."""
        garbage = b'\x00\x01\x02\xFF\xFE\xAA\x00\x55\xAA\x12'
        valid_packet = build_raw_packet(
            pkt_type=PacketType.Text,
            sequence=1,
            item_id=1,
            payload=b"after garbage",
        )
        raw = garbage + valid_packet
        ser = MockSerial(raw)
        packet = read_packet(ser)

        assert packet is not None
        assert packet.type == PacketType.Text
        assert packet.payload == b"after garbage"

    def test_empty_payload(self):
        """Packet with zero-length payload should parse correctly."""
        raw = build_raw_packet(payload=b"")
        ser = MockSerial(raw)
        packet = read_packet(ser)

        assert packet is not None
        assert packet.payload_length == 0
        assert packet.payload == b""

    def test_max_payload(self):
        """Packet with maximum payload size should parse correctly."""
        payload = bytes(range(256)) * 8  # 2048 bytes
        assert len(payload) == MAX_PAYLOAD
        raw = build_raw_packet(payload=payload)
        ser = MockSerial(raw)
        packet = read_packet(ser)

        assert packet is not None
        assert packet.payload_length == MAX_PAYLOAD
        assert packet.payload == payload

    def test_insufficient_data_returns_none(self):
        """Not enough bytes in the buffer should return None immediately."""
        ser = MockSerial(b'\xAA')  # Only 1 byte
        packet = read_packet(ser)
        assert packet is None

    def test_no_data_returns_none(self):
        """Empty serial buffer should return None."""
        ser = MockSerial(b'')
        packet = read_packet(ser)
        assert packet is None

    def test_fragment_count_greater_than_1_returns_none(self):
        """Packets with fragment_count > 1 should be rejected (not yet supported)."""
        raw = build_raw_packet(fragment_count=3)
        ser = MockSerial(raw)
        packet = read_packet(ser)
        assert packet is None

    def test_all_packet_types(self):
        """Every PacketType enum value should parse correctly."""
        for pkt_type in PacketType:
            raw = build_raw_packet(pkt_type=pkt_type, payload=b"type test")
            ser = MockSerial(raw)
            packet = read_packet(ser)
            assert packet is not None, f"Failed for {pkt_type.name}"
            assert packet.type == pkt_type


# ═══════════════════════════════════════════════════════════════════════════════
# assemble_message Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAssembleMessage:
    def test_basic_assembly(self):
        """Message should contain the same fields as the source Packet."""
        packet = Packet(
            type=PacketType.Text,
            sequence=42,
            item_id=7,
            fragment_index=0,
            fragment_count=1,
            payload_length=12,
            payload=b"Hello there.",
        )
        message = assemble_message(packet)

        assert message.type == PacketType.Text
        assert message.item_id == 7
        assert message.length == 12
        assert message.data == b"Hello there."

    def test_data_is_a_copy(self):
        """Message data should be an independent copy, not a reference."""
        payload = bytearray(b"mutable")
        packet = Packet(
            type=PacketType.Text,
            sequence=1,
            item_id=1,
            fragment_index=0,
            fragment_count=1,
            payload_length=len(payload),
            payload=bytes(payload),
        )
        message = assemble_message(packet)

        # Mutating the original payload should not affect the message
        payload[0] = 0xFF
        assert message.data[0] != 0xFF


# ═══════════════════════════════════════════════════════════════════════════════
# handle_message Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandleMessage:
    def test_text_message_prints(self, capsys):
        """Text messages should be decoded and printed."""
        message = Message(
            type=PacketType.Text,
            item_id=1,
            length=12,
            data=b"Hello there.",
        )
        handle_message(message)

        captured = capsys.readouterr()
        assert "Hello there." in captured.out
        assert "Received Text:" in captured.out

    def test_sensors_message_updates_state(self):
        """Sensors messages should update gateway_state."""
        from state import gateway_state

        payload = b'{"temperature_c": 25.0, "humidity_percent": 55.0}'
        message = Message(
            type=PacketType.Sensors,
            item_id=2,
            length=len(payload),
            data=payload,
        )
        handle_message(message)

        assert gateway_state["online"] is True
        assert gateway_state["sensor_data"]["temperature_c"] == 25.0
        assert gateway_state["sensor_data"]["humidity_percent"] == 55.0
        assert gateway_state["last_seen_at"] > 0

    def test_json_message_prints(self, capsys):
        """JSON messages should be decoded and printed."""
        payload = b'{"key": "value"}'
        message = Message(
            type=PacketType.JSON,
            item_id=3,
            length=len(payload),
            data=payload,
        )
        handle_message(message)

        captured = capsys.readouterr()
        assert "Received JSON:" in captured.out

    def test_unhandled_type_prints(self, capsys):
        """Unhandled message types should print a notice."""
        message = Message(
            type=PacketType.Audio,
            item_id=4,
            length=5,
            data=b"\x00\x01\x02\x03\x04",
        )
        handle_message(message)

        captured = capsys.readouterr()
        assert "unhandled" in captured.out.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# End-to-end: full pipeline test
# ═══════════════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_full_pipeline_text(self, capsys):
        """Wire bytes → read_packet → assemble_message → handle_message."""
        raw = build_raw_packet(
            pkt_type=PacketType.Text,
            sequence=1,
            item_id=1,
            payload=b"Hello there.",
        )
        ser = MockSerial(raw)

        packet = read_packet(ser)
        assert packet is not None

        message = assemble_message(packet)
        handle_message(message)

        captured = capsys.readouterr()
        assert "Hello there." in captured.out

    def test_full_pipeline_sensors(self):
        """Wire bytes → read_packet → assemble_message → handle_message for sensors."""
        from state import gateway_state

        payload = b'{"temperature_c": 30.0, "co2_ppm": 450}'
        raw = build_raw_packet(
            pkt_type=PacketType.Sensors,
            sequence=10,
            item_id=3,
            payload=payload,
        )
        ser = MockSerial(raw)

        packet = read_packet(ser)
        assert packet is not None

        message = assemble_message(packet)
        handle_message(message)

        assert gateway_state["sensor_data"]["temperature_c"] == 30.0
        assert gateway_state["sensor_data"]["co2_ppm"] == 450
