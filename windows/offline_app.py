from __future__ import annotations

import os
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk
import qrcode
from qrcode.constants import ERROR_CORRECT_M

from offline_server import DEFAULT_PORT, OfflineReceiverServer, TransferProgress, discover_private_ipv4

APP_TITLE = "QRBeam Offline 0.3"


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "계산 중…"
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class OfflineReceiverApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x740")
        self.minsize(900, 620)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.events: queue.Queue[TransferProgress] = queue.Queue()
        self.server = OfflineReceiverServer(self.events.put)
        self.qr_photo: ImageTk.PhotoImage | None = None

        self.ssid_var = tk.StringVar(value="")
        self.password_var = tk.StringVar(value="")
        self.host_var = tk.StringVar(value=discover_private_ipv4()[0])
        self.port_var = tk.IntVar(value=DEFAULT_PORT)
        self.output_var = tk.StringVar(value=str(Path.home() / "Downloads" / "QRBeam Offline"))
        self.status_var = tk.StringVar(value="Windows 모바일 핫스팟을 켠 뒤 수신 서버를 시작하세요.")
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

        self.qr_label = ttk.Label(left, text="수신 서버를 시작하면 연결 QR이 표시됩니다.", anchor="center")
        self.qr_label.grid(row=0, column=0, sticky="nsew")
        ttk.Progressbar(left, maximum=100, variable=self.progress_var).grid(row=1, column=0, sticky="ew", pady=(14, 8))
        ttk.Label(left, textvariable=self.status_var, font=("Segoe UI", 12, "bold"), wraplength=680).grid(row=2, column=0, sticky="w")
        ttk.Label(left, textvariable=self.detail_var, wraplength=680).grid(row=3, column=0, sticky="w", pady=(5, 0))

        ttk.Label(right, text="오프라인 고속 수신", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        fields = [
            ("핫스팟 이름", self.ssid_var, False),
            ("핫스팟 비밀번호", self.password_var, True),
            ("Windows 주소", self.host_var, False),
            ("포트", self.port_var, False),
            ("저장 폴더", self.output_var, False),
        ]
        for row, (title, variable, secret) in enumerate(fields, start=1):
            ttk.Label(right, text=title).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(right, textvariable=variable, width=34, show="•" if secret else "").grid(row=row, column=1, sticky="ew", pady=5)

        ttk.Button(right, text="저장 폴더 선택", command=self.choose_output).grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Button(right, text="Windows 핫스팟 설정 열기", command=self.open_hotspot_settings).grid(row=7, column=0, columnspan=2, sticky="ew", pady=4)
        self.server_button = ttk.Button(right, text="오프라인 수신 시작", command=self.toggle_server)
        self.server_button.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(12, 4))

        ttk.Separator(right).grid(row=9, column=0, columnspan=2, sticky="ew", pady=16)
        ttk.Label(
            right,
            text=(
                "Windows 설정의 실제 핫스팟 이름과 비밀번호를 입력하세요. "
                "파일 본문은 로컬 HTTP로 전송하며, 핫스팟 접근 제한과 일회용 토큰을 사용합니다. "
                "수신 중에는 임시 파일로 저장하고 최종 SHA-256이 맞을 때만 완료합니다."
            ),
            wraplength=330,
            justify="left",
        ).grid(row=10, column=0, columnspan=2, sticky="w")

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
        self.stop_server() if self.server.running else self.start_server()

    def start_server(self) -> None:
        if not self.ssid_var.get().strip() or len(self.password_var.get()) < 8:
            messagebox.showwarning(APP_TITLE, "실제 핫스팟 이름과 8자 이상의 비밀번호를 입력하세요.")
            return
        try:
            port = self.server.start(self.output_var.get(), int(self.port_var.get()))
            info = self.server.pairing_info(self.ssid_var.get().strip(), self.password_var.get(), self.host_var.get().strip())
            qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=4, box_size=8)
            qr.add_data(info.encode())
            qr.make(fit=True)
            image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            image.thumbnail((610, 610))
            self.qr_photo = ImageTk.PhotoImage(image)
            self.qr_label.configure(image=self.qr_photo, text="")
            self.server_button.configure(text="수신 서버 중지")
            self.status_var.set(f"수신 대기 중 · {info.host}:{port}")
            self.detail_var.set("iPad QRBeam의 오프라인 탭에서 연결 QR을 스캔하세요.")
            self.progress_var.set(0)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"수신 서버 시작 실패\n\n{exc}")

    def stop_server(self) -> None:
        self.server.stop()
        self.qr_photo = None
        self.qr_label.configure(image="", text="수신 서버가 중지되었습니다.")
        self.server_button.configure(text="오프라인 수신 시작")
        self.status_var.set("수신 서버 중지")
        self.detail_var.set("")
        self.progress_var.set(0)

    def _poll_events(self) -> None:
        try:
            while True:
                item = self.events.get_nowait()
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
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def on_close(self) -> None:
        self.server.stop()
        self.destroy()


if __name__ == "__main__":
    OfflineReceiverApp().mainloop()
