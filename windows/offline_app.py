from __future__ import annotations

import json
import os
import queue
import secrets
import string
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk
import qrcode
from qrcode.constants import ERROR_CORRECT_M

from offline_server import DEFAULT_PORT, OfflineReceiverServer, TransferProgress, discover_private_ipv4

APP_TITLE = "QRBeam Offline 0.4"
SOFTAP_HELPER = "QRBeam-SoftAP.exe"


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "계산 중…"
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def generate_credentials() -> tuple[str, str]:
    suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
    password_alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(password_alphabet) for _ in range(14))
    return f"QRBeam-{suffix}", password


def bundled_helper_path() -> Path:
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / SOFTAP_HELPER)
    source_root = Path(__file__).resolve().parent
    candidates.extend(
        [
            source_root / SOFTAP_HELPER,
            source_root / "softap" / "publish" / SOFTAP_HELPER,
            source_root / "softap" / "bin" / "Release" / "net8.0-windows10.0.19041.0" / "win-x64" / "publish" / SOFTAP_HELPER,
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("자동 오프라인 AP helper를 찾을 수 없습니다. 앱을 다시 설치하거나 수동 모드를 사용하세요.")


class SoftAPController:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, ssid: str, password: str, timeout: float = 20.0) -> dict[str, object]:
        if self.running:
            raise RuntimeError("자동 오프라인 네트워크가 이미 실행 중입니다.")
        helper = bundled_helper_path()
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            [str(helper), "--ssid", ssid, "--password", password],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        self.process = process
        result_queue: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_first_line() -> None:
            assert process.stdout is not None
            result_queue.put(process.stdout.readline())

        threading.Thread(target=read_first_line, daemon=True).start()
        try:
            line = result_queue.get(timeout=timeout).strip()
        except queue.Empty as exc:
            self.stop()
            raise TimeoutError("Windows가 20초 안에 오프라인 Wi-Fi 네트워크를 시작하지 못했습니다.") from exc

        if not line:
            stderr = process.stderr.read().strip() if process.stderr else ""
            exit_code = process.poll()
            self.stop()
            raise RuntimeError(stderr or f"자동 AP helper가 응답 없이 종료되었습니다. 종료 코드: {exit_code}")

        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            self.stop()
            raise RuntimeError(f"자동 AP helper 응답을 해석하지 못했습니다: {line}") from exc

        if payload.get("status") != "started":
            self.stop()
            message = str(payload.get("message") or payload.get("error") or "알 수 없는 Wi-Fi Direct 오류")
            raise RuntimeError(message)
        return payload

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.poll() is None and process.stdin is not None:
                process.stdin.write("stop\n")
                process.stdin.flush()
                process.wait(timeout=4)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()


class OfflineReceiverApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x760")
        self.minsize(900, 640)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.transfer_events: queue.Queue[TransferProgress] = queue.Queue()
        self.ui_events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.server = OfflineReceiverServer(self.transfer_events.put)
        self.softap = SoftAPController()
        self.qr_photo: ImageTk.PhotoImage | None = None
        self.starting = False

        ssid, password = generate_credentials()
        self.auto_ap_var = tk.BooleanVar(value=True)
        self.ssid_var = tk.StringVar(value=ssid)
        self.password_var = tk.StringVar(value=password)
        self.host_var = tk.StringVar(value="192.168.137.1")
        self.port_var = tk.IntVar(value=DEFAULT_PORT)
        self.output_var = tk.StringVar(value=str(Path.home() / "Downloads" / "QRBeam Offline"))
        self.status_var = tk.StringVar(value="버튼 하나로 인터넷 없는 전용 Wi-Fi와 수신 서버를 시작합니다.")
        self.detail_var = tk.StringVar(value="파일은 SHA-256 검증 성공 후에만 확정됩니다.")
        self.progress_var = tk.DoubleVar(value=0)

        self._build_ui()
        self.after(100, self._poll_events)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        left = ttk.Frame(self, padding=20)
        right = ttk.Frame(self, padding=(0, 20, 20, 20))
        left.grid(row=0, column=0, sticky="nsew")
        right.grid(row=0, column=1, sticky="ns")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        self.qr_label = ttk.Label(left, text="네트워크를 시작하면 연결 QR이 표시됩니다.", anchor="center")
        self.qr_label.grid(row=0, column=0, sticky="nsew")
        ttk.Progressbar(left, maximum=100, variable=self.progress_var).grid(row=1, column=0, sticky="ew", pady=(14, 8))
        ttk.Label(left, textvariable=self.status_var, font=("Segoe UI", 12, "bold"), wraplength=680).grid(row=2, column=0, sticky="w")
        ttk.Label(left, textvariable=self.detail_var, wraplength=680).grid(row=3, column=0, sticky="w", pady=(5, 0))

        ttk.Label(right, text="오프라인 고속 수신", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Checkbutton(
            right,
            text="인터넷 없는 전용 Wi-Fi 자동 생성",
            variable=self.auto_ap_var,
            command=self._update_mode_text,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        fields = [
            ("네트워크 이름", self.ssid_var, False),
            ("네트워크 비밀번호", self.password_var, True),
            ("Windows 주소", self.host_var, False),
            ("포트", self.port_var, False),
            ("저장 폴더", self.output_var, False),
        ]
        self.field_entries: list[ttk.Entry] = []
        for row, (title, variable, secret) in enumerate(fields, start=2):
            ttk.Label(right, text=title).grid(row=row, column=0, sticky="w", pady=5)
            entry = ttk.Entry(right, textvariable=variable, width=34, show="•" if secret else "")
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            self.field_entries.append(entry)

        ttk.Button(right, text="새 이름·비밀번호 생성", command=self.regenerate_credentials).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Button(right, text="저장 폴더 선택", command=self.choose_output).grid(row=8, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(right, text="수동 핫스팟 설정 열기 · 폴백", command=self.open_hotspot_settings).grid(row=9, column=0, columnspan=2, sticky="ew", pady=4)
        self.server_button = ttk.Button(right, text="전용 Wi-Fi + 수신 시작", command=self.toggle_server)
        self.server_button.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(12, 4))

        ttk.Separator(right).grid(row=11, column=0, columnspan=2, sticky="ew", pady=16)
        self.mode_note = ttk.Label(right, wraplength=340, justify="left")
        self.mode_note.grid(row=12, column=0, columnspan=2, sticky="w")
        self._update_mode_text()

    def _update_mode_text(self) -> None:
        if self.auto_ap_var.get():
            self.mode_note.configure(
                text=(
                    "권장: Windows 설정의 모바일 핫스팟을 사용하지 않습니다. 앱이 Wi-Fi Direct 레거시 모드로 "
                    "iPad가 접속할 수 있는 로컬 AP를 직접 만듭니다. 인터넷 연결은 필요 없습니다."
                )
            )
            self.server_button.configure(text="전용 Wi-Fi + 수신 시작")
        else:
            self.mode_note.configure(
                text=(
                    "폴백: 이미 켜 둔 Windows 핫스팟이나 다른 로컬 Wi-Fi를 사용합니다. "
                    "위 이름·비밀번호·Windows 주소를 실제 값에 맞춰 입력하세요."
                )
            )
            self.server_button.configure(text="수신 서버 시작")

    def regenerate_credentials(self) -> None:
        if self.server.running or self.starting:
            return
        ssid, password = generate_credentials()
        self.ssid_var.set(ssid)
        self.password_var.set(password)

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(title="오프라인 수신 파일 저장 폴더")
        if selected:
            self.output_var.set(selected)

    def open_hotspot_settings(self) -> None:
        try:
            os.startfile("ms-settings:network-mobilehotspot")  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def toggle_server(self) -> None:
        if self.server.running or self.softap.running:
            self.stop_server()
        elif not self.starting:
            self.start_server()

    def start_server(self) -> None:
        ssid = self.ssid_var.get().strip()
        password = self.password_var.get()
        if not ssid or len(password) < 8:
            messagebox.showwarning(APP_TITLE, "네트워크 이름과 8자 이상의 비밀번호를 입력하세요.")
            return
        self.starting = True
        self.server_button.configure(state="disabled")
        self.status_var.set("인터넷 없는 전용 Wi-Fi를 시작하는 중…" if self.auto_ap_var.get() else "수신 서버를 시작하는 중…")
        self.detail_var.set("Wi-Fi가 켜져 있어야 하며, 기존 모바일 핫스팟은 꺼져 있어야 합니다.")

        config = {
            "auto": self.auto_ap_var.get(),
            "ssid": ssid,
            "password": password,
            "host": self.host_var.get().strip(),
            "port": int(self.port_var.get()),
            "output": self.output_var.get(),
        }
        threading.Thread(target=self._start_stack, args=(config,), daemon=True).start()

    def _start_stack(self, config: dict[str, object]) -> None:
        try:
            host = str(config["host"])
            if bool(config["auto"]):
                result = self.softap.start(str(config["ssid"]), str(config["password"]))
                addresses = [str(value) for value in result.get("addresses", []) if value]
                if addresses:
                    host = addresses[0]
            port = self.server.start(str(config["output"]), int(config["port"]))
            info = self.server.pairing_info(str(config["ssid"]), str(config["password"]), host)
            self.ui_events.put(("started", info))
        except Exception as exc:  # noqa: BLE001 - hardware and process boundary
            self.server.stop()
            self.softap.stop()
            self.ui_events.put(("start_error", str(exc)))

    def _show_pairing_qr(self, info: object) -> None:
        self.host_var.set(info.host)
        qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=4, box_size=8)
        qr.add_data(info.encode())
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        image.thumbnail((610, 610))
        self.qr_photo = ImageTk.PhotoImage(image)
        self.qr_label.configure(image=self.qr_photo, text="")
        self.server_button.configure(text="네트워크와 수신 중지", state="normal")
        self.status_var.set(f"수신 대기 중 · {info.ssid} · {info.host}:{info.port}")
        self.detail_var.set("iPad QRBeam의 오프라인 탭에서 연결 QR을 스캔하세요.")
        self.progress_var.set(0)
        self.starting = False

    def stop_server(self) -> None:
        self.server_button.configure(state="disabled")
        self.status_var.set("네트워크와 수신 서버를 중지하는 중…")

        def stop_stack() -> None:
            self.server.stop()
            self.softap.stop()
            self.ui_events.put(("stopped", None))

        threading.Thread(target=stop_stack, daemon=True).start()

    def _handle_transfer_progress(self, item: TransferProgress) -> None:
        percent = item.received / item.total * 100 if item.total else 100
        self.progress_var.set(percent)
        if item.error:
            self.status_var.set(f"수신 실패 · {item.filename}")
            self.detail_var.set(item.error)
        elif item.completed:
            self.status_var.set(f"완료 · {item.filename}")
            self.detail_var.set(f"{item.total / 1048576:.2f} MB · {item.bytes_per_second / 1048576:.2f} MB/s · SHA-256 확인됨")
        else:
            self.status_var.set(f"수신 중 · {item.filename} · {percent:.1f}%")
            self.detail_var.set(
                f"{item.received / 1048576:.2f} / {item.total / 1048576:.2f} MB · "
                f"{item.bytes_per_second / 1048576:.2f} MB/s · ETA {format_duration(item.eta_seconds)}"
            )

    def _poll_events(self) -> None:
        try:
            while True:
                self._handle_transfer_progress(self.transfer_events.get_nowait())
        except queue.Empty:
            pass

        try:
            while True:
                kind, payload = self.ui_events.get_nowait()
                if kind == "started":
                    self._show_pairing_qr(payload)
                elif kind == "start_error":
                    self.starting = False
                    self.server_button.configure(state="normal")
                    self._update_mode_text()
                    self.status_var.set("자동 오프라인 네트워크 시작 실패")
                    self.detail_var.set(str(payload))
                    messagebox.showerror(
                        APP_TITLE,
                        "전용 Wi-Fi를 시작하지 못했습니다.\n\n"
                        f"{payload}\n\n"
                        "Wi-Fi를 켜고 Windows 모바일 핫스팟을 끈 뒤 다시 시도하세요. "
                        "어댑터가 레거시 SoftAP를 지원하지 않으면 자동 생성을 끄고 수동 폴백을 사용하세요.",
                    )
                elif kind == "stopped":
                    self.starting = False
                    self.qr_photo = None
                    self.qr_label.configure(image="", text="네트워크와 수신 서버가 중지되었습니다.")
                    self.server_button.configure(state="normal")
                    self._update_mode_text()
                    self.status_var.set("중지됨")
                    self.detail_var.set("")
                    self.progress_var.set(0)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def on_close(self) -> None:
        self.server.stop()
        self.softap.stop()
        self.destroy()


if __name__ == "__main__":
    OfflineReceiverApp().mainloop()
