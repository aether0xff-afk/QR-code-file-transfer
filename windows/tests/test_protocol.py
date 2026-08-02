from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from protocol import (
    CoreManifest,
    Packet,
    PacketType,
    PrivateMetadata,
    TransferPackage,
    decode_packet,
    encode_packet,
)
from receiver_state import ReceiverStore


class ProtocolTests(unittest.TestCase):
    def test_packet_round_trip(self) -> None:
        packet = Packet(PacketType.DATA, bytes(range(16)), 2, 3, b"hello\x00world")
        self.assertEqual(decode_packet(encode_packet(packet)), packet)

    def test_core_and_metadata_round_trip(self) -> None:
        core = CoreManifest(1234, 700, 2, "a" * 64, 8)
        metadata = PrivateMetadata("테스트.bin", "application/octet-stream", "2026-08-02T00:00:00+00:00")
        self.assertEqual(CoreManifest.from_bytes(core.to_bytes()), core)
        self.assertEqual(PrivateMetadata.from_bytes(metadata.to_bytes()), metadata)

    def test_file_transfer_round_trip(self) -> None:
        source = bytes(range(256)) * 40 + b"tail"
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "sample.bin"
            src.write_bytes(source)
            package = TransferPackage(src, chunk_size=700)
            store = ReceiverStore()
            last = None
            for wire in package.iter_cycle():
                last = store.accept(decode_packet(wire))
            assert last is not None
            self.assertTrue(last.is_complete())
            self.assertEqual(last.assemble(), source)
            self.assertEqual(hashlib.sha256(last.assemble()).hexdigest(), package.core.sha256)
            self.assertNotEqual(last.display_name, src.name)
            store.accept(decode_packet(package.metadata_frame))
            self.assertEqual(last.display_name, src.name)

    def test_xor_recovers_one_missing_chunk_per_group(self) -> None:
        source = bytes(range(251)) * 50
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "recovery.bin"
            src.write_bytes(source)
            package = TransferPackage(src, chunk_size=600, parity_group=8)
            store = ReceiverStore()
            transfer = None
            missing_index = 3
            for wire in package.iter_cycle():
                packet = decode_packet(wire)
                if packet.packet_type is PacketType.DATA and packet.index == missing_index:
                    continue
                transfer = store.accept(packet)
            assert transfer is not None
            self.assertIn(missing_index, transfer.chunks)
            self.assertTrue(transfer.is_complete())
            self.assertEqual(transfer.assemble(), source)

    def test_hash_mismatch_never_completes_successfully(self) -> None:
        source = b"important data" * 100
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "integrity.bin"
            src.write_bytes(source)
            package = TransferPackage(src, chunk_size=400)
            store = ReceiverStore()
            transfer = None
            for wire in package.iter_cycle():
                packet = decode_packet(wire)
                transfer = store.accept(packet)
            assert transfer is not None and transfer.core is not None
            transfer.chunks[0] = b"X" + transfer.chunks[0][1:]
            with self.assertRaises(Exception):
                transfer.assemble()


if __name__ == "__main__":
    unittest.main()
