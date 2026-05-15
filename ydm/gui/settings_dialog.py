"""
settings_dialog.py — Application preferences window for YDM.

Uses Adw.PreferencesWindow (libadwaita) with three preference groups:
  • Downloads    — default save path, max concurrent downloads
  • Network      — auto-retry, retry count
  • Browser Ext  — extension status, how-to link

All changes are persisted immediately via database.set_setting().
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

if TYPE_CHECKING:
    pass  # database is duck-typed; no need for a hard import

logger = logging.getLogger(__name__)

# Settings keys — must match what the Database layer expects
_KEY_SAVE_PATH = "default_save_path"
_KEY_MAX_CONCURRENT = "max_concurrent"
_KEY_AUTO_RETRY = "auto_retry"
_KEY_RETRY_COUNT = "retry_count"


class SettingsDialog(Adw.PreferencesWindow):
    """
    Application-wide settings window.

    All rows are backed by the database; changes take effect immediately
    without a "Save" button.
    """

    def __init__(self, parent: Gtk.Window, database) -> None:
        super().__init__()
        self._db = database
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title("YDM Preferences")
        self.set_default_size(560, 480)

        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Create all preference pages and groups."""
        page = Adw.PreferencesPage()
        page.set_title("General")
        page.set_icon_name("preferences-system-symbolic")
        self.add(page)

        # ── Downloads group ────────────────────────────────────────────
        dl_group = Adw.PreferencesGroup(
            title="Downloads",
            description="Configure where files are saved and how many run at once.",
        )
        page.add(dl_group)

        # Save path row
        self._save_path_row = Adw.ActionRow(
            title="Default Save Path",
            subtitle="Folder where downloaded files will be placed",
        )
        dl_group.add(self._save_path_row)

        self._save_path_label = Gtk.Label(label="")
        self._save_path_label.set_ellipsize(3)  # Pango END
        self._save_path_label.add_css_class("dim-label")
        self._save_path_label.set_valign(Gtk.Align.CENTER)
        self._save_path_row.add_suffix(self._save_path_label)

        btn_browse = Gtk.Button(label="Change…")
        btn_browse.set_valign(Gtk.Align.CENTER)
        btn_browse.connect("clicked", self._on_browse_save_path)
        self._save_path_row.add_suffix(btn_browse)
        self._save_path_row.set_activatable_widget(btn_browse)

        # Max concurrent row (SpinRow is Adw >= 1.4; use ActionRow + SpinButton for compat)
        self._concurrent_row = Adw.ActionRow(
            title="Max Concurrent Downloads",
            subtitle="How many downloads can run simultaneously (1–5)",
        )
        dl_group.add(self._concurrent_row)

        adjustment = Gtk.Adjustment(value=3, lower=1, upper=5, step_increment=1)
        self._concurrent_spin = Gtk.SpinButton(adjustment=adjustment, digits=0)
        self._concurrent_spin.set_valign(Gtk.Align.CENTER)
        self._concurrent_spin.connect("value-changed", self._on_concurrent_changed)
        self._concurrent_row.add_suffix(self._concurrent_spin)

        # ── Network group ──────────────────────────────────────────────
        net_group = Adw.PreferencesGroup(
            title="Network",
            description="Retry behaviour when downloads fail.",
        )
        page.add(net_group)

        # Auto-retry switch
        self._retry_switch_row = Adw.ActionRow(
            title="Auto-retry Failed Downloads",
            subtitle="Automatically restart downloads that fail due to network errors",
        )
        net_group.add(self._retry_switch_row)

        self._retry_switch = Gtk.Switch()
        self._retry_switch.set_valign(Gtk.Align.CENTER)
        self._retry_switch.connect("notify::active", self._on_retry_switch_changed)
        self._retry_switch_row.add_suffix(self._retry_switch)
        self._retry_switch_row.set_activatable_widget(self._retry_switch)

        # Retry count
        self._retry_count_row = Adw.ActionRow(
            title="Retry Count",
            subtitle="Number of times to retry a failed download (1–10)",
        )
        net_group.add(self._retry_count_row)

        retry_adjustment = Gtk.Adjustment(value=3, lower=1, upper=10, step_increment=1)
        self._retry_count_spin = Gtk.SpinButton(adjustment=retry_adjustment, digits=0)
        self._retry_count_spin.set_valign(Gtk.Align.CENTER)
        self._retry_count_spin.connect("value-changed", self._on_retry_count_changed)
        self._retry_count_row.add_suffix(self._retry_count_spin)

        # ── Browser Integration group ──────────────────────────────────
        ext_group = Adw.PreferencesGroup(
            title="Browser Integration",
            description="Native messaging host status and extension setup.",
        )
        page.add(ext_group)

        # Extension status row
        self._ext_status_row = Adw.ActionRow(
            title="Extension Status",
            subtitle="Checking…",
        )
        ext_group.add(self._ext_status_row)

        self._ext_status_icon = Gtk.Image()
        self._ext_status_icon.set_valign(Gtk.Align.CENTER)
        self._ext_status_icon.set_from_icon_name("emblem-ok-symbolic")
        self._ext_status_row.add_suffix(self._ext_status_icon)

        # How-to install
        self._howto_row = Adw.ActionRow(
            title="How to Install Browser Extension",
            subtitle="Learn how to enable one-click downloads from your browser",
        )
        self._howto_row.set_activatable(True)
        self._howto_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        self._howto_row.connect("activated", self._on_howto_clicked)
        ext_group.add(self._howto_row)

        # Check native host asynchronously
        GLib.idle_add(self._check_native_host_status)

    # ------------------------------------------------------------------
    # Load values from DB
    # ------------------------------------------------------------------

    def _load_values(self) -> None:
        """Populate widgets from the current database settings."""
        try:
            default_path = self._db.get_setting(
                _KEY_SAVE_PATH,
                os.path.expanduser("~/Downloads"),
            )
            self._save_path_label.set_text(default_path)
            self._current_save_path = default_path
        except Exception as exc:
            logger.warning("Could not load save path: %s", exc)
            self._current_save_path = os.path.expanduser("~/Downloads")

        try:
            max_c = int(self._db.get_setting(_KEY_MAX_CONCURRENT, "3"))
            self._concurrent_spin.set_value(max_c)
        except Exception as exc:
            logger.warning("Could not load max concurrent: %s", exc)

        try:
            auto_retry = (
                self._db.get_setting(_KEY_AUTO_RETRY, "false").lower() == "true"
            )
            self._retry_switch.set_active(auto_retry)
        except Exception as exc:
            logger.warning("Could not load auto_retry: %s", exc)

        try:
            retry_count = int(self._db.get_setting(_KEY_RETRY_COUNT, "3"))
            self._retry_count_spin.set_value(retry_count)
        except Exception as exc:
            logger.warning("Could not load retry_count: %s", exc)

    # ------------------------------------------------------------------
    # Callbacks — save immediately on change
    # ------------------------------------------------------------------

    def _on_browse_save_path(self, _btn: Gtk.Button) -> None:
        """Open a folder-chooser dialog for the default save path."""
        chooser = Gtk.FileDialog()
        chooser.set_title("Choose Default Save Folder")
        try:
            chooser.set_initial_folder(Gio.File.new_for_path(self._current_save_path))
        except Exception:
            pass
        chooser.select_folder(self, None, self._on_folder_chosen, None)

    def _on_folder_chosen(self, dialog, result, _user_data) -> None:
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                self._current_save_path = path
                self._save_path_label.set_text(path)
                self._db.set_setting(_KEY_SAVE_PATH, path)
        except Exception as exc:
            logger.debug("Folder chooser cancelled or failed: %s", exc)

    def _on_concurrent_changed(self, spin: Gtk.SpinButton) -> None:
        value = int(spin.get_value())
        try:
            self._db.set_setting(_KEY_MAX_CONCURRENT, str(value))
        except Exception as exc:
            logger.warning("Could not save max_concurrent: %s", exc)

    def _on_retry_switch_changed(self, switch: Gtk.Switch, _param) -> None:
        active = switch.get_active()
        try:
            self._db.set_setting(_KEY_AUTO_RETRY, "true" if active else "false")
        except Exception as exc:
            logger.warning("Could not save auto_retry: %s", exc)

    def _on_retry_count_changed(self, spin: Gtk.SpinButton) -> None:
        value = int(spin.get_value())
        try:
            self._db.set_setting(_KEY_RETRY_COUNT, str(value))
        except Exception as exc:
            logger.warning("Could not save retry_count: %s", exc)

    def _on_howto_clicked(self, _row: Adw.ActionRow) -> None:
        """Show a simple info dialog explaining how to install the extension."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Installing the Browser Extension",
            body=(
                "To enable one-click downloading from your browser:\n\n"
                "1. Open Firefox or Brave.\n"
                "2. Load the extension from the browser-extension/ folder.\n"
                "   • Firefox: go to about:debugging → Load Temporary Add-on\n"
                "   • Brave/Chrome: go to chrome://extensions → Load Unpacked\n"
                "3. Run the install_native_host.sh script to register the\n"
                "   native messaging host with your browser.\n\n"
                "After that, a YDM button will appear on YouTube video pages."
            ),
        )
        dialog.add_response("close", "Got it")
        dialog.set_default_response("close")
        dialog.connect("response", lambda d, _r: d.close())
        dialog.present()

    # ------------------------------------------------------------------
    # Native host status check
    # ------------------------------------------------------------------

    def _check_native_host_status(self) -> bool:
        """Check whether the native messaging host JSON is installed."""
        firefox_path = os.path.expanduser(
            "~/.mozilla/native-messaging-hosts/com.ydm.native_host.json"
        )
        chrome_path = os.path.expanduser(
            "~/.config/google-chrome/NativeMessagingHosts/com.ydm.native_host_chrome.json"
        )
        brave_path = os.path.expanduser(
            "~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/com.ydm.native_host_chrome.json"
        )

        firefox_ok = os.path.isfile(firefox_path)
        chromium_ok = os.path.isfile(chrome_path) or os.path.isfile(brave_path)

        if firefox_ok and chromium_ok:
            subtitle = "Native host installed for Firefox and Chromium"
            icon = "emblem-ok-symbolic"
        elif firefox_ok:
            subtitle = "Native host installed for Firefox only"
            icon = "dialog-warning-symbolic"
        elif chromium_ok:
            subtitle = "Native host installed for Chromium/Brave only"
            icon = "dialog-warning-symbolic"
        else:
            subtitle = "Native host not installed — run install_native_host.sh"
            icon = "dialog-error-symbolic"

        self._ext_status_row.set_subtitle(subtitle)
        self._ext_status_icon.set_from_icon_name(icon)
        return GLib.SOURCE_REMOVE
