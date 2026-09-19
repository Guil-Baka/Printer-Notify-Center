"""Data models for printers, targets and app settings."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

OPERATORS = [">=", "<=", ">", "<", "==", "!="]

# Quick-add presets: (label, object_name, field_name, operator, default_value, message)
TARGET_PRESETS = [
    ("Print complete", "print_stats", "state", "==", "complete", "Print finished!"),
    ("Print error", "print_stats", "state", "==",
     "error", "Print stopped with an error."),
    ("Print progress reached", "virtual_sdcard",
     "progress", ">=", "0.9", "Print is almost done."),
    ("Extruder temperature reached", "extruder", "temperature",
     ">=", "200", "Extruder reached target temperature."),
    ("Bed temperature reached", "heater_bed", "temperature",
     ">=", "60", "Bed reached target temperature."),
    ("Custom", "", "", ">=", "", ""),
]


def new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Target:
    object_name: str
    field_name: str
    operator: str
    value: str
    message: str = ""
    id: str = field(default_factory=new_id)
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Target":
        return cls(
            id=d.get("id", new_id()),
            object_name=d["object_name"],
            field_name=d["field_name"],
            operator=d["operator"],
            value=d["value"],
            message=d.get("message", ""),
            enabled=d.get("enabled", True),
        )


@dataclass
class Printer:
    name: str
    host: str
    port: int = 7125
    use_https: bool = False
    api_key: str = ""
    polling_interval: float = 3.0
    enabled: bool = True
    id: str = field(default_factory=new_id)
    targets: list[Target] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        scheme = "https" if self.use_https else "http"
        return f"{scheme}://{self.host}:{self.port}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "use_https": self.use_https,
            "api_key": self.api_key,
            "polling_interval": self.polling_interval,
            "enabled": self.enabled,
            "targets": [t.to_dict() for t in self.targets],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Printer":
        return cls(
            id=d.get("id", new_id()),
            name=d["name"],
            host=d["host"],
            port=d.get("port", 7125),
            use_https=d.get("use_https", False),
            api_key=d.get("api_key", ""),
            polling_interval=d.get("polling_interval", 3.0),
            enabled=d.get("enabled", True),
            targets=[Target.from_dict(t) for t in d.get("targets", [])],
        )


@dataclass
class Settings:
    use_native_notifications: bool = True
    use_in_app_notifications: bool = True
    notification_sound_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
