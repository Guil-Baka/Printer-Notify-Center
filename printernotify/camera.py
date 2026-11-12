"""Camera snapshots and FFmpeg-based timelapse rendering."""
from __future__ import annotations

import os
import random
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
import requests

MAX_IMAGE_BYTES = 20 * 1024 * 1024
_RETRY_INITIAL_SECONDS = 15
_RETRY_MAX_SECONDS = 300
_streams: dict[str, tuple["RTSPStream", int]] = {}
_streams_lock = threading.Lock()
_blocked_urls: set[str] = set()
_blocked_lock = threading.Lock()


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            process.terminate()
        except (ProcessLookupError, PermissionError):
            if process.poll() is None:
                raise


def validate_camera_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme in ("rtmp", "rtmps"):
        raise ValueError("RTMP is no longer supported. Enter the camera's RTSP or HTTP(S) snapshot URL.")
    if parsed.scheme not in ("http", "https", "rtsp", "rtsps") or not parsed.hostname:
        raise ValueError("Use an HTTP(S) snapshot URL or an RTSP(S) stream URL.")


class RTSPStream:
    """Continuously decode a stream, keeping only its most recent frame."""

    def __init__(self, url: str):
        self.url = url
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._process: subprocess.Popen | None = None
        self._frame: bytes | None = None
        self._frame_time = 0.0
        self._error: str | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def snapshot(self) -> bytes:
        deadline = time.monotonic() + 15
        with self._condition:
            while True:
                if self._stop.is_set():
                    raise RuntimeError("RTSP stream was closed.")
                if self._frame is not None and time.monotonic() - self._frame_time < 20:
                    return self._frame
                if self._error:
                    raise RuntimeError(self._error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("RTSP camera timed out. Check the host, port and stream path.")
                self._condition.wait(remaining)

    def close(self) -> None:
        self._stop.set()
        with self._condition:
            process = self._process
            self._condition.notify_all()
        if process is not None:
            _terminate_process(process)
        self._thread.join()

    def _read_frames(self, process: subprocess.Popen) -> None:
        assert process.stdout is not None
        buffer = bytearray()
        while not self._stop.is_set():
            chunk = process.stdout.read1(65536)
            if not chunk:
                return
            buffer.extend(chunk)
            while True:
                start = buffer.find(b"\xff\xd8")
                if start < 0:
                    del buffer[:-1]
                    break
                if start:
                    del buffer[:start]
                end = buffer.find(b"\xff\xd9", 2)
                if end < 0:
                    if len(buffer) > MAX_IMAGE_BYTES:
                        with self._condition:
                            self._error = "Camera frame exceeds the 20 MB limit."
                            self._condition.notify_all()
                        _terminate_process(process)
                        return
                    break
                if end + 2 > MAX_IMAGE_BYTES:
                    with self._condition:
                        self._error = "Camera frame exceeds the 20 MB limit."
                        self._condition.notify_all()
                    _terminate_process(process)
                    return
                frame = bytes(buffer[:end + 2])
                del buffer[:end + 2]
                with self._condition:
                    self._frame = frame
                    self._frame_time = time.monotonic()
                    self._error = None
                    self._condition.notify_all()

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            with _blocked_lock:
                blocked = self.url in _blocked_urls
            if blocked:
                with self._condition:
                    self._error = "RTSP authentication failed; retries are paused. Check credentials and restart the app."
                    self._condition.notify_all()
                return
            try:
                process = subprocess.Popen(
                    [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                     "-i", self.url, "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except (OSError, RuntimeError):
                with self._condition:
                    self._error = "Could not start FFmpeg for the RTSP camera."
                    self._condition.notify_all()
            else:
                with self._condition:
                    self._process = process
                    self._frame = None
                    self._frame_time = time.monotonic()
                    started = self._frame_time
                    self._error = None
                errors = bytearray()

                def read_errors() -> None:
                    assert process.stderr is not None
                    while True:
                        chunk = process.stderr.read1(4096)
                        if not chunk:
                            return
                        errors.extend(chunk)
                        if len(errors) > 16384:
                            del errors[:-16384]

                reader = threading.Thread(target=self._read_frames, args=(process,), daemon=True)
                stderr_reader = threading.Thread(target=read_errors, daemon=True)
                reader.start()
                stderr_reader.start()
                while process.poll() is None:
                    if self._stop.wait(1):
                        _terminate_process(process)
                        break
                    with self._condition:
                        stalled = time.monotonic() - self._frame_time >= 20
                    if stalled:
                        _terminate_process(process)
                        break
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                reader.join()
                stderr_reader.join()
                assert process.stdout is not None and process.stderr is not None
                process.stdout.close()
                process.stderr.close()
                with self._condition:
                    self._process = None
                    received_frame = self._frame is not None
                    self._frame = None
                    if not self._stop.is_set():
                        detail = bytes(errors).lower()
                        auth_failed = any(text in detail for text in (
                            b"401 unauthorized", b"403 forbidden",
                            b"server returned 401", b"server returned 403",
                        ))
                        if auth_failed:
                            with _blocked_lock:
                                _blocked_urls.add(self.url)
                            self._error = "RTSP authentication failed; retries are paused. Check credentials and restart the app."
                        elif not self._error:
                            self._error = "RTSP stream disconnected or timed out; retrying."
                        self._condition.notify_all()
                if self._stop.is_set() or auth_failed:
                    return
                if received_frame and time.monotonic() - started >= 60:
                    failures = 0
            delay = min(_RETRY_INITIAL_SECONDS * 2 ** min(failures, 5), _RETRY_MAX_SECONDS)
            failures += 1
            self._stop.wait(delay + random.uniform(0, delay * 0.2))


def acquire_rtsp_stream(url: str) -> RTSPStream:
    validate_camera_url(url)
    if urlparse(url).scheme not in ("rtsp", "rtsps"):
        raise ValueError("An RTSP(S) stream URL is required.")
    with _streams_lock:
        entry = _streams.get(url)
        if entry is None:
            stream = RTSPStream(url)
            _streams[url] = (stream, 1)
            return stream
        stream, references = entry
        _streams[url] = (stream, references + 1)
        return stream


def release_rtsp_stream(stream: RTSPStream) -> None:
    with _streams_lock:
        current, references = _streams[stream.url]
        if current is not stream:
            raise ValueError("RTSP stream does not match the active connection.")
        if references > 1:
            _streams[stream.url] = (stream, references - 1)
        else:
            del _streams[stream.url]
            stream.close()


def capture_frame(url: str) -> bytes:
    validate_camera_url(url)
    if urlparse(url).scheme in ("http", "https"):
        with requests.get(url, stream=True, timeout=(5, 10)) as response:
            response.raise_for_status()
            parts = []
            size = 0
            for part in response.iter_content(64 * 1024):
                size += len(part)
                if size > MAX_IMAGE_BYTES:
                    raise ValueError("Camera snapshot exceeds the 20 MB limit.")
                parts.append(part)
        frame = b"".join(parts)
    else:
        try:
            result = subprocess.run(
                [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                 "-i", url, "-frames:v", "1", "-f", "image2pipe", "-vcodec",
                 "mjpeg", "pipe:1"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "RTSP camera timed out. Check the host, port and stream path."
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", errors="replace").lower()
            if any(reason in detail for reason in (
                "cannot open connection", "connection refused", "connection timed out",
                "network is unreachable", "no route to host",
            )):
                raise RuntimeError(
                    "Could not connect to the RTSP camera. Check the host, port and network."
                ) from exc
            raise RuntimeError(
                "Could not read the RTSP stream. Check the full stream path and camera settings."
            ) from exc
        frame = result.stdout
        if len(frame) > MAX_IMAGE_BYTES:
            raise ValueError("Camera snapshot exceeds the 20 MB limit.")

    if not frame.startswith(b"\xff\xd8") or not frame.endswith(b"\xff\xd9"):
        raise ValueError("Camera did not return a complete JPEG image.")
    return frame


def render_timelapse(directory: Path, fps: int, output: Path | None = None) -> Path:
    output = output or directory / "timelapse.mp4"
    temporary = directory / "timelapse.partial.mp4"
    if output.exists():
        raise FileExistsError(f"Timelapse output already exists: {output}")
    try:
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
             "-y", "-framerate", str(fps), "-i", str(directory / "frame-%06d.jpg"),
             "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=600,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if output.exists():
            raise FileExistsError(f"Timelapse output already exists: {output}")
        temporary.rename(output)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timelapse rendering timed out; photos were retained.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Timelapse rendering failed: {detail}") from exc
    return output
