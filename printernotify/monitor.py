"""Background polling of Moonraker endpoints and target evaluation."""
from __future__ import annotations

import threading
from typing import Callable

import requests

from .models import Printer
from .moonraker_client import MoonrakerClient
from .notifier import Notifier

# Objects always polled so the dashboard has something useful to show, in
# addition to whatever objects the user's targets reference.
BASE_OBJECTS = ["print_stats", "virtual_sdcard", "extruder", "heater_bed"]


def describe_error(exc: Exception) -> str:
    """Turns a raw requests exception into a short, human-readable reason."""
    if isinstance(exc, requests.exceptions.ConnectionError):
        if "actively refused" in str(exc) or "10061" in str(exc):
            return "Connection refused (Moonraker not running or wrong port?)"
        if "getaddrinfo failed" in str(exc) or "Name or service not known" in str(exc):
            return "Host not found (check the address)"
        return "Could not connect (check host/network)"
    if isinstance(exc, requests.exceptions.Timeout):
        return "Connection timed out"
    if isinstance(exc, requests.exceptions.HTTPError):
        return f"HTTP error ({exc.response.status_code if exc.response is not None else '?'})"
    return str(exc)


def _coerce(value_str: str, sample):
    if isinstance(sample, bool):
        return value_str.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(sample, (int, float)):
        try:
            return float(value_str)
        except ValueError:
            return value_str
    return value_str


def _compare(current, operator: str, target_value: str) -> bool:
    coerced = _coerce(target_value, current)
    try:
        if operator == ">=":
            return current >= coerced
        if operator == "<=":
            return current <= coerced
        if operator == ">":
            return current > coerced
        if operator == "<":
            return current < coerced
        if operator == "==":
            return str(current) == str(coerced)
        if operator == "!=":
            return str(current) != str(coerced)
    except TypeError:
        return False
    return False


class PrinterMonitor:
    """Polls a single printer's Moonraker API on its own thread."""

    def __init__(self, printer: Printer, notifier: Notifier, status_callback: Callable[[str, dict], None]):
        self.printer = printer
        self.notifier = notifier
        self.status_callback = status_callback
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._met_state: dict[str, bool] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            client = MoonrakerClient(
                self.printer.base_url, self.printer.api_key)
            objects = sorted(set(BASE_OBJECTS) | {
                             t.object_name for t in self.printer.targets if t.enabled and t.object_name})
            try:
                status = client.query_objects(objects)
                self.status_callback(
                    self.printer.id, {"online": True, "status": status})
                self._evaluate_targets(status)
            except Exception as exc:
                self.status_callback(
                    self.printer.id, {"online": False, "error": describe_error(exc)})
            self._stop_event.wait(max(self.printer.polling_interval, 1.0))

    def _evaluate_targets(self, status: dict) -> None:
        for target in self.printer.targets:
            if not target.enabled or not target.object_name or not target.field_name:
                continue
            obj = status.get(target.object_name)
            if not isinstance(obj, dict) or target.field_name not in obj:
                continue
            current = obj[target.field_name]
            is_met = _compare(current, target.operator, target.value)
            was_met = self._met_state.get(target.id, False)
            if is_met and not was_met:
                message = target.message or f"{target.object_name}.{target.field_name} {target.operator} {target.value}"
                self.notifier.notify(f"{self.printer.name}", message)
            self._met_state[target.id] = is_met


class MonitorManager:
    """Owns and syncs one PrinterMonitor per configured printer."""

    def __init__(self, notifier: Notifier, status_callback: Callable[[str, dict], None]):
        self.notifier = notifier
        self.status_callback = status_callback
        self._monitors: dict[str, PrinterMonitor] = {}

    def sync(self, printers: list[Printer]) -> None:
        current_ids = {p.id for p in printers}
        for pid in list(self._monitors):
            if pid not in current_ids:
                self._monitors.pop(pid).stop()

        for printer in printers:
            monitor = self._monitors.get(printer.id)
            if monitor is None:
                monitor = PrinterMonitor(
                    printer, self.notifier, self.status_callback)
                self._monitors[printer.id] = monitor
            else:
                monitor.printer = printer
            if printer.enabled:
                monitor.start()
            else:
                monitor.stop()

    def stop_all(self) -> None:
        for monitor in self._monitors.values():
            monitor.stop()
