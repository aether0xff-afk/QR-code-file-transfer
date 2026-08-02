from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from protocol import Manifest, Packet, PacketType, ProtocolError, sanitize_filename, transfer_id_hex


@dataclass(slots=True)
class IncomingTransfer:
    transfer_id: bytes
    manifest: Manifest | None = None
    chunks: dict[int, bytes] = field(default_factory=dict)
    completed_path: Path | None = None

    @property
    def key(self) -> str:
        return transfer_id_hex(self.transfer_id)

    @property
    def expected_total(self) -> int:
        if self.manifest is not None:
            return self.manifest.total
        if self.chunks:
            return max(self.chunks) + 1
        return 0

    @property
    def progress(self) -> float:
        total = self.manifest.total if self.manifest else 0
        if total == 0:
            return 1.0 if self.manifest is not None else 0.0
        return min(1.0, len(self.chunks) / total)

    def accept(self, packet: Packet) -> bool:
        if packet.transfer_id != self.transfer_id:
            raise ProtocolError("transfer id mismatch")
        changed = False
        if packet.packet_type is PacketType.MANIFEST:
            manifest = Manifest.from_bytes(packet.payload)
            if packet.total != manifest.total:
                raise ProtocolError("manifest packet total mismatch")
            if self.manifest != manifest:
                self.manifest = manifest
                changed = True
        elif packet.packet_type is PacketType.DATA:
            if packet.index not in self.chunks:
                self.chunks[packet.index] = packet.payload
                changed = True
        return changed

    def is_complete(self) -> bool:
        if self.manifest is None:
            return False
        if self.manifest.total == 0:
            return True
        return len(self.chunks) == self.manifest.total and all(
            index in self.chunks for index in range(self.manifest.total)
        )

    def assemble(self) -> bytes:
        if not self.is_complete() or self.manifest is None:
            raise ProtocolError("transfer is incomplete")
        data = b"".join(self.chunks[index] for index in range(self.manifest.total))
        data = data[: self.manifest.size]
        if len(data) != self.manifest.size:
            raise ProtocolError("assembled file size mismatch")
        digest = hashlib.sha256(data).hexdigest()
        if digest != self.manifest.sha256:
            raise ProtocolError("SHA-256 mismatch")
        return data

    def save(self, output_directory: str | Path) -> Path:
        if self.manifest is None:
            raise ProtocolError("manifest not received")
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        name = sanitize_filename(self.manifest.name)
        candidate = output / name
        stem, suffix = candidate.stem, candidate.suffix
        counter = 1
        while candidate.exists():
            candidate = output / f"{stem} ({counter}){suffix}"
            counter += 1
        candidate.write_bytes(self.assemble())
        self.completed_path = candidate
        return candidate


class ReceiverStore:
    def __init__(self) -> None:
        self.transfers: dict[str, IncomingTransfer] = {}

    def accept(self, packet: Packet) -> IncomingTransfer:
        key = transfer_id_hex(packet.transfer_id)
        transfer = self.transfers.get(key)
        if transfer is None:
            transfer = IncomingTransfer(packet.transfer_id)
            self.transfers[key] = transfer
        transfer.accept(packet)
        return transfer
