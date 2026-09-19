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

## Setup

```powershell
pip install -r requirements.txt
python main.py
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
