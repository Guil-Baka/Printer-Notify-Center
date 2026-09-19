"""Flet UI for Printer Notify Center."""
from __future__ import annotations

import threading

import flet as ft

from .config import AppConfig
from .models import OPERATORS, TARGET_PRESETS, Printer, Target
from .monitor import MonitorManager
from .notifier import Notifier


def format_status(info: dict | None) -> tuple[str, str]:
    """Returns (summary_text, color) for a printer's latest status info."""
    if info is None:
        return "Waiting for data...", ft.Colors.OUTLINE
    if not info.get("online"):
        return f"Offline: {info.get('error', 'unreachable')}", ft.Colors.ERROR

    status = info.get("status", {})
    parts = []
    ps = status.get("print_stats", {})
    if "state" in ps:
        parts.append(f"State: {ps['state']}")
    vsd = status.get("virtual_sdcard", {})
    if "progress" in vsd:
        parts.append(f"Progress: {vsd['progress'] * 100:.0f}%")
    extruder = status.get("extruder", {})
    if "temperature" in extruder:
        parts.append(
            f"Extruder: {extruder['temperature']:.0f}/{extruder.get('target', 0):.0f}°C")
    bed = status.get("heater_bed", {})
    if "temperature" in bed:
        parts.append(
            f"Bed: {bed['temperature']:.0f}/{bed.get('target', 0):.0f}°C")
    return ("  •  ".join(parts) if parts else "Online, no data yet"), ft.Colors.GREEN


class PrinterNotifyApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.config = AppConfig()
        self.config.load()
        # Reentrant because dialog handlers call refresh_current_view(), which
        # re-enters route_change() while the lock is already held.
        self.update_lock = threading.RLock()

        self.notifier = Notifier(self.config.settings,
                                 in_app_callback=self.show_in_app_notification)
        self.monitor_manager = MonitorManager(
            self.notifier, self.on_status_update)
        self.status_cache: dict[str, dict] = {}
        self.printer_status_texts: dict[str, ft.Text] = {}
        self.printer_status_dots: dict[str, ft.Icon] = {}

        self._setup_page()
        self.page.on_route_change = self.route_change
        self.page.on_view_pop = self.view_pop
        self.page.on_error = lambda e: print(f"Page error: {e.data}")
        self.page.go(self.page.route or "/")
        self.monitor_manager.sync(self.config.printers)

    # ------------------------------------------------------------------ setup
    def _setup_page(self) -> None:
        self.page.title = "Printer Notify Center"
        self.page.theme_mode = ft.ThemeMode.SYSTEM
        self.page.theme = ft.Theme(
            color_scheme_seed=ft.Colors.DEEP_PURPLE, use_material3=True)
        self.page.dark_theme = ft.Theme(
            color_scheme_seed=ft.Colors.DEEP_PURPLE, use_material3=True)
        self.page.window.width = 900
        self.page.window.height = 720
        self.page.window.min_width = 480
        self.page.window.min_height = 480

    # --------------------------------------------------------------- routing
    def route_change(self, e: ft.RouteChangeEvent) -> None:
        with self.update_lock:
            route = self.page.route
            self.page.views.clear()
            self.page.views.append(self.build_dashboard_view())
            if route.startswith("/printer/"):
                printer_id = route.split("/printer/", 1)[1]
                printer = self.config.get_printer(printer_id)
                if printer is not None:
                    self.page.views.append(self.build_targets_view(printer))
            self.page.update()

    def view_pop(self, e: ft.ViewPopEvent) -> None:
        with self.update_lock:
            self.page.views.pop()
            top_view = self.page.views[-1]
            self.page.go(top_view.route)

    # ------------------------------------------------------------ dashboard
    def build_dashboard_view(self) -> ft.View:
        self.printer_status_texts.clear()
        self.printer_status_dots.clear()

        cards = [self._build_printer_card(p) for p in self.config.printers]
        if not cards:
            body = ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.PRECISION_MANUFACTURING,
                                size=64, color=ft.Colors.OUTLINE),
                        ft.Text("No printers configured yet.",
                                size=16, color=ft.Colors.OUTLINE),
                        ft.Text("Tap + to add your first Moonraker endpoint.",
                                color=ft.Colors.OUTLINE),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                    tight=True,
                ),
                alignment=ft.alignment.center,
                expand=True,
            )
        else:
            body = ft.ListView(controls=cards, spacing=12,
                               padding=16, expand=True)

        return ft.View(
            "/",
            [
                ft.AppBar(
                    title=ft.Text("Printer Notify Center"),
                    center_title=False,
                    actions=[
                        ft.IconButton(ft.Icons.SETTINGS, tooltip="Settings",
                                      on_click=lambda e: self.open_settings_dialog()),
                    ],
                ),
                body,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            floating_action_button=ft.FloatingActionButton(
                icon=ft.Icons.ADD, tooltip="Add printer", on_click=lambda e: self.open_printer_dialog()
            ),
        )

    def _build_printer_card(self, printer: Printer) -> ft.Control:
        status_text = ft.Text("Waiting for data...",
                              size=12, color=ft.Colors.OUTLINE)
        status_dot = ft.Icon(ft.Icons.CIRCLE, size=10, color=ft.Colors.OUTLINE)
        self.printer_status_texts[printer.id] = status_text
        self.printer_status_dots[printer.id] = status_dot

        cached = self.status_cache.get(printer.id)
        if cached is not None:
            text, color = format_status(cached)
            status_text.value = text
            status_dot.color = color

        return ft.Card(
            content=ft.Container(
                padding=16,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Row([status_dot, ft.Text(
                                    printer.name, size=18, weight=ft.FontWeight.BOLD)], spacing=8),
                                ft.Row(
                                    [
                                        ft.Switch(
                                            value=printer.enabled,
                                            tooltip="Enabled",
                                            on_change=lambda e, p=printer: self.toggle_printer(
                                                p, e.control.value),
                                        ),
                                        ft.IconButton(
                                            ft.Icons.TUNE,
                                            tooltip="Targets",
                                            on_click=lambda e, p=printer: self.page.go(
                                                f"/printer/{p.id}"),
                                        ),
                                        ft.IconButton(
                                            ft.Icons.EDIT,
                                            tooltip="Edit printer",
                                            on_click=lambda e, p=printer: self.open_printer_dialog(
                                                p),
                                        ),
                                        ft.IconButton(
                                            ft.Icons.DELETE_OUTLINE,
                                            tooltip="Delete printer",
                                            on_click=lambda e, p=printer: self.delete_printer(
                                                p),
                                        ),
                                    ],
                                    spacing=0,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Text(f"{printer.base_url}  •  {len(printer.targets)} target(s)",
                                size=12, color=ft.Colors.OUTLINE),
                        status_text,
                    ],
                    spacing=6,
                ),
            )
        )

    # --------------------------------------------------------------- targets
    def build_targets_view(self, printer: Printer) -> ft.View:
        rows = [self._build_target_row(printer, t) for t in printer.targets]
        if not rows:
            body = ft.Container(
                content=ft.Text("No targets yet. Tap + to add one.",
                                color=ft.Colors.OUTLINE),
                alignment=ft.alignment.center,
                expand=True,
            )
        else:
            body = ft.ListView(controls=rows, spacing=8,
                               padding=16, expand=True)

        return ft.View(
            f"/printer/{printer.id}",
            [
                ft.AppBar(title=ft.Text(f"Targets — {printer.name}")),
                body,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            floating_action_button=ft.FloatingActionButton(
                icon=ft.Icons.ADD, tooltip="Add target", on_click=lambda e, p=printer: self.open_target_dialog(p)
            ),
        )

    def _build_target_row(self, printer: Printer, target: Target) -> ft.Control:
        label = f"{target.object_name}.{target.field_name} {target.operator} {target.value}"
        return ft.Card(
            content=ft.ListTile(
                leading=ft.Icon(
                    ft.Icons.NOTIFICATIONS_ACTIVE if target.enabled else ft.Icons.NOTIFICATIONS_OFF),
                title=ft.Text(label),
                subtitle=ft.Text(
                    target.message or "(no custom message)", size=12, color=ft.Colors.OUTLINE),
                trailing=ft.Row(
                    [
                        ft.Switch(
                            value=target.enabled,
                            on_change=lambda e, p=printer, t=target: self.toggle_target(
                                p, t, e.control.value),
                        ),
                        ft.IconButton(
                            ft.Icons.EDIT, tooltip="Edit", on_click=lambda e, p=printer, t=target: self.open_target_dialog(p, t)
                        ),
                        ft.IconButton(
                            ft.Icons.DELETE_OUTLINE,
                            tooltip="Delete",
                            on_click=lambda e, p=printer, t=target: self.delete_target(
                                p, t),
                        ),
                    ],
                    tight=True,
                ),
            )
        )

    # ---------------------------------------------------------- printer CRUD
    def open_printer_dialog(self, printer: Printer | None = None) -> None:
        is_edit = printer is not None
        name_field = ft.TextField(
            label="Name", value=printer.name if printer else "", autofocus=True)
        host_field = ft.TextField(
            label="Host / IP", value=printer.host if printer else "")
        port_field = ft.TextField(label="Port", value=str(
            printer.port if printer else 7125), width=120)
        https_switch = ft.Switch(
            label="Use HTTPS", value=printer.use_https if printer else False)
        api_key_field = ft.TextField(
            label="API key (optional)", value=printer.api_key if printer else "", password=True, can_reveal_password=True)
        interval_field = ft.TextField(label="Poll interval (sec)", value=str(
            printer.polling_interval if printer else 3.0), width=160)
        error_text = ft.Text("", color=ft.Colors.ERROR)

        def save(e: ft.ControlEvent) -> None:
            if not name_field.value or not host_field.value:
                error_text.value = "Name and host are required."
                self.page.update()
                return
            try:
                port = int(port_field.value)
                interval = float(interval_field.value)
            except ValueError:
                error_text.value = "Port must be an integer and interval a number."
                self.page.update()
                return

            if is_edit:
                printer.name = name_field.value
                printer.host = host_field.value
                printer.port = port
                printer.use_https = https_switch.value
                printer.api_key = api_key_field.value
                printer.polling_interval = interval
            else:
                self.config.printers.append(
                    Printer(
                        name=name_field.value,
                        host=host_field.value,
                        port=port,
                        use_https=https_switch.value,
                        api_key=api_key_field.value,
                        polling_interval=interval,
                    )
                )
            self.config.save()
            self.monitor_manager.sync(self.config.printers)
            with self.update_lock:
                self.page.close(dialog)
                self.refresh_current_view()

        def cancel(e: ft.ControlEvent) -> None:
            with self.update_lock:
                self.page.close(dialog)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit printer" if is_edit else "Add printer"),
            content=ft.Column(
                [
                    name_field,
                    host_field,
                    ft.Row([port_field, interval_field]),
                    https_switch,
                    api_key_field,
                    error_text,
                ],
                tight=True,
                spacing=10,
                width=380,
            ),
            actions=[ft.TextButton("Cancel", on_click=cancel),
                     ft.FilledButton("Save", on_click=save)],
        )
        with self.update_lock:
            self.page.open(dialog)

    def toggle_printer(self, printer: Printer, value: bool) -> None:
        printer.enabled = value
        self.config.save()
        self.monitor_manager.sync(self.config.printers)

    def delete_printer(self, printer: Printer) -> None:
        def confirm(e: ft.ControlEvent) -> None:
            self.config.printers.remove(printer)
            self.config.save()
            self.monitor_manager.sync(self.config.printers)
            with self.update_lock:
                self.page.close(dialog)
                self.refresh_current_view()

        def cancel(e: ft.ControlEvent) -> None:
            with self.update_lock:
                self.page.close(dialog)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Delete printer?"),
            content=ft.Text(
                f"This will remove '{printer.name}' and all its targets."),
            actions=[ft.TextButton("Cancel", on_click=cancel), ft.FilledButton(
                "Delete", on_click=confirm)],
        )
        with self.update_lock:
            self.page.open(dialog)

    # ----------------------------------------------------------- target CRUD
    def open_target_dialog(self, printer: Printer, target: Target | None = None) -> None:
        is_edit = target is not None

        preset_dropdown = ft.Dropdown(
            label="Preset",
            options=[ft.dropdown.Option(p[0]) for p in TARGET_PRESETS],
            value=TARGET_PRESETS[-1][0],
        )
        object_field = ft.TextField(
            label="Object name", value=target.object_name if target else "", hint_text="e.g. print_stats")
        field_field = ft.TextField(
            label="Field name", value=target.field_name if target else "", hint_text="e.g. state")
        operator_dropdown = ft.Dropdown(
            label="Operator",
            options=[ft.dropdown.Option(op) for op in OPERATORS],
            value=target.operator if target else OPERATORS[0],
            width=120,
        )
        value_field = ft.TextField(
            label="Value", value=target.value if target else "")
        message_field = ft.TextField(
            label="Notification message (optional)", value=target.message if target else "")
        error_text = ft.Text("", color=ft.Colors.ERROR)

        def apply_preset(e: ft.ControlEvent) -> None:
            preset = next(
                (p for p in TARGET_PRESETS if p[0] == preset_dropdown.value), None)
            if preset and preset[0] != "Custom":
                _, obj, fld, op, val, msg = preset
                object_field.value = obj
                field_field.value = fld
                operator_dropdown.value = op
                value_field.value = val
                message_field.value = msg
                with self.update_lock:
                    self.page.update()

        preset_dropdown.on_change = apply_preset

        def save(e: ft.ControlEvent) -> None:
            if not object_field.value or not field_field.value or value_field.value == "":
                error_text.value = "Object, field and value are required."
                self.page.update()
                return
            if is_edit:
                target.object_name = object_field.value
                target.field_name = field_field.value
                target.operator = operator_dropdown.value
                target.value = value_field.value
                target.message = message_field.value
            else:
                printer.targets.append(
                    Target(
                        object_name=object_field.value,
                        field_name=field_field.value,
                        operator=operator_dropdown.value,
                        value=value_field.value,
                        message=message_field.value,
                    )
                )
            self.config.save()
            self.monitor_manager.sync(self.config.printers)
            with self.update_lock:
                self.page.close(dialog)
                self.refresh_current_view()

        def cancel(e: ft.ControlEvent) -> None:
            with self.update_lock:
                self.page.close(dialog)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit target" if is_edit else "Add target"),
            content=ft.Column(
                [
                    preset_dropdown,
                    object_field,
                    field_field,
                    ft.Row([operator_dropdown, value_field]),
                    message_field,
                    error_text,
                ],
                tight=True,
                spacing=10,
                width=380,
            ),
            actions=[ft.TextButton("Cancel", on_click=cancel),
                     ft.FilledButton("Save", on_click=save)],
        )
        with self.update_lock:
            self.page.open(dialog)

    def toggle_target(self, printer: Printer, target: Target, value: bool) -> None:
        target.enabled = value
        self.config.save()
        self.monitor_manager.sync(self.config.printers)

    def delete_target(self, printer: Printer, target: Target) -> None:
        printer.targets.remove(target)
        self.config.save()
        self.monitor_manager.sync(self.config.printers)
        with self.update_lock:
            self.refresh_current_view()

    # -------------------------------------------------------------- settings
    def open_settings_dialog(self) -> None:
        native_switch = ft.Switch(
            label="OS native notifications", value=self.config.settings.use_native_notifications)
        in_app_switch = ft.Switch(label="In-app popup notifications",
                                  value=self.config.settings.use_in_app_notifications)
        info_text = ft.Text(
            "In-app popups are always shown automatically if native notifications are "
            "disabled or unsupported on your OS.",
            size=12,
            color=ft.Colors.OUTLINE,
        )

        def save(e: ft.ControlEvent) -> None:
            self.config.settings.use_native_notifications = native_switch.value
            self.config.settings.use_in_app_notifications = in_app_switch.value
            self.config.save()
            with self.update_lock:
                self.page.close(dialog)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Settings"),
            content=ft.Column([native_switch, in_app_switch,
                              info_text], tight=True, spacing=10, width=380),
            actions=[ft.FilledButton("Done", on_click=save)],
        )
        with self.update_lock:
            self.page.open(dialog)

    # -------------------------------------------------------------- helpers
    def refresh_current_view(self) -> None:
        with self.update_lock:
            self.route_change(None)

    def show_in_app_notification(self, title: str, message: str) -> None:
        with self.update_lock:
            self.page.open(
                ft.SnackBar(
                    content=ft.Column(
                        [ft.Text(title, weight=ft.FontWeight.BOLD),
                         ft.Text(message)],
                        tight=True,
                        spacing=2,
                    ),
                    duration=6000,
                )
            )
            self.page.update()

    def on_status_update(self, printer_id: str, info: dict) -> None:
        self.status_cache[printer_id] = info
        text_control = self.printer_status_texts.get(printer_id)
        dot_control = self.printer_status_dots.get(printer_id)
        if text_control is None or dot_control is None:
            return
        summary, color = format_status(info)
        with self.update_lock:
            text_control.value = summary
            dot_control.color = color
            try:
                self.page.update()
            except Exception:
                pass


def main(page: ft.Page) -> None:
    PrinterNotifyApp(page)
