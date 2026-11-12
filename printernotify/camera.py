"""Camera snapshots and FFmpeg-based timelapse rendering."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
import requests

MAX_IMAGE_BYTES = 20 * 1024 * 1024


def validate_camera_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme in ("rtmp", "rtmps"):
        raise ValueError("RTMP is no longer supported. Enter the camera's RTSP or HTTP(S) snapshot URL.")
    if parsed.scheme not in ("http", "https", "rtsp", "rtsps") or not parsed.hostname:
        raise ValueError("Use an HTTP(S) snapshot URL or an RTSP(S) stream URL.")


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


def render_timelapse(directory: Path, fps: int) -> Path:
    output = directory / "timelapse.mp4"
    temporary = directory / "timelapse.partial.mp4"
    try:
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
             "-y", "-framerate", str(fps), "-i", str(directory / "frame-%06d.jpg"),
             "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=600,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        temporary.replace(output)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timelapse rendering timed out; photos were retained.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Timelapse rendering failed: {detail}") from exc
    return output
