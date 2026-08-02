"""Optional deterministic integration test: render every frame and decode it with OpenCV."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
import qrcode
from qrcode.constants import ERROR_CORRECT_M

from protocol import Packet, PacketType, TransferPackage, decode_packet, encode_packet
from receiver_state import ReceiverStore


def main() -> None:
    # A moderate-density QR keeps this CI-style image test reliable across OpenCV builds.
    source = bytes(range(256)) * 4 + b"end"
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "optical_roundtrip.bin"
        path.write_bytes(source)
        package = TransferPackage(path, chunk_size=400)

        # Remove randomness so every run produces identical QR matrices.
        package.transfer_id = bytes(range(16))
        package._manifest_frame = encode_packet(  # noqa: SLF001 - deterministic test fixture
            Packet(
                PacketType.MANIFEST,
                package.transfer_id,
                0,
                package.total,
                package.manifest.to_bytes(),
            )
        )

        store = ReceiverStore()
        transfer = None

        for frame_number, wire in enumerate(package.iter_cycle()):
            qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=12, border=4)
            qr.add_data(wire)
            qr.make(fit=True)
            image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            frame = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
            decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(frame)
            if decoded != wire:
                raise RuntimeError(f"OpenCV failed to decode rendered frame {frame_number}")
            transfer = store.accept(decode_packet(decoded))

        assert transfer is not None
        assert transfer.is_complete()
        assert transfer.assemble() == source
        print(
            f"Optical round-trip OK: {package.total} chunks, "
            f"{package.cycle_frame_count} QR frames"
        )


if __name__ == "__main__":
    main()
