from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from collections import OrderedDict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk
import qrcode
from qrcode.constants import ERROR_CORRECT_M

from protocol import DEFAULT_CHUNK_SIZE, ProtocolError, TransferPackage, decode_packet
from receiver_state import ReceiverStore

APP_TITLE = "QRBeam 0.2"
QR_PIXELS = 720
SPEED_PROFILES = {
    "안정 · 15 QR/s": 15,
    "고속 · 30 QR/s": 30,
    "터보 · 60 QR/s": 60,
}


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "계산 중…"
    value = int(round(seconds))
    hours, value = divmod(value, 3600)
    minutes, secs = divmod(value, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class CameraWorker:
    def __init__(self, camera_index: int, message_queue: queue.Queue[tuple[str, object]]) -> None:
        self.camera_index = camera_index
        self.message_queue = message_queue
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.latest_frame: Image.Image | None = None
        self.frame_lock = threading.Lock()
        self.detector = cv2.QRCodeDetector()
        self.last_value = ""
        self.last_seen = 0.0

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)

    def _run(self) -> None:
        capture = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not capture.isOpened():
            capture = cv2.VideoCapture(self.camera_index)
        if not capture.isOpened():
            self.message_queue.put(("camera_error", f"카메라 {self.camera_index}를 열 수 없습니다."))
            return

        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.message_queue.put(("camera_started", self.camera_index))

        try:
            while not self.stop_event.is_set():
                ok, frame = capture.read()
                if not ok:
                    time.sleep(0.03)
                    continue

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                preview = Image.fromarray(rgb)
                with self.frame_lock:
                    self.latest_frame = preview

                value, _, _ = self.detector.detectAndDecode(frame)
                if value:
                    now = time.monotonic()
                    if value != self.last_value or now - self.last_seen > 0.12:
                        self.last_value = value
                        self.last_seen = now
                        self.message_queue.put(("qr", value))
        finally:
            capture.release()
            self.message_queue.put(("camera_stopped", None))

    def get_preview(self) -> Image.Image | None:
        with self.frame_lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None


class QRBeamApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x800")
        self.minsize(960, 700)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.outbound: TransferPackage | None = None
        self.send_counter = 0
        self.send_job: str | None = None
        self.send_photo: ImageTk.PhotoImage | None = None
        self.qr_cache: OrderedDict[str, Image.Image] = OrderedDict()
        self.send_started_at: float | None = None
        self.send_elapsed_before_start = 0.0
        self.metadata_restore_job: str | None = None

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.camera: CameraWorker | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.receiver_store = ReceiverStore()
        self.saved_transfer_ids: set[str] = set()

        self._build_ui()
        self.after(40, self._poll_events)
        self.after(80, self._refresh_preview)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)

        self.send_tab = ttk.Frame(notebook, padding=16)
        self.receive_tab = ttk.Frame(notebook, padding=16)
        notebook.add(self.send_tab, text="보내기")
        notebook.add(self.receive_tab, text="받기 · 실험")

        self._build_send_tab()
        self._build_receive_tab()

    def _build_send_tab(self) -> None:
        self.send_tab.columnconfigure(0, weight=1)
        self.send_tab.columnconfigure(1, weight=0)
        self.send_tab.rowconfigure(1, weight=1)

        controls = ttk.Frame(self.send_tab)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        controls.columnconfigure(1, weight=1)

        ttk.Button(controls, text="파일 선택", command=self.choose_file).grid(row=0, column=0, padx=(0, 10))
        self.file_label = ttk.Label(controls, text="선택된 파일 없음")
        self.file_label.grid(row=0, column=1, sticky="w")

        ttk.Label(controls, text="속도").grid(row=0, column=2, padx=(12, 6))
        self.profile_var = tk.StringVar(value="안정 · 15 QR/s")
        profile_box = ttk.Combobox(
            controls,
            state="readonly",
            values=list(SPEED_PROFILES),
            width=17,
            textvariable=self.profile_var,
        )
        profile_box.grid(row=0, column=3)

        ttk.Label(controls, text="청크").grid(row=0, column=4, padx=(12, 6))
        self.chunk_var = tk.IntVar(value=DEFAULT_CHUNK_SIZE)
        ttk.Spinbox(controls, from_=600, to=1400, increment=100, width=7, textvariable=self.chunk_var).grid(
            row=0, column=5
        )

        self.start_button = ttk.Button(controls, text="전송 시작", command=self.toggle_send, state="disabled")
        self.start_button.grid(row=0, column=6, padx=(12, 0))

        qr_container = ttk.Frame(self.send_tab)
        qr_container.grid(row=1, column=0, sticky="nsew")
        qr_container.columnconfigure(0, weight=1)
        qr_container.rowconfigure(0, weight=1)

        self.qr_label = ttk.Label(qr_container, anchor="center", text="파일을 선택하면 QR이 표시됩니다.")
        self.qr_label.grid(row=0, column=0, sticky="nsew")

        side = ttk.Frame(self.send_tab, padding=(18, 0, 0, 0))
        side.grid(row=1, column=1, sticky="ns")
        ttk.Label(side, text="전송 상태", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        self.send_status = ttk.Label(side, text="대기 중", wraplength=290, justify="left")
        self.send_status.pack(anchor="w", pady=(12, 8))
        self.send_progress = ttk.Progressbar(side, mode="determinate", length=290)
        self.send_progress.pack(anchor="w", pady=(0, 12))

        self.metadata_button = ttk.Button(
            side,
            text="파일 정보 QR 보내기 · 3초",
            command=self.show_metadata,
            state="disabled",
        )
        self.metadata_button.pack(anchor="w", fill="x", pady=(6, 4))
        ttk.Label(
            side,
            text=(
                "개인정보 보호: 파일명·확장자·수정 시각은 기본 전송에 포함되지 않습니다. "
                "위 버튼을 누를 때만 별도 QR로 전송됩니다."
            ),
            wraplength=290,
            justify="left",
        ).pack(anchor="w", pady=(4, 14))

        ttk.Separator(side).pack(fill="x", pady=8)
        ttk.Label(
            side,
            text=(
                "기본 안정 모드: 화면 60Hz 기준 QR을 4프레임 유지해 15 QR/s로 전송합니다. "
                "8개 데이터마다 복구 QR 하나를 보내며, 최종 SHA-256이 다르면 저장되지 않습니다."
            ),
            wraplength=290,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

    def _build_receive_tab(self) -> None:
        self.receive_tab.columnconfigure(0, weight=1)
        self.receive_tab.columnconfigure(1, weight=0)
        self.receive_tab.rowconfigure(2, weight=1)

        warning = ttk.Label(
            self.receive_tab,
            text=(
                "실험 기능: 노트북 내장 전면 카메라는 화면 반사와 노출 때문에 인식률이 낮습니다. "
                "USB 웹캠이나 캡처 장치를 권장합니다."
            ),
            foreground="#9a5b00",
            wraplength=1000,
        )
        warning.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        controls = ttk.Frame(self.receive_tab)
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Label(controls, text="카메라 번호").pack(side="left")
        self.camera_index_var = tk.IntVar(value=0)
        ttk.Spinbox(controls, from_=0, to=9, width=5, textvariable=self.camera_index_var).pack(
            side="left", padx=(6, 10)
        )
        self.camera_button = ttk.Button(controls, text="카메라 시작", command=self.toggle_camera)
        self.camera_button.pack(side="left")

        ttk.Label(controls, text="저장 폴더").pack(side="left", padx=(20, 6))
        default_output = Path.home() / "Downloads" / "QRBeam Received"
        self.output_dir_var = tk.StringVar(value=str(default_output))
        ttk.Entry(controls, textvariable=self.output_dir_var).pack(side="left", fill="x", expand=True)
        ttk.Button(controls, text="찾기", command=self.choose_output_dir).pack(side="left", padx=(6, 0))

        preview_frame = ttk.Frame(self.receive_tab)
        preview_frame.grid(row=2, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(preview_frame, anchor="center", text="카메라가 꺼져 있습니다.")
        self.preview_label.grid(row=0, column=0, sticky="nsew")

        side = ttk.Frame(self.receive_tab, padding=(18, 0, 0, 0))
        side.grid(row=2, column=1, sticky="ns")
        ttk.Label(side, text="수신 상태", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        self.receive_status = ttk.Label(side, text="대기 중", wraplength=300, justify="left")
        self.receive_status.pack(anchor="w", pady=(12, 8))
        self.receive_progress = ttk.Progressbar(side, mode="determinate", length=300)
        self.receive_progress.pack(anchor="w", pady=(0, 12))
        self.transfer_list = tk.Listbox(side, width=45, height=18)
        self.transfer_list.pack(fill="both", expand=True)

    def choose_file(self) -> None:
        path = filedialog.askopenfilename(title="QR로 보낼 파일 선택")
        if not path:
            return
        try:
            chunk = max(600, min(1400, int(self.chunk_var.get())))
            self.chunk_var.set(chunk)
            package = TransferPackage(path, chunk_size=chunk)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"파일을 준비하지 못했습니다.\n\n{exc}")
            return
        self.stop_send(reset_elapsed=True)
        self.outbound = package
        self.send_counter = 0
        self.qr_cache.clear()
        self.file_label.configure(
            text=f"{package.path.name} · {len(package.data):,} bytes · {package.total:,} data chunks"
        )
        self.start_button.configure(state="normal", text="전송 시작")
        self.metadata_button.configure(state="normal")
        self._show_send_frame()

    def toggle_send(self) -> None:
        if self.send_job is None:
            self.start_send()
        else:
            self.stop_send()

    def target_qr_rate(self) -> int:
        return SPEED_PROFILES.get(self.profile_var.get(), 15)

    def start_send(self) -> None:
        if self.outbound is None:
            return
        if self.send_started_at is None:
            self.send_started_at = time.monotonic()
        self.start_button.configure(text="일시정지")
        interval_ms = max(1, round(1000 / self.target_qr_rate()))
        self._advance_send(interval_ms)

    def stop_send(self, reset_elapsed: bool = False) -> None:
        if self.send_job is not None:
            self.after_cancel(self.send_job)
            self.send_job = None
        if self.send_started_at is not None:
            self.send_elapsed_before_start += time.monotonic() - self.send_started_at
            self.send_started_at = None
        if reset_elapsed:
            self.send_elapsed_before_start = 0.0
        if hasattr(self, "start_button"):
            self.start_button.configure(text="전송 시작")

    def _send_elapsed(self) -> float:
        running = time.monotonic() - self.send_started_at if self.send_started_at is not None else 0.0
        return self.send_elapsed_before_start + running

    def _advance_send(self, interval_ms: int) -> None:
        started = time.monotonic()
        self._show_send_frame()
        self.send_counter += 1
        spent_ms = int((time.monotonic() - started) * 1000)
        self.send_job = self.after(max(1, interval_ms - spent_ms), self._advance_send, interval_ms)

    def _qr_image(self, text: str) -> Image.Image:
        cached = self.qr_cache.get(text)
        if cached is not None:
            self.qr_cache.move_to_end(text)
            return cached
        qr = qrcode.QRCode(
            version=None,
            error_correction=ERROR_CORRECT_M,
            box_size=1,
            border=4,
        )
        qr.add_data(text)
        qr.make(fit=True)
        total_modules = qr.modules_count + qr.border * 2
        qr.box_size = max(2, QR_PIXELS // total_modules)
        image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        self.qr_cache[text] = image
        if len(self.qr_cache) > 24:
            self.qr_cache.popitem(last=False)
        return image

    def _display_wire(self, text: str) -> None:
        image = self._qr_image(text)
        self.send_photo = ImageTk.PhotoImage(image)
        self.qr_label.configure(image=self.send_photo, text="")

    def _show_send_frame(self) -> None:
        if self.outbound is None:
            return
        text, position = self.outbound.frame_for_counter(self.send_counter)
        self._display_wire(text)

        cycle_count = max(1, self.outbound.cycle_frame_count)
        cycle_pos = self.send_counter % cycle_count
        progress = (cycle_pos + 1) / cycle_count * 100
        elapsed = self._send_elapsed()
        actual_rate = self.send_counter / elapsed if elapsed > 0.5 else float(self.target_qr_rate())
        eta = (cycle_count - cycle_pos - 1) / max(0.1, actual_rate)
        byte_rate = actual_rate * self.outbound.chunk_size * (
            self.outbound.total / max(1, self.outbound.cycle_frame_count)
        )
        self.send_progress["value"] = progress
        self.send_status.configure(
            text=(
                f"프레임: {position}\n"
                f"한 바퀴: {cycle_pos + 1:,} / {cycle_count:,} · {progress:.1f}%\n"
                f"경과: {format_duration(elapsed)} · ETA: {format_duration(eta)}\n"
                f"실제: {actual_rate:.1f} QR/s · 약 {byte_rate / 1024:.1f} KB/s\n"
                f"복구: 데이터 {self.outbound.parity_group}개당 XOR 1개\n"
                f"파일 정보: 기본 비공개"
            )
        )

    def show_metadata(self) -> None:
        if self.outbound is None:
            return
        was_sending = self.send_job is not None
        self.stop_send()
        self._display_wire(self.outbound.metadata_frame)
        self.send_status.configure(
            text=(
                "파일 정보 QR 표시 중 · 3초\n"
                "파일명·확장자·MIME·수정 시각이 포함됩니다.\n"
                "데이터 전송은 잠시 멈췄다가 자동으로 재개됩니다."
            )
        )
        if self.metadata_restore_job is not None:
            self.after_cancel(self.metadata_restore_job)

        def restore() -> None:
            self.metadata_restore_job = None
            self._show_send_frame()
            if was_sending:
                self.start_send()

        self.metadata_restore_job = self.after(3000, restore)

    def choose_output_dir(self) -> None:
        directory = filedialog.askdirectory(title="수신 파일 저장 폴더")
        if directory:
            self.output_dir_var.set(directory)

    def toggle_camera(self) -> None:
        if self.camera is not None:
            self.camera.stop()
            self.camera = None
            self.camera_button.configure(text="카메라 시작")
            self.preview_label.configure(image="", text="카메라가 꺼져 있습니다.")
            return

        self.camera = CameraWorker(int(self.camera_index_var.get()), self.events)
        self.camera.start()
        self.camera_button.configure(text="카메라 중지")
        self.receive_status.configure(text="카메라 시작 중…")

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "qr":
                    self._handle_qr(str(payload))
                elif kind == "camera_error":
                    messagebox.showerror(APP_TITLE, str(payload))
                    self.camera = None
                    self.camera_button.configure(text="카메라 시작")
                elif kind == "camera_started":
                    self.receive_status.configure(text=f"카메라 {payload}에서 QR을 찾는 중…")
        except queue.Empty:
            pass
        self.after(40, self._poll_events)

    def _handle_qr(self, text: str) -> None:
        try:
            packet = decode_packet(text)
            transfer = self.receiver_store.accept(packet)
        except ProtocolError:
            return
        except Exception as exc:
            self.receive_status.configure(text=f"프레임 처리 오류: {exc}")
            return

        total = transfer.core.total if transfer.core else packet.total
        received = len(transfer.chunks)
        progress = transfer.progress * 100
        eta = transfer.eta_seconds
        metadata_status = "받음" if transfer.metadata else "숨겨짐 · 일반 이름 사용"
        self.receive_progress["value"] = progress
        self.receive_status.configure(
            text=(
                f"{transfer.display_name}\n"
                f"{received:,} / {total:,} chunks · {progress:.1f}%\n"
                f"경과 {format_duration(transfer.elapsed)} · ETA {format_duration(eta)}\n"
                f"{transfer.chunk_rate:.1f} chunks/s · {transfer.byte_rate / 1024:.1f} KB/s\n"
                f"파일 정보: {metadata_status}"
            )
        )
        self._refresh_transfer_list()

        if transfer.is_complete() and transfer.key not in self.saved_transfer_ids:
            try:
                path = transfer.save(self.output_dir_var.get())
            except Exception as exc:
                self.receive_status.configure(text=f"무결성 검증 또는 복원 실패: {exc}")
                return
            self.saved_transfer_ids.add(transfer.key)
            self.receive_status.configure(
                text=f"완료 · SHA-256 검증 성공\n{path.name}\n{path}\n경과 {format_duration(transfer.elapsed)}"
            )
            self.receive_progress["value"] = 100
            messagebox.showinfo(APP_TITLE, f"파일 복원과 SHA-256 검증이 완료되었습니다.\n\n{path}")
            self._refresh_transfer_list()

    def _refresh_transfer_list(self) -> None:
        self.transfer_list.delete(0, tk.END)
        for transfer in self.receiver_store.transfers.values():
            total = transfer.core.total if transfer.core else "?"
            marker = "✓" if transfer.completed_path else "·"
            self.transfer_list.insert(tk.END, f"{marker} {transfer.display_name} — {len(transfer.chunks)}/{total}")

    def _refresh_preview(self) -> None:
        if self.camera is not None:
            image = self.camera.get_preview()
            if image is not None:
                width = max(480, self.preview_label.winfo_width())
                height = max(360, self.preview_label.winfo_height())
                image.thumbnail((width, height), Image.Resampling.LANCZOS)
                self.preview_photo = ImageTk.PhotoImage(image)
                self.preview_label.configure(image=self.preview_photo, text="")
        self.after(80, self._refresh_preview)

    def on_close(self) -> None:
        self.stop_send()
        if self.metadata_restore_job is not None:
            self.after_cancel(self.metadata_restore_job)
        if self.camera is not None:
            self.camera.stop()
        self.destroy()


def main() -> None:
    app = QRBeamApp()
    app.mainloop()


if __name__ == "__main__":
    main()
