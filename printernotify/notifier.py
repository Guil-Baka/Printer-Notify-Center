"""Sends notifications either via the OS (native) and/or as an in-app popup."""
from __future__ import annotations

from typing import Callable, Optional

try:
    from plyer import notification as _plyer_notification
except Exception:  # pragma: no cover - plyer missing or unsupported platform
    _plyer_notification = None


class Notifier:
    def __init__(self, settings, in_app_callback: Optional[Callable[[str, str], None]] = None):
        self.settings = settings
        self.in_app_callback = in_app_callback

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

        # Always fall back to the in-app popup if native notifications are
        # disabled, unsupported, or failed to fire.
        if self.in_app_callback and (self.settings.use_in_app_notifications or not native_ok):
            self.in_app_callback(title, message)
