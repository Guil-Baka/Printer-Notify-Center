"""Portable storage paths and migration of earlier app data."""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

from platformdirs import user_config_dir, user_videos_path


def app_dir() -> Path:
    if not os.environ.get("FLET_PLATFORM"):
        return Path(__file__).resolve().parent.parent
    if sys.platform == "win32":
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, len(buffer))
        if not length or length >= len(buffer):
            raise OSError("Could not determine the application executable path.")
        return Path(buffer.value).resolve().parent
    if sys.platform.startswith("linux"):
        return Path(os.readlink("/proc/self/exe")).resolve().parent
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None)
        size = ctypes.c_uint32(0)
        libc._NSGetExecutablePath(None, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if libc._NSGetExecutablePath(buffer, ctypes.byref(size)) != 0:
            raise OSError("Could not determine the application executable path.")
        return Path(os.fsdecode(buffer.value)).resolve().parent
    raise OSError("Portable storage is not supported on this platform.")


def printer_folder_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name or name.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = f"_{name or 'Printer'}"
    return name[:100].rstrip(" .")


def videos_dir() -> Path:
    return app_dir() / "Videos"


def migrate_legacy_data() -> None:
    root = app_dir()
    legacy_config = Path(user_config_dir("PrinterNotifyCenter")) / "config.json"
    new_config = root / "config.json"
    paths = []
    for path in (legacy_config, new_config):
        if path.is_file() and path not in paths:
            paths.append(path)

    printers = {}
    sources = {user_videos_path() / "Printer Notify Center"}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for printer in data.get("printers", []):
            printer_id = str(printer.get("id", ""))
            if printer_id:
                printers[printer_id] = str(printer.get("name", "Printer"))
            output = printer.get("camera", {}).get("output_dir", "")
            if output:
                sources.add(Path(output).expanduser())

    pattern = re.compile(r"^(\d{8}-\d{6})-([0-9a-f]{8})-([0-9a-f]{8})$")
    target_root = videos_dir().resolve()
    for source in sources:
        if not source.is_dir() or source.resolve() == target_root:
            continue
        for session in source.iterdir():
            match = pattern.fullmatch(session.name)
            if not match or session.is_symlink() or not session.is_dir():
                continue
            printer_name = printers.get(match.group(2), "Unknown printer")
            destination = target_root / printer_folder_name(printer_name) / session.name
            if session.resolve() == destination.resolve():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination = destination.with_name(f"{destination.name}-{uuid.uuid4().hex}")
            shutil.move(str(session), str(destination))

    if target_root.is_dir():
        for printer in target_root.iterdir():
            if not printer.is_dir() or printer.is_symlink():
                continue
            for session in printer.iterdir():
                if not session.is_dir() or session.is_symlink():
                    continue
                if not re.fullmatch(
                    r"\d{8}-\d{6}-[0-9a-f]{8}-[0-9a-f]{8}(?:-[0-9a-f]{32})?",
                    session.name,
                ):
                    continue
                old_video = session / "timelapse.mp4"
                if old_video.is_file() and not old_video.is_symlink():
                    new_video = printer / f"{session.name}.mp4"
                    if new_video.exists():
                        new_video = printer / f"{session.name}-{uuid.uuid4().hex}.mp4"
                    shutil.move(str(old_video), str(new_video))

    if legacy_config.is_file() and not new_config.exists():
        root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_config), str(new_config))
