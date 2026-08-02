from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

PAIRING_PREFIX = "QRB3:"
DEFAULT_PORT = 8765
READ_BLOCK = 1024 * 1024
MAX_FILE_SIZE = 50 * 1024 * 1024 * 1024  # 50 GiB safety ceiling


@dataclass(frozen=True)
class PairingInfo:
    version: int
    ssid: str
    password: str
    host: str
    port: int
    token: str
    protocol: str = "http"

    def encode(self) -> str:
        raw = json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return PAIRING_PREFIX + encoded

    @staticmethod
    def decode(value: str) -> "PairingInfo":
        if not value.startswith(PAIRING_PREFIX):
            raise ValueError("QRBeam 오프라인 연결 QR이 아닙니다.")
        encoded = value[len(PAIRING_PREFIX) :]
        encoded += "=" * ((4 - len(encoded) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        return PairingInfo(**payload)


@dataclass
class TransferProgress:
    filename: str
    received: int
    total: int
    bytes_per_second: float
    eta_seconds: float | None
    completed: bool = False
    error: str | None = None


ProgressCallback = Callable[[TransferProgress], None]


def safe_filename(value: str) -> str:
    name = Path(value).name.strip().strip(".")
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in invalid or ord(ch) < 32 else ch for ch in name)
    return cleaned[:240] or "received_file.bin"


def decode_filename_header(value: str | None) -> str:
    if not value:
        return "received_file.bin"
    try:
        padded = value + "=" * ((4 - len(value) % 4) % 4)
        return safe_filename(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - malformed remote input
        raise ValueError("파일 이름 헤더가 잘못되었습니다.") from exc


def discover_private_ipv4() -> list[str]:
    candidates: set[str] = set()
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = result[4][0]
            if address.startswith(("10.", "172.", "192.168.")) and not address.startswith("127."):
                candidates.add(address)
    except OSError:
        pass

    # Windows Mobile Hotspot normally uses this address.
    ordered = sorted(candidates, key=lambda value: (value != "192.168.137.1", value))
    if "192.168.137.1" not in ordered:
        ordered.insert(0, "192.168.137.1")
    return ordered or ["192.168.137.1"]


class _UploadHandler(BaseHTTPRequestHandler):
    server: "OfflineHTTPServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = self.headers.get("X-QRBeam-Token", "")
        return hmac.compare_digest(token, self.server.transfer_token)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/status":
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        if not self._authorized():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "invalid_token"})
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "service": "QRBeam Offline",
                "version": 3,
                "max_file_size": MAX_FILE_SIZE,
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/upload":
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        if not self._authorized():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "invalid_token"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "-1"))
            declared_size = int(self.headers.get("X-File-Size", str(content_length)))
            declared_sha = self.headers.get("X-File-SHA256", "").lower()
            filename = decode_filename_header(self.headers.get("X-File-Name"))
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        if content_length < 0 or content_length != declared_size:
            self._write_json(HTTPStatus.LENGTH_REQUIRED, {"ok": False, "error": "size_mismatch"})
            return
        if content_length > MAX_FILE_SIZE:
            self._write_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "file_too_large"})
            return
        if len(declared_sha) != 64 or any(ch not in "0123456789abcdef" for ch in declared_sha):
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_sha256"})
            return

        destination = self.server.output_directory / filename
        destination = self.server.unique_destination(destination)
        partial = destination.with_name(destination.name + ".part")
        started = time.monotonic()
        received = 0
        digest = hashlib.sha256()

        try:
            self.server.output_directory.mkdir(parents=True, exist_ok=True)
            with partial.open("wb", buffering=READ_BLOCK) as handle:
                remaining = content_length
                while remaining:
                    block = self.rfile.read(min(READ_BLOCK, remaining))
                    if not block:
                        raise ConnectionError("연결이 파일 수신 도중 끊어졌습니다.")
                    handle.write(block)
                    digest.update(block)
                    received += len(block)
                    remaining -= len(block)
                    elapsed = max(0.001, time.monotonic() - started)
                    speed = received / elapsed
                    eta = (content_length - received) / speed if speed > 0 else None
                    self.server.report(
                        TransferProgress(filename, received, content_length, speed, eta)
                    )
                handle.flush()
                os.fsync(handle.fileno())

            actual_sha = digest.hexdigest()
            if actual_sha != declared_sha:
                partial.unlink(missing_ok=True)
                self.server.report(
                    TransferProgress(
                        filename,
                        received,
                        content_length,
                        0,
                        None,
                        error="SHA-256 불일치",
                    )
                )
                self._write_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "sha256_mismatch", "actual": actual_sha},
                )
                return

            partial.replace(destination)
            elapsed = max(0.001, time.monotonic() - started)
            self.server.report(
                TransferProgress(
                    destination.name,
                    received,
                    content_length,
                    received / elapsed,
                    0,
                    completed=True,
                )
            )
            self._write_json(
                HTTPStatus.CREATED,
                {
                    "ok": True,
                    "name": destination.name,
                    "size": received,
                    "sha256": actual_sha,
                },
            )
        except Exception as exc:  # noqa: BLE001 - network and filesystem boundary
            partial.unlink(missing_ok=True)
            self.server.report(
                TransferProgress(filename, received, content_length, 0, None, error=str(exc))
            )
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})


class OfflineHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        output_directory: Path,
        transfer_token: str,
        progress_callback: ProgressCallback | None,
    ) -> None:
        super().__init__(address, _UploadHandler)
        self.output_directory = output_directory
        self.transfer_token = transfer_token
        self.progress_callback = progress_callback
        self._destination_lock = threading.Lock()

    def report(self, progress: TransferProgress) -> None:
        if self.progress_callback:
            self.progress_callback(progress)

    def unique_destination(self, desired: Path) -> Path:
        with self._destination_lock:
            if not desired.exists() and not desired.with_name(desired.name + ".part").exists():
                return desired
            stem, suffix = desired.stem, desired.suffix
            for number in range(1, 10_000):
                candidate = desired.with_name(f"{stem} ({number}){suffix}")
                if not candidate.exists() and not candidate.with_name(candidate.name + ".part").exists():
                    return candidate
        raise FileExistsError("사용 가능한 파일 이름을 만들 수 없습니다.")


class OfflineReceiverServer:
    def __init__(self, progress_callback: ProgressCallback | None = None) -> None:
        self.progress_callback = progress_callback
        self.token = secrets.token_urlsafe(24)
        self.httpd: OfflineHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.httpd is not None and self.thread is not None and self.thread.is_alive()

    @property
    def port(self) -> int | None:
        return self.httpd.server_address[1] if self.httpd else None

    def start(self, output_directory: str | Path, port: int = DEFAULT_PORT) -> int:
        if self.running:
            assert self.port is not None
            return self.port
        self.token = secrets.token_urlsafe(24)
        self.httpd = OfflineHTTPServer(
            ("0.0.0.0", port),
            Path(output_directory),
            self.token,
            self.progress_callback,
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        assert self.port is not None
        return self.port

    def stop(self) -> None:
        httpd, thread = self.httpd, self.thread
        self.httpd = None
        self.thread = None
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        if thread and thread.is_alive():
            thread.join(timeout=2)

    def pairing_info(self, ssid: str, password: str, host: str) -> PairingInfo:
        if not self.running or self.port is None:
            raise RuntimeError("수신 서버가 실행 중이 아닙니다.")
        return PairingInfo(
            version=3,
            ssid=ssid,
            password=password,
            host=host,
            port=self.port,
            token=self.token,
        )
