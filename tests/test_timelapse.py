import http.server
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import imageio_ffmpeg

from printernotify.camera import capture_frame, render_timelapse, validate_camera_url
from printernotify.models import CameraSettings, Printer
from printernotify.timelapse import TimelapseRecorder


def status(state="printing", progress=0.0, filename="part.gcode"):
    return {
        "print_stats": {"state": state, "filename": filename},
        "virtual_sdcard": {"progress": progress},
    }


class TimelapseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = CameraSettings(
            url="http://localhost/snapshot", timelapse_enabled=True,
            capture_mode="percentage", interval=0.5, output_dir=self.temp.name,
        )
        self.printer = Printer("Printer", "localhost", camera=self.settings)
        self.notifier = Mock()
        self.recorder = TimelapseRecorder(self.printer, self.notifier)

    def test_settings_roundtrip_and_backward_compatibility(self):
        old = Printer.from_dict({"name": "Old", "host": "localhost"})
        self.assertFalse(old.camera.timelapse_enabled)
        restored = Printer.from_dict(self.printer.to_dict())
        self.assertEqual(self.settings.to_dict(), restored.camera.to_dict())

    @patch("printernotify.timelapse.render_timelapse")
    @patch("printernotify.timelapse.capture_frame", return_value=b"frame")
    def test_percentage_thresholds_and_print_completion(self, capture, render):
        self.recorder._update(status(progress=0.0))
        self.recorder._update(status(progress=0.0049))
        self.assertEqual(1, capture.call_count)
        self.recorder._update(status(progress=0.005))
        self.recorder._update(status(progress=0.0099))
        self.assertEqual(2, capture.call_count)
        self.recorder._update(status(progress=0.01))
        self.assertEqual(3, capture.call_count)
        directory = self.recorder._directory
        self.assertEqual(
            ["frame-000001.jpg", "frame-000002.jpg", "frame-000003.jpg"],
            sorted(p.name for p in directory.iterdir()),
        )
        self.recorder._update(status(state="complete", progress=1.0))
        render.assert_called_once_with(directory, 30)
        self.assertEqual(4, capture.call_count)
        self.assertIsNone(self.recorder._directory)

    @patch("printernotify.timelapse.time.monotonic")
    @patch("printernotify.timelapse.capture_frame", return_value=b"frame")
    def test_time_threshold_pause_offline_and_resume(self, capture, clock):
        self.settings.capture_mode = "time"
        self.settings.interval = 2.5
        clock.return_value = 10
        self.recorder._update(status())
        self.assertEqual(1, capture.call_count)
        clock.return_value = 12.49
        self.recorder._update(status())
        self.assertEqual(1, capture.call_count)
        clock.return_value = 12.5
        self.recorder._update(status())
        self.assertEqual(2, capture.call_count)
        self.recorder._update(status(state="paused"))
        self.assertFalse(self.recorder._active)
        self.recorder._update(None)
        self.assertFalse(self.recorder._active)
        clock.return_value = 16
        self.recorder._update(status())
        self.assertEqual(3, capture.call_count)

    @patch("printernotify.timelapse.render_timelapse")
    @patch("printernotify.timelapse.capture_frame", return_value=b"frame")
    def test_cancellation_error_and_new_print_finalize(self, capture, render):
        for state in ("cancelled", "error"):
            self.recorder._update(status())
            directory = self.recorder._directory
            self.recorder._update(status(state=state))
            render.assert_called_with(directory, 30)
        self.recorder._update(status(filename="first.gcode"))
        directory = self.recorder._directory
        self.recorder._update(status(filename="second.gcode"))
        render.assert_called_with(directory, 30)
        self.assertNotEqual(directory, self.recorder._directory)

    @patch("printernotify.timelapse.capture_frame", side_effect=RuntimeError("unavailable"))
    def test_capture_error_is_notified_once_and_retried(self, capture):
        self.recorder._update(status())
        self.recorder._update(status())
        self.assertEqual(1, capture.call_count)
        self.notifier.notify.assert_called_once()
        self.assertEqual(0, self.recorder._frame_count)

    @patch("printernotify.timelapse.capture_frame", return_value=b"frame")
    def test_worker_captures_by_time_without_another_status_poll(self, capture):
        self.settings.capture_mode = "time"
        self.settings.interval = 0.1
        self.recorder.start()
        try:
            self.recorder.submit(status())
            deadline = time.monotonic() + 3
            while capture.call_count < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(capture.call_count, 2)
            self.recorder.submit(None)
            deadline = time.monotonic() + 3
            while self.recorder._active and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(self.recorder._active)
            calls = capture.call_count
            time.sleep(0.2)
            self.assertEqual(calls, capture.call_count)
        finally:
            self.recorder.stop()
            self.recorder._thread.join(timeout=3)
            self.assertFalse(self.recorder._thread.is_alive())

    @patch("printernotify.timelapse.render_timelapse", side_effect=RuntimeError("encoder failed"))
    @patch("printernotify.timelapse.capture_frame", return_value=b"frame")
    def test_render_failure_notifies_and_preserves_photos(self, capture, render):
        self.recorder._update(status())
        directory = self.recorder._directory
        self.recorder._update(status(state="error"))
        self.assertTrue((directory / "frame-000001.jpg").is_file())
        self.assertIn("retained", self.notifier.notify.call_args.args[1])

    @patch("printernotify.camera.subprocess.run")
    def test_rtmp_capture_uses_ffmpeg(self, run):
        run.return_value.stdout = b"\xff\xd8frame\xff\xd9"
        self.assertEqual(b"\xff\xd8frame\xff\xd9", capture_frame("rtmp://host/live"))
        command = run.call_args.args[0]
        self.assertEqual("rtmp://host/live", command[command.index("-i") + 1])
        self.assertIn("image2pipe", command)

    @patch("printernotify.camera.subprocess.run")
    def test_rtmp_errors_are_actionable_and_do_not_expose_credentials(self, run):
        url = "rtmp://user:secret@host/live?token=secret"
        run.side_effect = subprocess.CalledProcessError(
            1, [], stderr=f"Cannot open connection to {url}".encode()
        )
        with self.assertRaises(RuntimeError) as caught:
            capture_frame(url)
        self.assertIn("connect", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        run.side_effect = subprocess.CalledProcessError(
            1, [], stderr=f"Stream not found at {url}".encode()
        )
        with self.assertRaises(RuntimeError) as caught:
            capture_frame(url)
        self.assertIn("stream path", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_url_validation(self):
        for value in ("http://host/snapshot", "https://host/camera", "rtmp://host/live"):
            validate_camera_url(value)
        for value in ("", "file:///test", "http://", "ftp://host/file"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_camera_url(value)

    def test_actual_http_snapshot_and_mp4_encoding(self):
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        frame = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "color=c=red:s=64x64", "-frames:v", "1", "-f", "image2pipe",
             "-vcodec", "mjpeg", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30,
        ).stdout

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)

            def log_message(self, format, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            captured = capture_frame(f"http://127.0.0.1:{server.server_port}/snapshot")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(frame, captured)
        directory = Path(self.temp.name)
        (directory / "frame-000001.jpg").write_bytes(captured)
        (directory / "frame-000002.jpg").write_bytes(captured)
        output = render_timelapse(directory, 30)
        self.assertTrue(output.is_file())
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(output),
             "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
