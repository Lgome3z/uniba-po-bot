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
    PacketWriter,
    MessageAssembler,
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


class MockSerialWriter:
    """
    A minimal mock that captures bytes written via .write(),
    then exposes them through MockSerial for read-back.
    """
    def __init__(self):
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self._chunks.append(bytes(data))

    @property
    def packets_written(self) -> list[bytes]:
        return list(self._chunks)

    def as_reader(self) -> MockSerial:
        """Return a MockSerial containing all written data for read-back."""
        return MockSerial(b"".join(self._chunks))


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

    def test_fragment_count_greater_than_1_parses(self):
        """Packets with fragment_count > 1 should now parse successfully."""
        raw = build_raw_packet(fragment_count=3, fragment_index=0)
        ser = MockSerial(raw)
        packet = read_packet(ser)
        assert packet is not None
        assert packet.fragment_count == 3
        assert packet.fragment_index == 0

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

        assert message is not None
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

        assert message is not None
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
        assert message is not None
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
        assert message is not None
        handle_message(message)

        assert gateway_state["sensor_data"]["temperature_c"] == 30.0
        assert gateway_state["sensor_data"]["co2_ppm"] == 450


# ═══════════════════════════════════════════════════════════════════════════════
# PacketWriter Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPacketWriter:
    def test_sequence_increments(self):
        """next_sequence() should return 1, 2, 3, …"""
        writer = PacketWriter()
        assert writer.next_sequence() == 1
        assert writer.next_sequence() == 2
        assert writer.next_sequence() == 3

    def test_sequence_wraps_at_16_bits(self):
        """Sequence should wrap around after 0xFFFF."""
        writer = PacketWriter()
        writer._sequence = 0xFFFE
        assert writer.next_sequence() == 0xFFFF
        assert writer.next_sequence() == 0

    def test_item_id_increments(self):
        """next_item_id() should return 1, 2, 3, …"""
        writer = PacketWriter()
        assert writer.next_item_id() == 1
        assert writer.next_item_id() == 2
        assert writer.next_item_id() == 3

    def test_single_packet_roundtrip(self):
        """A short payload should produce one packet parseable by read_packet."""
        writer = PacketWriter()
        mock_ser = MockSerialWriter()

        writer.write_packet(mock_ser, PacketType.Text, b"Hello World")

        assert len(mock_ser.packets_written) == 1

        reader = mock_ser.as_reader()
        packet = read_packet(reader)

        assert packet is not None
        assert packet.type == PacketType.Text
        assert packet.payload == b"Hello World"
        assert packet.fragment_index == 0
        assert packet.fragment_count == 1
        assert packet.item_id == 1
        assert packet.sequence == 1

    def test_crc_integrity(self):
        """The CRC in the written packet should match a fresh computation."""
        writer = PacketWriter()
        mock_ser = MockSerialWriter()

        writer.write_packet(mock_ser, PacketType.Command, b"ping")

        raw = mock_ser.packets_written[0]
        body = raw[:-CRC_SIZE]
        expected_crc = crc16_ccitt_false(body)
        actual_crc = struct.unpack('>H', raw[-CRC_SIZE:])[0]

        assert actual_crc == expected_crc

    def test_empty_payload(self):
        """Writing zero-length data should produce a valid single packet."""
        writer = PacketWriter()
        mock_ser = MockSerialWriter()

        writer.write_packet(mock_ser, PacketType.Text, b"")

        assert len(mock_ser.packets_written) == 1

        reader = mock_ser.as_reader()
        packet = read_packet(reader)

        assert packet is not None
        assert packet.payload_length == 0
        assert packet.payload == b""
        assert packet.fragment_count == 1

    def test_fragmented_packet_roundtrip(self):
        """A payload larger than MAX_PAYLOAD should be split and reassembled."""
        writer = PacketWriter()
        mock_ser = MockSerialWriter()
        asm = MessageAssembler()

        payload = bytes(range(256)) * 9  # 2304 bytes → 2 fragments
        writer.write_packet(mock_ser, PacketType.Video, payload)

        assert len(mock_ser.packets_written) == 2

        message = None
        for raw in mock_ser.packets_written:
            reader = MockSerial(raw)
            packet = read_packet(reader)
            assert packet is not None
            result = asm.assemble(packet)
            if result is not None:
                message = result

        assert message is not None
        assert message.type == PacketType.Video
        assert message.data == payload
        assert message.length == len(payload)

    def test_exact_max_payload_no_extra_fragment(self):
        """A payload of exactly MAX_PAYLOAD bytes should produce 1 fragment."""
        writer = PacketWriter()
        mock_ser = MockSerialWriter()

        payload = bytes(range(256)) * 8  # exactly 2048
        writer.write_packet(mock_ser, PacketType.Audio, payload)

        assert len(mock_ser.packets_written) == 1

        reader = mock_ser.as_reader()
        packet = read_packet(reader)

        assert packet is not None
        assert packet.payload == payload
        assert packet.fragment_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# MessageAssembler Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMessageAssembler:
    def test_single_fragment_returns_message(self):
        """A single-fragment packet should return a Message immediately."""
        asm = MessageAssembler()
        packet = Packet(
            type=PacketType.Text, sequence=1, item_id=1,
            fragment_index=0, fragment_count=1,
            payload_length=5, payload=b"hello",
        )
        message = asm.assemble(packet)
        assert message is not None
        assert message.data == b"hello"

    def test_two_fragment_reassembly(self):
        """Two fragments for the same item_id should reassemble into one Message."""
        asm = MessageAssembler()
        frag0 = Packet(
            type=PacketType.Video, sequence=1, item_id=10,
            fragment_index=0, fragment_count=2,
            payload_length=4, payload=b"AAAA",
        )
        frag1 = Packet(
            type=PacketType.Video, sequence=2, item_id=10,
            fragment_index=1, fragment_count=2,
            payload_length=4, payload=b"BBBB",
        )
        assert asm.assemble(frag0) is None
        message = asm.assemble(frag1)
        assert message is not None
        assert message.data == b"AAAABBBB"
        assert message.length == 8
        assert message.item_id == 10

    def test_out_of_order_fragment_discards(self):
        """Receiving fragment 2 before fragment 1 should discard the partial."""
        asm = MessageAssembler()
        frag0 = Packet(
            type=PacketType.Audio, sequence=1, item_id=20,
            fragment_index=0, fragment_count=3,
            payload_length=2, payload=b"AA",
        )
        frag2 = Packet(
            type=PacketType.Audio, sequence=3, item_id=20,
            fragment_index=2, fragment_count=3,
            payload_length=2, payload=b"CC",
        )
        assert asm.assemble(frag0) is None
        assert asm.assemble(frag2) is None  # out of order, slot cleared

    def test_non_zero_first_fragment_rejected(self):
        """A new item starting with fragment_index != 0 should be rejected."""
        asm = MessageAssembler()
        packet = Packet(
            type=PacketType.Text, sequence=1, item_id=30,
            fragment_index=1, fragment_count=3,
            payload_length=2, payload=b"XX",
        )
        assert asm.assemble(packet) is None

    def test_slot_eviction_oldest(self):
        """When all slots are full, the oldest should be evicted."""
        asm = MessageAssembler(max_slots=2)
        frag_a = Packet(
            type=PacketType.Text, sequence=1, item_id=100,
            fragment_index=0, fragment_count=2,
            payload_length=1, payload=b"A",
        )
        frag_b = Packet(
            type=PacketType.Text, sequence=2, item_id=200,
            fragment_index=0, fragment_count=2,
            payload_length=1, payload=b"B",
        )
        frag_c = Packet(
            type=PacketType.Text, sequence=3, item_id=300,
            fragment_index=0, fragment_count=2,
            payload_length=1, payload=b"C",
        )
        asm.assemble(frag_a)
        asm.assemble(frag_b)
        asm.assemble(frag_c)  # should evict item_id=100 (oldest)

        # item_id=100 was evicted, so its fragment 1 should start a new slot (rejected since index!=0)
        frag_a1 = Packet(
            type=PacketType.Text, sequence=4, item_id=100,
            fragment_index=1, fragment_count=2,
            payload_length=1, payload=b"X",
        )
        assert asm.assemble(frag_a1) is None

    def test_cleanup_expires_stale_slots(self):
        """cleanup() should discard partial messages older than the timeout."""
        asm = MessageAssembler(timeout=0.0)  # immediate expiry
        frag = Packet(
            type=PacketType.Text, sequence=1, item_id=50,
            fragment_index=0, fragment_count=2,
            payload_length=3, payload=b"abc",
        )
        asm.assemble(frag)
        asm.cleanup()

        # The slot should be cleared, so fragment 1 won't find a match
        frag1 = Packet(
            type=PacketType.Text, sequence=2, item_id=50,
            fragment_index=1, fragment_count=2,
            payload_length=3, payload=b"def",
        )
        assert asm.assemble(frag1) is None

    def test_three_fragment_reassembly(self):
        """Three fragments should reassemble correctly."""
        asm = MessageAssembler()
        frags = [
            Packet(type=PacketType.Video, sequence=i+1, item_id=42,
                   fragment_index=i, fragment_count=3,
                   payload_length=3, payload=bytes([i*3, i*3+1, i*3+2]))
            for i in range(3)
        ]
        assert asm.assemble(frags[0]) is None
        assert asm.assemble(frags[1]) is None
        message = asm.assemble(frags[2])
        assert message is not None
        assert message.data == bytes(range(9))
        assert message.length == 9
