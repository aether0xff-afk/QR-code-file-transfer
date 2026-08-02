from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Iterator

WIRE_PREFIX = "QRF1:"
MAGIC = b"QRF1"
VERSION = 1
DEFAULT_CHUNK_SIZE = 700
MANIFEST_INTERVAL = 24

# magic, version, packet_type, transfer_id, index, total, payload_length
_HEADER = struct.Struct(">4sBB16sIIH")
_CRC = struct.Struct(">I")


class ProtocolError(ValueError):
    pass


class PacketType(IntEnum):
    MANIFEST = 1
    DATA = 2


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
class Manifest:
    name: str
    size: int
    chunk_size: int
    total: int
    sha256: str

    def to_bytes(self) -> bytes:
        body = {
            "name": self.name,
            "size": self.size,
            "chunk": self.chunk_size,
            "total": self.total,
            "sha256": self.sha256,
        }
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Manifest":
        try:
            data = json.loads(raw.decode("utf-8"))
            manifest = cls(
                name=sanitize_filename(str(data["name"])),
                size=int(data["size"]),
                chunk_size=int(data["chunk"]),
                total=int(data["total"]),
                sha256=str(data["sha256"]).lower(),
            )
        except (UnicodeDecodeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"invalid manifest: {exc}") from exc

        if manifest.size < 0 or manifest.chunk_size <= 0 or manifest.total < 0:
            raise ProtocolError("invalid manifest numeric values")
        if len(manifest.sha256) != 64 or any(c not in "0123456789abcdef" for c in manifest.sha256):
            raise ProtocolError("invalid manifest sha256")
        expected = math.ceil(manifest.size / manifest.chunk_size) if manifest.size else 0
        if expected != manifest.total:
            raise ProtocolError("manifest chunk count does not match size")
        return manifest


def sanitize_filename(name: str) -> str:
    """Drop path components and characters Windows rejects."""
    name = Path(name).name.strip().replace("\x00", "")
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if c in invalid or ord(c) < 32 else c for c in name)
    cleaned = cleaned.rstrip(" .")
    return cleaned or "received_file.bin"


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

    return Packet(packet_type, transfer_id, index, total, payload)


class TransferPackage:
    """Prepared outbound transfer using the shared QRBeam wire protocol."""

    def __init__(self, file_path: str | os.PathLike[str], chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        if not 128 <= chunk_size <= 1400:
            raise ValueError("chunk_size must be between 128 and 1400 bytes")

        self.path = path
        self.data = path.read_bytes()
        self.chunk_size = chunk_size
        self.transfer_id = os.urandom(16)
        self.total = math.ceil(len(self.data) / chunk_size) if self.data else 0
        self.manifest = Manifest(
            name=sanitize_filename(path.name),
            size=len(self.data),
            chunk_size=chunk_size,
            total=self.total,
            sha256=hashlib.sha256(self.data).hexdigest(),
        )
        self._manifest_frame = encode_packet(
            Packet(PacketType.MANIFEST, self.transfer_id, 0, self.total, self.manifest.to_bytes())
        )
        self._sequence: list[tuple[str, int]] = []
        if self.total == 0:
            self._sequence.append(("manifest", 0))
        else:
            for index in range(self.total):
                if index % MANIFEST_INTERVAL == 0:
                    self._sequence.append(("manifest", 0))
                self._sequence.append(("data", index))

    @property
    def cycle_frame_count(self) -> int:
        return len(self._sequence)

    def data_packet(self, index: int) -> Packet:
        if not 0 <= index < self.total:
            raise IndexError(index)
        start = index * self.chunk_size
        payload = self.data[start : start + self.chunk_size]
        return Packet(PacketType.DATA, self.transfer_id, index, self.total, payload)

    def iter_cycle(self) -> Iterator[str]:
        for kind, index in self._sequence:
            if kind == "manifest":
                yield self._manifest_frame
            else:
                yield encode_packet(self.data_packet(index))

    def frame_for_counter(self, counter: int) -> tuple[str, str]:
        """Return frame text and a human-readable position for a repeating transfer."""
        if counter < 0:
            raise ValueError(counter)
        pos = counter % self.cycle_frame_count
        kind, index = self._sequence[pos]
        if kind == "manifest":
            return self._manifest_frame, "manifest"
        return encode_packet(self.data_packet(index)), f"{index + 1}/{self.total}"
