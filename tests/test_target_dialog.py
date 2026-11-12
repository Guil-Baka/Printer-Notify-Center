import asyncio
import threading
import unittest
from unittest.mock import patch

import msgpack
from flet.controls.base_control import BaseControl
from flet.messaging.connection import Connection
from flet.messaging.protocol import configure_encode_object_for_msgpack
from flet.messaging.session import Session
from flet.pubsub.pubsub_hub import PubSubHub

from printernotify.models import Printer, Settings, Target
from printernotify.ui import PrinterNotifyApp


class TestConnection(Connection):
    def __init__(self):
        super().__init__()
        self.pubsubhub = PubSubHub()
        self.loop = asyncio.get_running_loop()
        self.messages = []
        self.encoder = configure_encode_object_for_msgpack(BaseControl)

    def send_message(self, message):
        encoded = msgpack.packb([message.action, message.body], default=self.encoder)
        self.messages.append(msgpack.unpackb(encoded, strict_map_key=False))


class TestConfig:
    def __init__(self):
        self.settings = Settings()
        self.target = Target("print_stats", "state", "==", "complete", "Finished")
        self.printers = [Printer("Printer", "localhost", targets=[self.target])]
        self.saved = 0

    def load(self):
        pass

    def save(self):
        self.saved += 1

    def get_printer(self, printer_id):
        return next((p for p in self.printers if p.id == printer_id), None)


class TestMonitorManager:
    def __init__(self, notifier, callback):
        self.sync_count = 0

    def sync(self, printers):
        self.sync_count += 1


class TargetDialogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connection = TestConnection()
        self.session = Session(self.connection)
        self.page = self.session.page
        msgpack.packb(self.session.get_page_patch(), default=self.connection.encoder)
        self.config = TestConfig()
        with patch("printernotify.ui.AppConfig", return_value=self.config), patch(
            "printernotify.ui.MonitorManager", TestMonitorManager
        ):
            self.app = PrinterNotifyApp(self.page)
        self.printer = self.config.printers[0]
        self.page.route = f"/printer/{self.printer.id}"
        self.app.route_change(None)

    async def open_edit_dialog(self):
        row = self.page.views[-1].controls[0].controls[0]
        edit = row.content.trailing.controls[1]
        self.assertIn(edit._i, self.session.index)
        await self.session.dispatch_event(edit._i, "click", None)
        dialog = self.page._dialogs.controls[-1]
        self.assertTrue(dialog.open)
        return dialog

    async def test_cancel_closes_without_saving_or_replacing_the_view(self):
        dialog = await self.open_edit_dialog()
        current_view = self.page.views[-1]
        await self.session.dispatch_event(dialog.actions[0]._i, "click", None)
        self.assertFalse(dialog.open)
        self.assertEqual(0, self.config.saved)
        self.assertIs(current_view, self.page.views[-1])

    async def test_save_closes_and_refreshes_without_replacing_the_view(self):
        dialog = await self.open_edit_dialog()
        current_view = self.page.views[-1]
        message = dialog.content.controls[4]
        self.session.apply_patch(message._i, {"value": "Updated"})
        await self.session.dispatch_event(dialog.actions[1]._i, "click", None)
        self.assertFalse(dialog.open)
        self.assertEqual("Updated", self.config.target.message)
        self.assertEqual(1, self.config.saved)
        self.assertIs(current_view, self.page.views[-1])
        row = current_view.controls[0].controls[0]
        self.assertEqual("Updated", row.content.subtitle.value)
        self.assertTrue(any(
            message[1].get("id") == dialog._i
            and any(op[2:] == ["open", False] for op in message[1]["patch"][1:])
            for message in self.connection.messages
        ))
        await self.session.dispatch_event(dialog._i, "dismiss", None)
        reopened = await self.open_edit_dialog()
        await self.session.dispatch_event(reopened.actions[0]._i, "click", None)
        self.assertFalse(reopened.open)
        self.assertEqual(1, self.config.saved)

    async def test_target_add_updates_empty_view_without_replacing_it(self):
        self.printer.targets.clear()
        self.app.route_change(None)
        current_view = self.page.views[-1]
        add = current_view.floating_action_button
        await self.session.dispatch_event(add._i, "click", None)
        dialog = self.page._dialogs.controls[-1]
        controls = dialog.content.controls
        self.session.apply_patch(controls[1]._i, {"value": "print_stats"})
        self.session.apply_patch(controls[2]._i, {"value": "state"})
        self.session.apply_patch(controls[3].controls[1]._i, {"value": "complete"})
        await self.session.dispatch_event(dialog.actions[1]._i, "click", None)
        self.assertFalse(dialog.open)
        self.assertEqual(1, len(self.printer.targets))
        self.assertIs(current_view, self.page.views[-1])
        self.assertEqual(1, len(current_view.controls[0].controls))

    async def test_printer_edit_save_preserves_dashboard_view(self):
        self.page.route = "/"
        self.app.route_change(None)
        current_view = self.page.views[-1]
        card = current_view.controls[0].controls[0]
        edit = card.content.content.controls[0].controls[1].controls[2]
        await self.session.dispatch_event(edit._i, "click", None)
        dialog = self.page._dialogs.controls[-1]
        self.assertTrue(dialog.open)
        name = dialog.content.controls[0]
        self.session.apply_patch(name._i, {"value": "Updated printer"})
        await self.session.dispatch_event(dialog.actions[1]._i, "click", None)
        self.assertFalse(dialog.open)
        self.assertEqual("Updated printer", self.printer.name)
        self.assertEqual(1, self.config.saved)
        self.assertIs(current_view, self.page.views[-1])
        card = current_view.controls[0].controls[0]
        self.assertEqual(
            "Updated printer", card.content.content.controls[0].controls[0].controls[1].value
        )

    async def test_printer_delete_confirm_preserves_dashboard_view(self):
        self.page.route = "/"
        self.app.route_change(None)
        current_view = self.page.views[-1]
        card = current_view.controls[0].controls[0]
        delete = card.content.content.controls[0].controls[1].controls[3]
        await self.session.dispatch_event(delete._i, "click", None)
        dialog = self.page._dialogs.controls[-1]
        await self.session.dispatch_event(dialog.actions[1]._i, "click", None)
        self.assertFalse(dialog.open)
        self.assertEqual([], self.config.printers)
        self.assertEqual(1, self.config.saved)
        self.assertIs(current_view, self.page.views[-1])

    async def test_camera_settings_validate_and_save(self):
        self.page.route = "/"
        self.app.route_change(None)
        card = self.page.views[-1].controls[0].controls[0]
        button = card.content.content.controls[0].controls[1].controls[4]
        await self.session.dispatch_event(button._i, "click", None)
        dialog = self.page._dialogs.controls[-1]
        url, enabled, mode, interval, fps, _, error = dialog.content.controls
        self.session.apply_patch(enabled._i, {"value": True})
        await self.session.dispatch_event(dialog.actions[2]._i, "click", None)
        self.assertTrue(dialog.open)
        self.assertIn("URL", error.value)
        self.assertEqual(0, self.config.saved)
        self.session.apply_patch(url._i, {"value": "rtmp://camera/live"})
        await self.session.dispatch_event(dialog.actions[2]._i, "click", None)
        self.assertTrue(dialog.open)
        self.assertIn("RTMP is no longer supported", error.value)
        self.assertEqual(0, self.config.saved)
        self.session.apply_patch(url._i, {"value": "rtsp://camera/live"})
        self.session.apply_patch(mode._i, {"value": "percentage"})
        self.session.apply_patch(interval._i, {"value": "0.5"})
        self.session.apply_patch(fps._i, {"value": "24"})
        await self.session.dispatch_event(dialog.actions[2]._i, "click", None)
        self.assertFalse(dialog.open)
        self.assertEqual(1, self.config.saved)
        self.assertEqual(
            ("rtsp://camera/live", True, "percentage", 0.5, 24),
            (self.printer.camera.url, self.printer.camera.timelapse_enabled,
             self.printer.camera.capture_mode, self.printer.camera.interval,
             self.printer.camera.fps),
        )

    async def test_camera_preview_waits_for_a_valid_frame(self):
        started = threading.Event()
        release = threading.Event()
        frame = b"\xff\xd8test\xff\xd9"

        def capture(url):
            started.set()
            release.wait(5)
            return frame

        with patch("printernotify.ui.capture_frame", side_effect=capture):
            self.app.open_camera_preview("rtsp://host/live", "Printer")
            dialog = self.page._dialogs.controls[-1]
            preview, status = dialog.content.controls
            try:
                self.assertTrue(started.wait(2))
                self.assertIsNone(preview.content)
                self.assertEqual("Connecting to camera...", status.value)
                release.set()
                for _ in range(100):
                    if preview.content is not None:
                        break
                    await asyncio.sleep(0.01)
                self.assertIsNotNone(preview.content)
                self.assertEqual(frame, preview.content.src)
                self.assertIn("Live preview", status.value)
            finally:
                release.set()
                dialog.actions[0].on_click(None)

    async def test_camera_preview_reports_failure_without_empty_image(self):
        with patch("printernotify.ui.capture_frame", side_effect=RuntimeError(
            "Could not connect to the RTSP camera."
        )):
            self.app.open_camera_preview("rtsp://host/live", "Printer")
            dialog = self.page._dialogs.controls[-1]
            preview, status = dialog.content.controls
            try:
                for _ in range(100):
                    if "failed" in status.value:
                        break
                    await asyncio.sleep(0.01)
                self.assertIsNone(preview.content)
                self.assertIn("Could not connect", status.value)
            finally:
                dialog.actions[0].on_click(None)


if __name__ == "__main__":
    unittest.main()
