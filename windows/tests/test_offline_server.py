from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from offline_server import OfflineReceiverServer, PairingInfo


class OfflineServerTests(unittest.TestCase):
    def test_pairing_round_trip(self) -> None:
        info = PairingInfo(3, "QRBeam Test", "password123", "192.168.137.1", 8765, "token")
        self.assertEqual(PairingInfo.decode(info.encode()), info)

    def test_upload_and_sha256_verification(self) -> None:
        payload = bytes(range(256)) * 4096
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            server = OfflineReceiverServer()
            port = server.start(temp, 0)
            try:
                name = base64.urlsafe_b64encode("sample.bin".encode()).decode().rstrip("=")
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/upload",
                    data=payload,
                    method="POST",
                    headers={
                        "X-QRBeam-Token": server.token,
                        "X-File-Name": name,
                        "X-File-Size": str(len(payload)),
                        "X-File-SHA256": digest,
                        "Content-Type": "application/octet-stream",
                    },
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    result = json.loads(response.read())
                self.assertTrue(result["ok"])
                self.assertEqual((Path(temp) / "sample.bin").read_bytes(), payload)
            finally:
                server.stop()

    def test_rejects_wrong_hash(self) -> None:
        payload = b"not the declared file"
        with tempfile.TemporaryDirectory() as temp:
            server = OfflineReceiverServer()
            port = server.start(temp, 0)
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/upload",
                    data=payload,
                    method="POST",
                    headers={
                        "X-QRBeam-Token": server.token,
                        "X-File-Name": base64.urlsafe_b64encode(b"bad.bin").decode().rstrip("="),
                        "X-File-Size": str(len(payload)),
                        "X-File-SHA256": "0" * 64,
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=10)
                self.assertEqual(raised.exception.code, 422)
                self.assertFalse(any(Path(temp).iterdir()))
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
