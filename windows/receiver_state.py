from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from protocol import (
    CoreManifest,
    Packet,
    PacketType,
    PrivateMetadata,
    ProtocolError,
    generic_filename,
    sanitize_filename,
    transfer_id_hex,
)


@dataclass(slots=True)
class IncomingTransfer:
    transfer_id: bytes
    core: CoreManifest | None = None
    metadata: PrivateMetadata | None = None
    chunks: dict[int, bytes] = field(default_factory=dict)
    parity: dict[int, bytes] = field(default_factory=dict)
    completed_path: Path | None = None
    first_seen: float = field(default_factory=time.monotonic)
    last_unique_at: float = field(default_factory=time.monotonic)
    unique_events: list[tuple[float, int]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return transfer_id_hex(self.transfer_id)

    @property
    def display_name(self) -> str:
        return self.metadata.name if self.metadata else generic_filename(self.transfer_id)

    @property
    def expected_total(self) -> int:
        if self.core is not None:
            return self.core.total
        if self.chunks:
            return max(self.chunks) + 1
        return 0

    @property
    def progress(self) -> float:
        total = self.core.total if self.core else 0
        if total == 0:
            return 1.0 if self.core is not None else 0.0
        return min(1.0, len(self.chunks) / total)

    @property
    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self.first_seen)

    @property
    def chunk_rate(self) -> float:
        now = time.monotonic()
        window_start = now - 8.0
        events = [(stamp, count) for stamp, count in self.unique_events if stamp >= window_start]
        self.unique_events[:] = events
        if not events:
            return 0.0
        start = min(self.first_seen, events[0][0])
        span = max(0.5, now - max(window_start, start))
        return sum(count for _, count in events) / span

    @property
    def byte_rate(self) -> float:
        if self.core is None:
            return 0.0
        return self.chunk_rate * self.core.chunk_size

    @property
    def eta_seconds(self) -> float | None:
        if self.core is None:
            return None
        remaining = max(0, self.core.total - len(self.chunks))
        if remaining == 0:
            return 0.0
        rate = self.chunk_rate
        if rate < 0.15 or time.monotonic() - self.last_unique_at > 3.0:
            return None
        return remaining / rate

    def accept(self, packet: Packet) -> int:
        if packet.transfer_id != self.transfer_id:
            raise ProtocolError("transfer id mismatch")

        new_chunks = 0
        if packet.packet_type is PacketType.CORE:
            core = CoreManifest.from_bytes(packet.payload)
            if packet.total != core.total:
                raise ProtocolError("core packet total mismatch")
            self.core = core
            new_chunks += self._recover_available()
        elif packet.packet_type is PacketType.METADATA:
            self.metadata = PrivateMetadata.from_bytes(packet.payload)
        elif packet.packet_type is PacketType.DATA:
            if packet.index not in self.chunks:
                self.chunks[packet.index] = packet.payload
                new_chunks += 1
            new_chunks += self._recover_available()
        elif packet.packet_type is PacketType.PARITY:
            if packet.index not in self.parity:
                self.parity[packet.index] = packet.payload
            new_chunks += self._recover_available()

        if new_chunks:
            now = time.monotonic()
            self.last_unique_at = now
            self.unique_events.append((now, new_chunks))
        return new_chunks

    def _recover_available(self) -> int:
        if self.core is None or self.core.total == 0:
            return 0
        recovered = 0
        for group_start, parity_payload in list(self.parity.items()):
            group_end = min(self.core.total, group_start + self.core.parity_group)
            missing = [index for index in range(group_start, group_end) if index not in self.chunks]
            if len(missing) != 1:
                continue
            if len(parity_payload) != self.core.chunk_size:
                continue
            restored = bytearray(parity_payload)
            for index in range(group_start, group_end):
                if index == missing[0]:
                    continue
                chunk = self.chunks.get(index)
                if chunk is None:
                    break
                for offset, value in enumerate(chunk):
                    restored[offset] ^= value
            else:
                self.chunks[missing[0]] = bytes(restored)
                recovered += 1
        return recovered

    def is_complete(self) -> bool:
        if self.core is None:
            return False
        if self.core.total == 0:
            return True
        return len(self.chunks) == self.core.total and all(
            index in self.chunks for index in range(self.core.total)
        )

    def assemble(self) -> bytes:
        if not self.is_complete() or self.core is None:
            raise ProtocolError("transfer is incomplete")
        data = b"".join(self.chunks[index] for index in range(self.core.total))
        data = data[: self.core.size]
        if len(data) != self.core.size:
            raise ProtocolError("assembled file size mismatch")
        digest = hashlib.sha256(data).hexdigest()
        if digest != self.core.sha256:
            raise ProtocolError("SHA-256 mismatch")
        return data

    def save(self, output_directory: str | Path, override_name: str | None = None) -> Path:
        if self.core is None:
            raise ProtocolError("core manifest not received")
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        name = sanitize_filename(override_name or self.display_name)
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
