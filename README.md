# Printer Notify Center

A small cross-platform (Windows / macOS / Linux) desktop app that watches one or more
[Moonraker](https://moonraker.readthedocs.io/) API endpoints (Klipper printers) and notifies
you when configured targets are hit — e.g. print finished, a temperature reached, print
progress crossed a threshold, or the printer entered an error state.

## Motivation

I often print projects with many pieces that will be assembled later. I wanted
to know when the bed had cooled enough to remove a part without risking warping,
so I created an app with adjustable notification targets. I hope it helps others
too.

## Features

- Configure any number of Moonraker endpoints (host, port, optional API key, poll interval).
- See each printer's connection status, print state, progress, and temperatures
  when Moonraker provides them; enable or disable monitoring per printer.
- Define arbitrary "targets": watch any Moonraker object field with an operator
  (`>=`, `<=`, `>`, `<`, `==`, `!=`) and a threshold value. Notifies once when the
  condition transitions from not-met to met. Presets cover completion, errors,
  progress, extruder temperature, and bed temperature.
- OS native notifications (Windows toast, macOS notification center, Linux libnotify)
  via `plyer`, with automatic fallback to a standalone popup if native notifications
  aren't available on the current OS.
- Enable popup notifications independently, test notification channels, and choose
  a custom popup sound.
- Configuration and timelapses are saved next to the executable (or in the
  project folder when running from source).
- One manually configured RTSP camera or Mainsail-compatible JPEG snapshot URL per
  printer, with a periodically refreshed preview.
- Timelapses captured by elapsed time or print progress, with photos and an MP4
  saved locally when a print completes, is cancelled, or errors.

## Getting started

1. Start the app using the instructions in [Running from source](#running-from-source)
   or [Building a standalone app](#building-a-standalone-app).
2. Select **Add printer** and enter the Moonraker host, port, and optional API
   key. Enable HTTPS if the endpoint uses it.
3. On the printer card, select **Targets** and add a preset or a custom target.
   For example, use the bed temperature preset with `<=` and a threshold of `40`
   to be notified when the bed cools to 40 °C.
4. Optionally select **Camera and timelapse** to configure a camera, then open
   **Settings** to choose and test your notification channels.

The app must be running to monitor printers and send notifications.

## Cameras and timelapses

On a printer card, select the camera icon. Enter an `rtsp://` or `rtsps://`
stream URL, or the full `http://` or `https://` JPEG **snapshot** URL from
Mainsail's webcam settings. A Mainsail stream URL is not a snapshot URL; use
the snapshot address, not the MJPEG stream address. Camera URLs are entered
manually, not discovered automatically. The Preview button displays refreshed
frames approximately every two seconds, rather than a full-frame-rate stream.
If an RTSP preview cannot connect, check that the camera's RTSP service is
running and reachable from this computer, and use the full stream URL,
including any required application and stream path.
Existing RTMP URLs are no longer supported: replace each with the camera's
actual RTSP or HTTP(S) snapshot URL. Changing only the URL scheme does not
convert an RTMP stream into an RTSP stream.

Enable timelapses and choose a capture mode, an interval, and the video playback
FPS (default 30). For time-based capture, the interval is in seconds; for
progress-based capture, it is in percentage points, so `0.5` means one frame
every half percent. The first
frame is taken when an active print is observed; subsequent percentage crossings
are checked at the printer's polling interval. If progress jumps across several
thresholds, only one frame is taken because past images cannot be recovered.
Time-based intervals have a 0.1-second minimum and run independently of printer polling; a slow camera can
delay the next frame. The video FPS controls playback speed, not capture frequency.

Capture pauses when a print is paused or the printer is offline. On completion,
cancellation or error, the app retains the numbered JPEG frames and generates
an MP4 named with the date and time under `Videos/<printer name>` next to the
app. Numbered JPEG photos are kept in a matching dated subfolder. Keep the app
running until the print finishes and video rendering completes. If the app is
closed or the printer is disabled during a print, captured photos remain but
the unfinished video is not automatically resumed. Camera and rendering errors
are reported via notifications. Camera URLs, including any embedded credentials,
are stored in the app's local configuration file.

## Data and migration

The app stores configuration and recordings next to its executable, or in the
project directory when running from source:

```text
App folder/
  config.json
  Videos/
    Printer name/
      20261112-173716-<printer-id>-<id>.mp4
      20261112-173716-<printer-id>-<id>/
        frame-000001.jpg
        ...
```

Each recording gets a separate timestamped photo folder and MP4. Characters
that are unsafe in folder names are replaced. Per-printer custom output folders
are no longer used.

On first launch, settings are moved from the previous user configuration
directory, unless a configuration already exists beside the app. In that case
the existing local configuration is used and the old one is retained. The app
also moves identifiable timelapse folders from the old Videos location and
configured custom output folders. Other files in custom folders are left alone.
Make sure the app folder is writable. Renamed or manually moved recordings may
need to be moved manually. Configuration may contain printer and camera
credentials; keep it private and do not distribute it with the app.

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

## Running from source

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
   The project's `pyproject.toml` overrides the Flutter JNI dependency to a
   compatible 1.1 release and excludes development files from the package;
   keep it in the project root when building. Review the output before
   distributing it, especially if you keep other private files in the project.

3. Find the packaged app in `build/<platform>` and copy the entire folder to a
   separate writable location before running it. It bundles its own Python runtime.
   **Flet deletes and replaces the build output on subsequent builds.** Running
   the app from `build/<platform>` can therefore cause its configuration and
   videos to be deleted by a rebuild. Back up these files before rebuilding or
   replacing an existing installation.

## Dependency updates

Dependabot proposes dependency updates weekly. The Windows workflow runs the
tests and a standalone build on pushes, pull requests, and weekly, so changes in
transitive Flutter or Python dependencies can be detected. Review and test
update proposals before merging; this cannot prevent every future regression.
Flet and its CLI and desktop components are installed at the same pinned
version. To refresh compatible dependencies locally, run:

```powershell
python -m pip install --upgrade --upgrade-strategy eager -r requirements.txt
python -m pip check
python -m unittest discover -s tests -v
flet build windows
```

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
