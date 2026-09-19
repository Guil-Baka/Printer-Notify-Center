"""Loading and saving app configuration (printers + settings) as JSON."""
from __future__ import annotations

import json
from pathlib import Path

from platformdirs import user_config_dir

from .models import Printer, Settings

APP_NAME = "PrinterNotifyCenter"


def config_dir() -> Path:
    path = Path(user_config_dir(APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return config_dir() / "config.json"


class AppConfig:
    def __init__(self):
        self.printers: list[Printer] = []
        self.settings = Settings()

    def load(self) -> None:
        path = config_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.printers = [Printer.from_dict(p)
                         for p in data.get("printers", [])]
        self.settings = Settings.from_dict(data.get("settings", {}))

    def save(self) -> None:
        data = {
            "printers": [p.to_dict() for p in self.printers],
            "settings": self.settings.to_dict(),
        }
        config_path().write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_printer(self, printer_id: str) -> Printer | None:
        return next((p for p in self.printers if p.id == printer_id), None)
