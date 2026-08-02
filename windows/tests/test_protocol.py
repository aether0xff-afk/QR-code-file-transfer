from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from protocol import Manifest, Packet, PacketType, TransferPackage, decode_packet, encode_packet
from receiver_state import ReceiverStore


class ProtocolTests(unittest.TestCase):
    def test_packet_round_trip(self) -> None:
        packet = Packet(PacketType.DATA, bytes(range(16)), 2, 3, b"hello\x00world")
        self.assertEqual(decode_packet(encode_packet(packet)), packet)

    def test_manifest_round_trip(self) -> None:
        manifest = Manifest("테스트.bin", 1234, 700, 2, "a" * 64)
        self.assertEqual(Manifest.from_bytes(manifest.to_bytes()), manifest)

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
            self.assertEqual(hashlib.sha256(last.assemble()).hexdigest(), package.manifest.sha256)


if __name__ == "__main__":
    unittest.main()
