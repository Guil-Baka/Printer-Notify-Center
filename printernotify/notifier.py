"""Sends notifications either via the OS (native) and/or a standalone popup window."""
from __future__ import annotations

import threading
from pathlib import Path

try:
    from plyer import notification as _plyer_notification
except Exception:  # pragma: no cover - plyer missing or unsupported platform
    _plyer_notification = None

# CC0 "Notification Pop" sound by Kenney, via soundcn.xyz.
POPUP_SOUND_PATH = Path(__file__).parent / "assets" / "notification_pop.mp3"


def _play_popup_sound(sound_path: Path) -> None:
    try:
        from playsound import playsound
        playsound(str(sound_path))
    except Exception:  # pragma: no cover - best-effort, missing backend/codec, etc.
        pass


def show_popup_notification(
    title: str, message: str, duration: float = 6.0, sound_path: str | Path = ""
) -> None:
    """Shows a small always-on-top popup window, independent of the main app window.

    Unlike an in-app banner, this uses its own top-level OS window (Tkinter), so
    it's visible even if the main app window is minimized or not focused.

    `sound_path`, if given and it exists, overrides the bundled default sound.
    """
    resolved_sound = Path(sound_path) if sound_path else POPUP_SOUND_PATH
    if resolved_sound.exists():
        threading.Thread(target=_play_popup_sound, args=(
            resolved_sound,), daemon=True).start()

    def _run() -> None:
        try:
            import tkinter as tk
        except Exception:
            return

        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.96)
        except Exception:
            pass

        width, height = 320, 110
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        root.geometry(
            f"{width}x{height}+{screen_w - width - 20}+{screen_h - height - 60}")

        bg = "#2b2b2b"
        root.configure(bg=bg)
        frame = tk.Frame(root, bg=bg, padx=14, pady=12,
                         highlightbackground="#555555", highlightthickness=1)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=title, font=("Segoe UI", 11, "bold"), fg="white",
                 bg=bg, anchor="w", justify="left", wraplength=width - 28).pack(fill="x")
        tk.Label(frame, text=message, font=("Segoe UI", 10), fg="#dddddd", bg=bg,
                 anchor="w", justify="left", wraplength=width - 28).pack(fill="x", pady=(6, 0))

        root.bind("<Button-1>", lambda e: root.destroy())
        root.after(int(duration * 1000), root.destroy)
        root.mainloop()

    threading.Thread(target=_run, daemon=True).start()


class Notifier:
    def __init__(self, settings):
        self.settings = settings

    def notify(self, title: str, message: str) -> None:
        native_ok = False
        if self.settings.use_native_notifications and _plyer_notification is not None:
            try:
                _plyer_notification.notify(
                    title=title,
                    message=message,
                    app_name="Printer Notify Center",
                    timeout=10,
                )
                native_ok = True
            except Exception:
                native_ok = False

        # Always fall back to the standalone popup if native notifications are
        # disabled, unsupported, or failed to fire.
        if self.settings.use_in_app_notifications or not native_ok:
            show_popup_notification(
                title, message, sound_path=self.settings.notification_sound_path)
