"""Print-aware timelapse scheduling, capture and rendering."""
from __future__ import annotations

import logging
import math
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from platformdirs import user_videos_path

from .camera import capture_frame, render_timelapse, validate_camera_url
from .models import Printer

logger = logging.getLogger(__name__)


class TimelapseRecorder:
    def __init__(self, printer: Printer, notifier):
        self.printer = printer
        self.notifier = notifier
        self._events: queue.Queue[dict | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._directory: Path | None = None
        self._filename = ""
        self._frame_count = 0
        self._next_time = 0.0
        self._next_percent: float | None = None
        self._active = False
        self._error_reported = False

    def start(self) -> None:
        self._thread.start()

    def submit(self, status: dict | None) -> None:
        self._events.put(status)

    def stop(self) -> None:
        self._events.put({"_stop": True})

    def _report_error(self, message: str, exc: Exception) -> None:
        logger.error("%s: %s", message, exc)
        if not self._error_reported:
            self.notifier.notify(f"{self.printer.name} timelapse", f"{message}: {exc}")
            self._error_reported = True

    def _begin(self, filename: str) -> None:
        settings = self.printer.camera
        base = Path(settings.output_dir).expanduser() if settings.output_dir else (
            user_videos_path() / "Printer Notify Center"
        )
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._directory = base / f"{stamp}-{self.printer.id}-{uuid.uuid4().hex[:8]}"
        self._directory.mkdir(parents=True, exist_ok=False)
        self._filename = filename
        self._frame_count = 0
        self._next_time = 0.0
        self._next_percent = None
        self._error_reported = False

    def _finish(self) -> None:
        directory = self._directory
        if directory is not None and self._frame_count:
            try:
                render_timelapse(directory, self.printer.camera.fps)
            except (OSError, RuntimeError, ValueError) as exc:
                self._error_reported = False
                self._report_error("Could not create video; photos were retained", exc)
        self._directory = None
        self._active = False

    def _capture(self, progress: float | None) -> None:
        settings = self.printer.camera
        succeeded = False
        try:
            frame = capture_frame(settings.url)
            if self._directory is None:
                raise RuntimeError("Timelapse output directory is unavailable.")
            path = self._directory / f"frame-{self._frame_count + 1:06d}.jpg"
            path.write_bytes(frame)
            self._frame_count += 1
            succeeded = True
            self._error_reported = False
            if settings.capture_mode == "percentage" and progress is not None:
                self._next_percent = (math.floor(progress / settings.interval + 1e-9) + 1) * settings.interval
        except requests.RequestException:
            self._report_error("Camera capture failed", RuntimeError("HTTP request failed; check the camera URL and connection."))
        except (OSError, ValueError, RuntimeError) as exc:
            self._report_error("Camera capture failed", exc)
        if settings.capture_mode == "percentage" and succeeded:
            self._next_time = 0.0
        else:
            delay = settings.interval if settings.capture_mode == "time" else 5.0
            self._next_time = time.monotonic() + max(delay, 0.1)

    def _update(self, status: dict | None) -> None:
        settings = self.printer.camera
        if not settings.timelapse_enabled:
            if self._directory is not None:
                self._finish()
            return
        try:
            validate_camera_url(settings.url)
            if settings.capture_mode not in ("time", "percentage"):
                raise ValueError("Invalid capture mode.")
            if not math.isfinite(settings.interval) or settings.interval <= 0:
                raise ValueError("The capture interval must be a positive finite number.")
            if settings.capture_mode == "time" and settings.interval < 0.1:
                raise ValueError("The time interval must be at least 0.1 seconds.")
            if settings.capture_mode == "percentage" and settings.interval > 100:
                raise ValueError("The percentage interval cannot exceed 100.")
            if not 1 <= settings.fps <= 120:
                raise ValueError("Video FPS must be between 1 and 120.")
        except (TypeError, ValueError) as exc:
            self._active = False
            self._report_error("Invalid timelapse settings", exc)
            return
        if status is None:
            self._active = False
            return
        stats = status.get("print_stats", {})
        state = stats.get("state")
        filename = str(stats.get("filename") or "print")
        if state in ("complete", "cancelled", "error", "standby"):
            if self._directory is not None:
                progress = status.get("virtual_sdcard", {}).get("progress")
                if (
                    state == "complete"
                    and settings.capture_mode == "percentage"
                    and isinstance(progress, (int, float))
                    and math.isfinite(progress)
                    and 0 <= progress <= 1
                    and (self._next_percent is None or progress * 100 >= self._next_percent)
                ):
                    self._capture(progress * 100)
                self._finish()
            return
        self._active = state == "printing"
        if not self._active:
            return
        if self._directory is not None and filename != self._filename:
            self._finish()
        if self._directory is None:
            try:
                self._begin(filename)
            except (OSError, ValueError) as exc:
                self._report_error("Could not create timelapse directory", exc)
                self._active = False
                return
        progress = status.get("virtual_sdcard", {}).get("progress")
        percent = float(progress) * 100 if isinstance(progress, (int, float)) else None
        if settings.capture_mode == "percentage":
            if percent is None or not math.isfinite(percent) or not 0 <= percent <= 100:
                self._report_error(
                    "Cannot capture by progress",
                    ValueError("Moonraker did not report a valid print progress."),
                )
            elif (self._next_percent is None or percent >= self._next_percent) and time.monotonic() >= self._next_time:
                self._capture(percent)
        elif time.monotonic() >= self._next_time:
            self._capture(percent)

    def _run(self) -> None:
        while True:
            timeout = None
            if self._active and self.printer.camera.capture_mode == "time":
                timeout = max(0.0, self._next_time - time.monotonic())
            try:
                status = self._events.get(timeout=timeout)
            except queue.Empty:
                self._capture(None)
                continue
            if status is not None and status.get("_stop"):
                return
            self._update(status)
