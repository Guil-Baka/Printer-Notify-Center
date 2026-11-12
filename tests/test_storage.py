import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from printernotify import config, storage
from printernotify.models import CameraSettings


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.legacy = self.root / "legacy"
        self.videos = self.root / "old-videos"
        self.custom = self.root / "custom"
        for path in (self.app, self.legacy, self.videos, self.custom):
            path.mkdir()
        self.patches = [
            patch("printernotify.storage.app_dir", return_value=self.app),
            patch("printernotify.storage.user_config_dir", return_value=str(self.legacy)),
            patch("printernotify.storage.user_videos_path", return_value=self.videos),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def write_legacy(self):
        data = {
            "printers": [{
                "id": "1234abcd", "name": "Printer / One",
                "host": "localhost", "camera": {"output_dir": str(self.custom)},
            }],
            "settings": {},
        }
        (self.legacy / "config.json").write_text(json.dumps(data), encoding="utf-8")
        return data

    def create_session(self, root, name):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "frame-000001.jpg").write_bytes(b"frame")
        (folder / "timelapse.mp4").write_bytes(b"video")
        return folder

    def test_migrates_config_and_generated_sessions_without_other_files(self):
        data = self.write_legacy()
        default = self.videos / "Printer Notify Center"
        names = ["20261112-120000-1234abcd-abcdef12", "20261112-130000-1234abcd-abcdef34"]
        first = self.create_session(default, names[0])
        second = self.create_session(self.custom, names[1])
        unrelated = self.custom / "other.mp4"
        unrelated.write_bytes(b"unrelated")
        storage.migrate_legacy_data()
        self.assertEqual(data, json.loads((self.app / "config.json").read_text(encoding="utf-8")))
        self.assertFalse((self.legacy / "config.json").exists())
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue(unrelated.exists())
        folder = self.app / "Videos" / "Printer _ One"
        for name in names:
            self.assertEqual(b"frame", (folder / name / "frame-000001.jpg").read_bytes())
            self.assertEqual(b"video", (folder / f"{name}.mp4").read_bytes())
        storage.migrate_legacy_data()
        self.assertEqual(2, len(list(folder.glob("*.mp4"))))

    def test_existing_config_and_collisions_are_preserved(self):
        self.write_legacy()
        current = {"printers": [], "settings": {"use_native_notifications": False}}
        (self.app / "config.json").write_text(json.dumps(current), encoding="utf-8")
        name = "20261112-120000-1234abcd-abcdef12"
        self.create_session(self.custom, name)
        destination = self.app / "Videos" / "Printer _ One" / name
        destination.mkdir(parents=True)
        (destination / "frame-000001.jpg").write_bytes(b"existing")
        storage.migrate_legacy_data()
        self.assertEqual(current, json.loads((self.app / "config.json").read_text(encoding="utf-8")))
        self.assertTrue((self.legacy / "config.json").exists())
        self.assertEqual(b"existing", (destination / "frame-000001.jpg").read_bytes())
        self.assertEqual(2, len([item for item in destination.parent.iterdir() if item.is_dir()]))

    def test_migration_errors_do_not_remove_configuration(self):
        self.write_legacy()
        self.create_session(self.custom, "20261112-120000-1234abcd-abcdef12")
        with patch("printernotify.storage.shutil.move", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                storage.migrate_legacy_data()
        self.assertTrue((self.legacy / "config.json").exists())
        self.assertFalse((self.app / "config.json").exists())

    def test_config_uses_app_folder_and_invalid_config_is_reported(self):
        with patch("printernotify.config.app_dir", return_value=self.app):
            instance = config.AppConfig()
            instance.save()
            self.assertTrue((self.app / "config.json").is_file())
            (self.app / "config.json").write_text("{", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                instance.load()

    def test_legacy_output_setting_is_ignored(self):
        camera = CameraSettings.from_dict({"output_dir": "outside", "fps": 24})
        self.assertEqual(24, camera.fps)
        self.assertNotIn("output_dir", camera.to_dict())

    def test_printer_folder_names_are_safe(self):
        for name, expected in (("../Foo", "_Foo"), ("CON", "_CON"),
                               ("", "_Printer"), ("A:B", "A_B")):
            with self.subTest(name=name):
                self.assertEqual(expected, storage.printer_folder_name(name))


if __name__ == "__main__":
    unittest.main()
