import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from printernotify import config, models, monitor, moonraker_client, notifier, ui


class FakePage:
    def __init__(self):
        self.route = "/"
        self.views = []
        self.services = []
        self.window = SimpleNamespace()
        self.dialog = None
        self.updates = 0

    def update(self):
        self.updates += 1

    def show_dialog(self, dialog):
        self.dialog = dialog

    def pop_dialog(self):
        self.dialog = None

    def navigate(self, route):
        self.route = route


class ConfigStub:
    def __init__(self, printers=None):
        self.printers = list(printers or [])
        self.settings = models.Settings()
        self.saved = 0

    def load(self):
        pass

    def save(self):
        self.saved += 1

    def get_printer(self, printer_id):
        return next((p for p in self.printers if p.id == printer_id), None)


class ManagerStub:
    def __init__(self, notifier_obj, status_callback):
        self.sync_calls = []

    def sync(self, printers):
        self.sync_calls.append(list(printers))


class FakeEvent:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, _seconds):
        self.stopped = True


class FunctionalityTests(unittest.TestCase):
    def make_app(self, printers=None):
        page = FakePage()
        cfg = ConfigStub(printers)
        with patch.object(ui, "AppConfig", return_value=cfg), patch.object(
            ui, "MonitorManager", ManagerStub
        ):
            app = ui.PrinterNotifyApp(page)
        return app, page, cfg

    def test_model_roundtrip_urls_and_presets(self):
        target = models.Target("print_stats", "state", "==", "complete", "Done")
        printer = models.Printer("P", "printer.local", targets=[target])
        self.assertEqual("http://printer.local:7125", printer.base_url)
        printer.use_https = True
        self.assertEqual("https://printer.local:7125", printer.base_url)
        self.assertEqual(printer.to_dict(), models.Printer.from_dict(printer.to_dict()).to_dict())
        self.assertEqual([">=", "<=", ">", "<", "==", "!="], models.OPERATORS)
        self.assertEqual(6, len(models.TARGET_PRESETS))

    def test_config_persist_load_and_lookup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            printer = models.Printer(
                "P", "host", targets=[models.Target("o", "f", "==", "x")]
            )
            original = config.AppConfig()
            original.printers = [printer]
            original.settings = models.Settings(False, True, "tone.wav")
            with patch.object(config, "config_path", return_value=path):
                original.save()
                loaded = config.AppConfig()
                loaded.load()
            self.assertEqual(printer.to_dict(), loaded.get_printer(printer.id).to_dict())
            self.assertEqual(
                {"use_native_notifications": False, "use_in_app_notifications": True,
                 "notification_sound_path": "tone.wav"},
                loaded.settings.to_dict(),
            )
            self.assertEqual(printer.id, json.loads(path.read_text(encoding="utf-8"))[
                "printers"
            ][0]["id"])

    def test_moonraker_client_query_url_auth_and_empty_list(self):
        response = Mock()
        response.json.return_value = {
            "result": {"status": {"extruder": {"temperature": 201}}}
        }
        with patch.object(moonraker_client.requests, "get", return_value=response) as get:
            client = moonraker_client.MoonrakerClient("http://host/", "secret", timeout=2)
            self.assertEqual(
                {"extruder": {"temperature": 201}},
                client.query_objects(["extruder", "heater_bed"]),
            )
            args, kwargs = get.call_args
            self.assertEqual(
                "http://host/printer/objects/query?extruder&heater_bed", args[0]
            )
            self.assertEqual({"X-Api-Key": "secret"}, kwargs["headers"])
            self.assertEqual(2, kwargs["timeout"])
            self.assertEqual({}, client.query_objects([]))
            self.assertEqual(1, get.call_count)
        response.raise_for_status.assert_called_once()

    def test_target_operators_numeric_equality_and_transition_edges(self):
        cases = {
            ">=": (5, True, 4),
            "<=": (5, True, 6),
            ">": (6, True, 5),
            "<": (4, True, 5),
            "==": (5, True, 6),
            "!=": (6, True, 5),
        }
        for operator, (initial, matches, nonmatching) in cases.items():
            with self.subTest(operator=operator):
                target = models.Target("o", "v", operator, "5", "hit")
                notify = Mock()
                evaluator = monitor.PrinterMonitor(
                    models.Printer("P", "host", targets=[target]), notify, Mock()
                )
                evaluator._evaluate_targets({"o": {"v": initial}})
                self.assertEqual(matches, notify.notify.called)
                notify.notify.assert_called_once_with("P", "hit")
                evaluator._evaluate_targets({"o": {"v": initial}})
                notify.notify.assert_called_once()
                evaluator._evaluate_targets({"o": {"v": nonmatching}})
                evaluator._evaluate_targets({"o": {"v": initial}})
                self.assertEqual(2, notify.notify.call_count)

        disabled = models.Target("o", "v", "==", "5", enabled=False)
        evaluator = monitor.PrinterMonitor(
            models.Printer("P", "host", targets=[disabled]), Mock(), Mock()
        )
        evaluator._evaluate_targets({"o": {"v": 5}})
        self.assertEqual({}, evaluator._met_state)

    def test_monitor_poll_cycle_queries_and_reports_status_or_error(self):
        printer = models.Printer(
            "P", "host", targets=[models.Target("custom", "value", ">", "2")]
        )
        client = Mock()
        client.query_objects.return_value = {"custom": {"value": 3}}
        callback, notify = Mock(), Mock()
        poller = monitor.PrinterMonitor(printer, notify, callback)
        poller._stop_event = FakeEvent()
        with patch.object(monitor, "MoonrakerClient", return_value=client):
            poller._run()
        self.assertEqual(
            sorted(set(monitor.BASE_OBJECTS + ["custom"])),
            client.query_objects.call_args.args[0],
        )
        callback.assert_called_once_with(
            printer.id, {"online": True, "status": {"custom": {"value": 3}}}
        )
        notify.notify.assert_called_once()

        callback.reset_mock()
        client.query_objects.side_effect = TimeoutError("slow")
        poller._stop_event = FakeEvent()
        with patch.object(monitor, "MoonrakerClient", return_value=client):
            poller._run()
        self.assertFalse(callback.call_args.args[1]["online"])
        self.assertIn("slow", callback.call_args.args[1]["error"])

    def test_monitor_manager_syncs_enable_disable_and_removal(self):
        created = []

        class MonitorStub:
            def __init__(self, printer, notifier_obj, callback):
                self.printer = printer
                self.started = self.stopped = 0
                created.append(self)

            def start(self):
                self.started += 1

            def stop(self):
                self.stopped += 1

        manager = monitor.MonitorManager(Mock(), Mock())
        with patch.object(monitor, "PrinterMonitor", MonitorStub):
            enabled = models.Printer("A", "a")
            disabled = models.Printer("B", "b", enabled=False)
            manager.sync([enabled, disabled])
            self.assertEqual([1, 0], [created[0].started, created[1].started])
            self.assertEqual(1, created[1].stopped)
            replacement = models.Printer("A2", "a2", id=enabled.id)
            manager.sync([replacement])
            self.assertIs(replacement, created[0].printer)
            self.assertEqual(2, created[1].stopped)
            manager.stop_all()
            self.assertEqual(1, created[0].stopped)

    def test_notifier_native_popup_and_fallback_paths(self):
        with patch.object(notifier, "_plyer_notification") as native, patch.object(
            notifier, "show_popup_notification"
        ) as popup:
            notifier.Notifier(models.Settings(True, False)).notify("T", "M")
            native.notify.assert_called_once()
            popup.assert_not_called()
            native.notify.side_effect = RuntimeError("unavailable")
            notifier.Notifier(models.Settings(True, False)).notify("T", "M")
            popup.assert_called_once()

            native.notify.side_effect = None
            native.notify.reset_mock()
            popup.reset_mock()
            notifier.Notifier(models.Settings(False, False)).notify("T", "M")
            native.notify.assert_not_called()
            popup.assert_called_once()
            popup.reset_mock()
            notifier.Notifier(models.Settings(True, True)).notify("T", "M")
            native.notify.assert_called_once()
            popup.assert_called_once()

    def test_popup_sound_uses_default_and_custom_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            custom = Path(temp_dir) / "custom.wav"
            custom.touch()
            with patch.object(notifier.threading, "Thread") as thread:
                notifier.show_popup_notification("T", "M")
                self.assertEqual(notifier._play_popup_sound, thread.call_args_list[0].kwargs["target"])
                self.assertEqual((notifier.POPUP_SOUND_PATH,), thread.call_args_list[0].kwargs["args"])
                thread.reset_mock()
                notifier.show_popup_notification("T", "M", sound_path=custom)
                self.assertEqual((custom,), thread.call_args_list[0].kwargs["args"])

    def test_popup_sound_calls_player_and_reports_errors(self):
        from playsound3 import PlaysoundException

        sound_path = Path("example.mp3")
        with patch("playsound3.playsound") as play:
            notifier._play_popup_sound(sound_path)
            play.assert_called_once_with(str(sound_path))
            play.side_effect = PlaysoundException("backend unavailable")
            with self.assertLogs(notifier.logger, level="WARNING") as logs:
                notifier._play_popup_sound(sound_path)
            self.assertIn("backend unavailable", logs.output[0])

    def test_status_formatting_waiting_offline_and_online(self):
        self.assertEqual("Waiting for data...", ui.format_status(None)[0])
        self.assertEqual(
            "Offline: Timeout",
            ui.format_status({"online": False, "error": "Timeout"})[0],
        )
        summary, color = ui.format_status({
            "online": True,
            "status": {
                "print_stats": {"state": "printing"},
                "virtual_sdcard": {"progress": 0.5},
                "extruder": {"temperature": 199.6, "target": 205},
                "heater_bed": {"temperature": 59.7},
            },
        })
        self.assertEqual(
            "State: printing  \u2022  Progress: 50%  \u2022  Extruder: 200/205\u00b0C"
            "  \u2022  Bed: 60/0\u00b0C",
            summary,
        )
        self.assertEqual(ui.ft.Colors.GREEN, color)
        self.assertEqual(
            "Online, no data yet", ui.format_status({"online": True, "status": {}})[0]
        )

    def test_ui_routes_dashboard_and_target_view_and_status_update(self):
        printer = models.Printer(
            "P", "host", targets=[models.Target("o", "f", "==", "v")]
        )
        app, page, _ = self.make_app([printer])
        self.assertEqual(["/"], [view.route for view in page.views])
        page.route = f"/printer/{printer.id}"
        app.route_change(None)
        self.assertEqual(["/", f"/printer/{printer.id}"], [v.route for v in page.views])
        app.on_status_update(
            printer.id,
            {"online": True, "status": {"print_stats": {"state": "complete"}}},
        )
        self.assertIn("State: complete", app.printer_status_texts[printer.id].value)

    def test_ui_printer_target_and_settings_crud(self):
        app, page, cfg = self.make_app()
        app.open_printer_dialog()
        dialog = page.dialog
        name, host, port_and_interval, _, _, _ = dialog.content.controls
        name.value, host.value = "Printer", "printer.local"
        port_and_interval.controls[0].value = "7126"
        port_and_interval.controls[1].value = "2.5"
        dialog.actions[1].on_click(None)
        printer = cfg.printers[0]
        self.assertEqual(
            ("Printer", "printer.local", 7126, 2.5),
            (printer.name, printer.host, printer.port, printer.polling_interval),
        )

        app.open_target_dialog(printer)
        dialog = page.dialog
        _, obj, field, row, _, _ = dialog.content.controls
        obj.value, field.value, row.controls[1].value = "print_stats", "state", "complete"
        dialog.actions[1].on_click(None)
        target = printer.targets[0]
        self.assertEqual("print_stats", target.object_name)
        app.toggle_target(printer, target, False)
        self.assertFalse(target.enabled)
        app.delete_target(printer, target)
        self.assertEqual([], printer.targets)
        app.toggle_printer(printer, False)
        self.assertFalse(printer.enabled)

        app.open_settings_dialog()
        dialog = page.dialog
        rows = dialog.content.controls
        rows[0].controls[0].value = False
        rows[1].controls[0].value = True
        app.sound_file_picker.on_result(SimpleNamespace(
            files=[SimpleNamespace(path="tone.wav", name="tone.wav")]
        ))
        dialog.actions[0].on_click(None)
        self.assertEqual((False, True, "tone.wav"), (
            cfg.settings.use_native_notifications,
            cfg.settings.use_in_app_notifications,
            cfg.settings.notification_sound_path,
        ))
        self.assertGreaterEqual(cfg.saved, 5)

    def test_ui_edit_and_delete_printer(self):
        printer = models.Printer("Before", "old", id="fixed")
        app, page, cfg = self.make_app([printer])
        app.open_printer_dialog(printer)
        dialog = page.dialog
        name, host, row, https, api_key, _ = dialog.content.controls
        name.value, host.value = "After", "new"
        row.controls[0].value, row.controls[1].value = "7127", "4"
        https.value, api_key.value = True, "token"
        dialog.actions[1].on_click(None)
        self.assertEqual(
            ("After", "new", 7127, True, "token"),
            (printer.name, printer.host, printer.port, printer.use_https, printer.api_key),
        )
        app.delete_printer(printer)
        page.dialog.actions[1].on_click(None)
        self.assertEqual([], cfg.printers)


if __name__ == "__main__":
    unittest.main()
