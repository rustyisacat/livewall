"""Settings dialog — the Windows-GUI counterpart to gui.py's SettingsScreen.
Same fields (backend, random interval/favorites/tags, Material You opt-out),
plus Windows-specific automation toggles that call straight into
windows/task_scheduler.py and windows/startup.py, mirroring how the Linux
SettingsScreen calls systemd.py directly on save.
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from livewall.backends import BackendUnavailableError, WallpaperBackend, available_backend_names, get_backend
from livewall.config import RANDOM_INTERVAL_SECONDS, Config

logger = logging.getLogger("livewall.gui_qt")


class SettingsDialog(QDialog):
    def __init__(self, config: Config, backend: WallpaperBackend, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LiveWall Settings")
        self.config = config
        self.backend = backend
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(available_backend_names())
        self.backend_combo.setCurrentText(self.config.backend)
        form.addRow("Wallpaper backend", self.backend_combo)

        self.interval_combo = QComboBox()
        self.interval_combo.addItems(list(RANDOM_INTERVAL_SECONDS))
        self.interval_combo.setCurrentText(self.config.random_interval)
        form.addRow("Random interval", self.interval_combo)

        self.favorites_only_check = QCheckBox()
        self.favorites_only_check.setChecked(self.config.random_favorites_only)
        form.addRow("Random: favorites only", self.favorites_only_check)

        self.tags_edit = QLineEdit(", ".join(self.config.random_tags))
        form.addRow("Random: tags", self.tags_edit)

        self.no_smart_check = QCheckBox()
        self.no_smart_check.setChecked(self.config.no_smart_colours)
        form.addRow("Skip Material You recolour (caelestia-aw only)", self.no_smart_check)

        # Battery saver — only meaningful if the currently-configured backend
        # actually supports pause/resume (capability-gated, same pattern the
        # Linux doctor/battery-saver install commands already use).
        if self.backend.supports_pause and self.backend.supports_resume:
            battery_group = QGroupBox("Battery saver")
            battery_form = QFormLayout(battery_group)
            self.battery_low_spin = QSpinBox(minimum=1, maximum=99, value=self.config.battery_saver_low)
            self.battery_high_spin = QSpinBox(minimum=2, maximum=100, value=self.config.battery_saver_high)
            battery_form.addRow("Pause at or below (%)", self.battery_low_spin)
            battery_form.addRow("Resume at or above (%)", self.battery_high_spin)
            self.battery_saver_check = QCheckBox("Enabled")
            try:
                from livewall.windows import task_scheduler

                self.battery_saver_check.setChecked(task_scheduler.is_power_saver_installed())
            except Exception:
                pass
            battery_form.addRow(self.battery_saver_check)
            root.addWidget(battery_group)
        else:
            self.battery_low_spin = None
            self.battery_high_spin = None
            self.battery_saver_check = None

        # Windows-only automation — no Linux equivalent needed here since
        # hypr.py/systemd.py already cover this on Linux via the CLI.
        windows_group = QGroupBox("Windows startup")
        windows_form = QFormLayout(windows_group)
        self.autostart_check = QCheckBox("Run LiveWall in the system tray at login "
                                          "(needed for the quick-picker hotkey)")
        self.restore_check = QCheckBox("Re-apply the last wallpaper at login")
        try:
            from livewall.windows import startup, task_scheduler

            self.autostart_check.setChecked(startup.is_autostart_installed())
            self.restore_check.setChecked(task_scheduler.is_restore_installed())
        except Exception:
            pass
        windows_form.addRow(self.autostart_check)
        windows_form.addRow(self.restore_check)
        root.addWidget(windows_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _save(self) -> None:
        old_backend_name = self.config.backend
        new_backend_name = self.backend_combo.currentText()
        backend_changed = new_backend_name != old_backend_name

        new_interval = self.interval_combo.currentText()
        interval_changed = new_interval != self.config.random_interval

        self.config.backend = new_backend_name
        self.config.random_interval = new_interval
        self.config.random_favorites_only = self.favorites_only_check.isChecked()
        self.config.random_tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        self.config.no_smart_colours = self.no_smart_check.isChecked()
        if self.battery_low_spin is not None:
            self.config.battery_saver_low = self.battery_low_spin.value()
            self.config.battery_saver_high = self.battery_high_spin.value()
        self.config.save()

        if backend_changed:
            try:
                get_backend(old_backend_name).stop()
            except Exception as exc:
                logger.warning("Couldn't cleanly stop the previous backend: %s", exc)
            try:
                self.backend = get_backend(new_backend_name)
            except BackendUnavailableError as exc:
                logger.warning("Failed to switch backend: %s", exc)

        from livewall.windows import task_scheduler

        if interval_changed:
            try:
                if new_interval == "off":
                    if task_scheduler.is_installed():
                        task_scheduler.uninstall()
                else:
                    task_scheduler.install(new_interval)
            except Exception as exc:
                logger.warning("Failed to update the random-rotation task: %s", exc)

        if self.battery_saver_check is not None:
            try:
                wants_enabled = self.battery_saver_check.isChecked()
                currently_enabled = task_scheduler.is_power_saver_installed()
                if wants_enabled and not currently_enabled:
                    task_scheduler.install_power_saver()
                elif not wants_enabled and currently_enabled:
                    task_scheduler.uninstall_power_saver()
            except Exception as exc:
                logger.warning("Failed to update the battery-saver task: %s", exc)

        from livewall.windows import startup

        try:
            wants_autostart = self.autostart_check.isChecked()
            if wants_autostart and not startup.is_autostart_installed():
                startup.install_autostart()
            elif not wants_autostart and startup.is_autostart_installed():
                startup.uninstall_autostart()

            wants_restore = self.restore_check.isChecked()
            if wants_restore and not task_scheduler.is_restore_installed():
                task_scheduler.install_restore_service()
            elif not wants_restore and task_scheduler.is_restore_installed():
                task_scheduler.uninstall_restore_service()
        except Exception as exc:
            logger.warning("Failed to update Windows startup settings: %s", exc)

        self.accept()
