# Printer Notify Center

A small cross-platform (Windows / macOS / Linux) desktop app that watches one or more
[Moonraker](https://moonraker.readthedocs.io/) API endpoints (Klipper printers) and notifies
you when configured targets are hit — e.g. print finished, a temperature reached, print
progress crossed a threshold, or the printer entered an error state.

## Features

- Configure any number of Moonraker endpoints (host, port, optional API key, poll interval).
- Define arbitrary "targets": watch any Moonraker object field with an operator
  (`>=`, `<=`, `>`, `<`, `==`, `!=`) and a threshold value. Notifies once when the
  condition transitions from not-met to met.
- OS native notifications (Windows toast, macOS notification center, Linux libnotify)
  via `plyer`, with automatic fallback to an in-app popup if native notifications
  aren't available on the current OS.
- A settings toggle to force in-app popups on, or disable native notifications entirely,
  for users who prefer not to use OS notifications.
- Config persisted as JSON in the OS-appropriate user config directory.
- One manually configured RTMP camera or Mainsail-compatible JPEG snapshot URL per
  printer, with a periodically refreshed preview.
- Timelapses captured by elapsed time or print progress, with photos and an MP4
  saved locally when a print completes, is cancelled, or errors.

## Setup

```powershell
pip install -r requirements.txt
python main.py
```

## Cameras and timelapses

On a printer card, select the camera icon. Enter an `rtmp://` or `rtmps://`
stream URL, or the full `http://` or `https://` JPEG **snapshot** URL from
Mainsail's webcam settings. A Mainsail stream URL is not a snapshot URL; use
the snapshot address, not the MJPEG stream address. Camera URLs are entered
manually, not discovered automatically. The Preview button displays refreshed
frames approximately every two seconds, rather than a full-frame-rate stream.
If an RTMP preview cannot connect, check that the camera's RTMP service is
running and reachable from this computer, and use the full stream URL,
including any required application and stream path.

Enable timelapses and choose either seconds or percentage, an interval, the
video playback FPS (default 30), and optionally an output folder. Percentage
values are percentages, so `0.5` means one frame every half percent. The first
frame is taken when an active print is observed; subsequent percentage crossings
are checked at the printer's polling interval. If progress jumps across several
thresholds, only one frame is taken because past images cannot be recovered.
Time-based intervals have a 0.1-second minimum and run independently of printer polling; a slow camera can
delay the next frame.

Capture pauses when a print is paused or the printer is offline. On completion,
cancellation or error, the app retains the numbered JPEG frames and generates
`timelapse.mp4` in a separate print folder under the OS Videos directory
(`Printer Notify Center`), or under the configured output folder. Keep the app
running until the print finishes and video rendering completes. If the app is
closed or the printer is disabled during a print, captured photos remain but
the unfinished video is not automatically resumed. Camera and rendering errors
are reported via notifications. Camera URLs, including any embedded credentials,
are stored in the app's local configuration file.

`imageio-ffmpeg` supplies an FFmpeg executable on supported platforms; standalone
app packaging must include its binary. Native binaries are platform-specific.
The bundled FFmpeg builds have their own licensing terms (the Windows build
enables GPL components); check those terms before distributing a packaged app.

## Running tests

The functionality tests use mocked Moonraker responses and notification services,
so they can run without a connected printer or desktop notification daemon:

```powershell
python -m unittest discover -s tests -v
```

## Running (development)

1. Create and activate a virtual environment (optional but recommended):

   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   ```

2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Launch the app:

   ```powershell
   python main.py
   ```

   On macOS/Linux, activate the venv with `source .venv/bin/activate` and run
   `python3 main.py` instead.

## Building a standalone app

This project uses [Flet](https://flet.dev), which bundles the `flet` CLI for packaging
the app into a native, standalone executable per platform (no Python install required
by end users).

1. Make sure the `flet` CLI is available (installed via `requirements.txt`):

   ```powershell
   pip install -r requirements.txt
   ```

2. Build for your current OS from the project root:

   ```powershell
   flet build windows   # on Windows -> produces an .exe under build/windows
   flet build macos      # on macOS   -> produces a .app under build/macos
   flet build linux      # on Linux   -> produces a binary under build/linux
   ```

   Flet only builds for the OS you run the command on (cross-compiling isn't
   supported), so build on Windows for a Windows binary, on macOS for a `.app`, etc.

3. Find the packaged app in the `build/<platform>` folder and distribute/run it
   directly — it bundles its own Python runtime.

## Notes on native notifications

- **Windows**: works out of the box.
- **macOS**: uses `osascript`, works out of the box.
- **Linux**: requires a notification daemon (most desktop environments ship one) and
  `notify-send` / D-Bus available. If unavailable, enable "In-app popup notifications"
  in Settings so you still get notified.

Popup sounds use `playsound3` and an available system audio backend. MP3 and WAV
are the most portable choices; OGG support depends on the selected backend and
installed codecs. On Linux, install GStreamer (`gst-play-1.0` and suitable audio
plugins), FFplay, or both `aplay` and `mpg123`. Backend and playback errors may
appear in the app's console output.
