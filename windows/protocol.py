from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import os
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Iterator

WIRE_PREFIX = "QRF2:"
MAGIC = b"QRF2"
VERSION = 2
DEFAULT_CHUNK_SIZE = 1400
CORE_INTERVAL = 96
PARITY_GROUP_SIZE = 8

# magic, version, packet_type, transfer_id, index, total, payload_length
_HEADER = struct.Struct(">4sBB16sIIH")
_CRC = struct.Struct(">I")


class ProtocolError(ValueError):
    pass


class PacketType(IntEnum):
    CORE = 1
    DATA = 2
    METADATA = 3
    PARITY = 4


@dataclass(frozen=True, slots=True)
class Packet:
    packet_type: PacketType
    transfer_id: bytes
    index: int
    total: int
    payload: bytes

    def __post_init__(self) -> None:
        if len(self.transfer_id) != 16:
            raise ValueError("transfer_id must be exactly 16 bytes")
        if not 0 <= self.index <= 0xFFFFFFFF:
            raise ValueError("index out of range")
        if not 0 <= self.total <= 0xFFFFFFFF:
            raise ValueError("total out of range")
        if len(self.payload) > 0xFFFF:
            raise ValueError("payload too large")


@dataclass(frozen=True, slots=True)
class CoreManifest:
    size: int
    chunk_size: int
    total: int
    sha256: str
    parity_group: int = PARITY_GROUP_SIZE

    def to_bytes(self) -> bytes:
        body = {
            "size": self.size,
            "chunk": self.chunk_size,
            "total": self.total,
            "sha256": self.sha256,
            "parity": self.parity_group,
        }
        return json.dumps(body, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "CoreManifest":
        try:
            data = json.loads(raw.decode("utf-8"))
            manifest = cls(
                size=int(data["size"]),
                chunk_size=int(data["chunk"]),
                total=int(data["total"]),
                sha256=str(data["sha256"]).lower(),
                parity_group=int(data.get("parity", PARITY_GROUP_SIZE)),
            )
        except (UnicodeDecodeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"invalid core manifest: {exc}") from exc

        if manifest.size < 0 or not 128 <= manifest.chunk_size <= 1800 or manifest.total < 0:
            raise ProtocolError("invalid core manifest numeric values")
        if not 2 <= manifest.parity_group <= 32:
            raise ProtocolError("invalid parity group size")
        if len(manifest.sha256) != 64 or any(c not in "0123456789abcdef" for c in manifest.sha256):
            raise ProtocolError("invalid manifest sha256")
        expected = math.ceil(manifest.size / manifest.chunk_size) if manifest.size else 0
        if expected != manifest.total:
            raise ProtocolError("manifest chunk count does not match size")
        return manifest


@dataclass(frozen=True, slots=True)
class PrivateMetadata:
    name: str
    mime: str | None = None
    modified_utc: str | None = None

    def to_bytes(self) -> bytes:
        body: dict[str, str] = {"name": sanitize_filename(self.name)}
        if self.mime:
            body["mime"] = self.mime
        if self.modified_utc:
            body["modified"] = self.modified_utc
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "PrivateMetadata":
        try:
            data = json.loads(raw.decode("utf-8"))
            name = sanitize_filename(str(data["name"]))
            mime = str(data["mime"]) if data.get("mime") else None
            modified = str(data["modified"]) if data.get("modified") else None
        except (UnicodeDecodeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"invalid private metadata: {exc}") from exc
        return cls(name=name, mime=mime, modified_utc=modified)


def sanitize_filename(name: str) -> str:
    """Drop path components and characters Windows rejects."""
    name = Path(name).name.strip().replace("\x00", "")
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if c in invalid or ord(c) < 32 else c for c in name)
    cleaned = cleaned.rstrip(" .")
    return cleaned or "received_file.bin"


def generic_filename(transfer_id: bytes) -> str:
    return f"received_{transfer_id.hex()[:12]}.bin"


def transfer_id_hex(transfer_id: bytes) -> str:
    return transfer_id.hex()


def encode_packet(packet: Packet) -> str:
    header = _HEADER.pack(
        MAGIC,
        VERSION,
        int(packet.packet_type),
        packet.transfer_id,
        packet.index,
        packet.total,
        len(packet.payload),
    )
    body = header + packet.payload
    raw = body + _CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return WIRE_PREFIX + encoded


def decode_packet(text: str) -> Packet:
    text = text.strip()
    if not text.startswith(WIRE_PREFIX):
        raise ProtocolError("not a QRBeam frame")
    encoded = text[len(WIRE_PREFIX) :]
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(encoded + padding)
    except (ValueError, base64.binascii.Error) as exc:
        raise ProtocolError("invalid base64url") from exc

    minimum = _HEADER.size + _CRC.size
    if len(raw) < minimum:
        raise ProtocolError("frame too short")

    body, crc_raw = raw[:-_CRC.size], raw[-_CRC.size :]
    expected_crc = _CRC.unpack(crc_raw)[0]
    actual_crc = zlib.crc32(body) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise ProtocolError("CRC32 mismatch")

    magic, version, packet_type_raw, transfer_id, index, total, payload_length = _HEADER.unpack(
        body[: _HEADER.size]
    )
    if magic != MAGIC:
        raise ProtocolError("wrong magic")
    if version != VERSION:
        raise ProtocolError(f"unsupported protocol version: {version}")
    try:
        packet_type = PacketType(packet_type_raw)
    except ValueError as exc:
        raise ProtocolError(f"unknown packet type: {packet_type_raw}") from exc

    payload = body[_HEADER.size :]
    if len(payload) != payload_length:
        raise ProtocolError("payload length mismatch")
    if packet_type is PacketType.DATA and index >= total:
        raise ProtocolError("data packet index is outside total")
    if packet_type is PacketType.PARITY and index >= total and total != 0:
        raise ProtocolError("parity group start is outside total")

    return Packet(packet_type, transfer_id, index, total, payload)


def xor_parity(chunks: list[bytes], chunk_size: int) -> bytes:
    parity = bytearray(chunk_size)
    for chunk in chunks:
        if len(chunk) > chunk_size:
            raise ValueError("chunk larger than parity size")
        for index, value in enumerate(chunk):
            parity[index] ^= value
    return bytes(parity)


class TransferPackage:
    """Prepared outbound transfer using QRBeam v2 with privacy and XOR recovery."""

    def __init__(
        self,
        file_path: str | os.PathLike[str],
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        parity_group: int = PARITY_GROUP_SIZE,
    ) -> None:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        if not 128 <= chunk_size <= 1800:
            raise ValueError("chunk_size must be between 128 and 1800 bytes")
        if not 2 <= parity_group <= 32:
            raise ValueError("parity_group must be between 2 and 32")

        self.path = path
        self.data = path.read_bytes()
        self.chunk_size = chunk_size
        self.parity_group = parity_group
        self.transfer_id = os.urandom(16)
        self.total = math.ceil(len(self.data) / chunk_size) if self.data else 0
        self.core = CoreManifest(
            size=len(self.data),
            chunk_size=chunk_size,
            total=self.total,
            sha256=hashlib.sha256(self.data).hexdigest(),
            parity_group=parity_group,
        )
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        self.metadata = PrivateMetadata(
            name=path.name,
            mime=mimetypes.guess_type(path.name)[0],
            modified_utc=modified,
        )
        self._core_frame = encode_packet(
            Packet(PacketType.CORE, self.transfer_id, 0, self.total, self.core.to_bytes())
        )
        self._metadata_frame = encode_packet(
            Packet(PacketType.METADATA, self.transfer_id, 0, self.total, self.metadata.to_bytes())
        )
        self._sequence: list[tuple[str, int]] = []
        self._build_sequence()

    def _build_sequence(self) -> None:
        self._sequence = [("core", 0)]
        if self.total == 0:
            return
        since_core = 0
        for group_start in range(0, self.total, self.parity_group):
            group_end = min(self.total, group_start + self.parity_group)
            for index in range(group_start, group_end):
                if since_core >= CORE_INTERVAL:
                    self._sequence.append(("core", 0))
                    since_core = 0
                self._sequence.append(("data", index))
                since_core += 1
            self._sequence.append(("parity", group_start))
            since_core += 1

    @property
    def cycle_frame_count(self) -> int:
        return len(self._sequence)

    @property
    def metadata_frame(self) -> str:
        return self._metadata_frame

    def data_packet(self, index: int) -> Packet:
        if not 0 <= index < self.total:
            raise IndexError(index)
        start = index * self.chunk_size
        payload = self.data[start : start + self.chunk_size]
        return Packet(PacketType.DATA, self.transfer_id, index, self.total, payload)

    def parity_packet(self, group_start: int) -> Packet:
        if not 0 <= group_start < self.total:
            raise IndexError(group_start)
        group_end = min(self.total, group_start + self.parity_group)
        chunks = [self.data_packet(index).payload for index in range(group_start, group_end)]
        return Packet(
            PacketType.PARITY,
            self.transfer_id,
            group_start,
            self.total,
            xor_parity(chunks, self.chunk_size),
        )

    def iter_cycle(self) -> Iterator[str]:
        for kind, index in self._sequence:
            if kind == "core":
                yield self._core_frame
            elif kind == "parity":
                yield encode_packet(self.parity_packet(index))
            else:
                yield encode_packet(self.data_packet(index))

    def frame_for_counter(self, counter: int) -> tuple[str, str]:
        if counter < 0:
            raise ValueError(counter)
        pos = counter % self.cycle_frame_count
        kind, index = self._sequence[pos]
        if kind == "core":
            return self._core_frame, "core"
        if kind == "parity":
            group_number = index // self.parity_group + 1
            return encode_packet(self.parity_packet(index)), f"recovery {group_number}"
        return encode_packet(self.data_packet(index)), f"{index + 1}/{self.total}"
