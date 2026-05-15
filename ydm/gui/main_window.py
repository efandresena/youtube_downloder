"""
main_window.py — The main application window for YDM.

Layout:
  ┌─────────────────────────────────────────┐
  │ [Adw.HeaderBar] "YDM"  [+] [⚙] [speed] │
  ├─────────────────────────────────────────┤
  │ Left sidebar: filter list               │
  │   • All Downloads                       │
  │   • Downloading                         │
  │   • Completed                           │
  │   • Failed                              │
  │                                         │
  │ Right pane: Gtk.ListBox (DownloadRows)  │
  │   [DownloadRow] …                       │
  │   [empty state label]                   │
  ├─────────────────────────────────────────┤
  │ Status bar: "3 active • 2.5 MB/s"      │
  └─────────────────────────────────────────┘

Threading rule: _on_item_updated() may be called from a background thread.
Every UI mutation in that path goes through GLib.idle_add().
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .add_dialog import AddDialog
from .download_row import DownloadRow, _fmt_speed  # reuse formatting helper
from .settings_dialog import SettingsDialog

if TYPE_CHECKING:
    from ydm.core.models import DownloadItem
    from ydm.core.queue_manager import QueueManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filter definitions
# ---------------------------------------------------------------------------
_FILTERS = [
    ("all", "All Downloads", None),
    ("downloading", "Downloading", ("downloading", "fetching_info", "queued")),
    ("completed", "Completed", ("completed",)),
    ("failed", "Failed", ("failed", "cancelled")),
]


class MainWindow(Adw.ApplicationWindow):
    """
    Primary window of the YDM application.

    Instantiated once by YDMApp.do_activate() and kept alive for the entire
    session.  Closing the window hides it (close-to-tray behaviour); the
    application only quits via the tray menu.
    """

    def __init__(
        self, app, queue_manager: "QueueManager", database, downloader
    ) -> None:
        super().__init__(application=app)
        self._qm = queue_manager
        self._db = database
        self._downloader = downloader
        self._rows: dict[str, DownloadRow] = {}  # item_id → DownloadRow
        self._active_filter: tuple | None = None  # None means show all

        self.set_title("YDM")
        self.set_default_size(900, 600)

        self._build_ui()
        self._register_callbacks()
        self._load_existing()

        # Speed label refresh timer (every second)
        GLib.timeout_add(1000, self._update_speed_label)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble the full window layout."""
        # ── Top-level layout ───────────────────────────────────────────
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root_box)

        # ── Header bar ─────────────────────────────────────────────────
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        root_box.append(header)

        # Title widget (centred)
        title_label = Gtk.Label(label="YDM")
        title_label.add_css_class("title")
        header.set_title_widget(title_label)

        # [+] Add button
        btn_add = Gtk.Button(label="+")
        btn_add.set_tooltip_text("Add Download")
        btn_add.add_css_class("suggested-action")
        btn_add.connect("clicked", self._on_add_clicked)
        header.pack_start(btn_add)

        # [⚙] Settings button
        btn_settings = Gtk.Button(icon_name="preferences-system-symbolic")
        btn_settings.set_tooltip_text("Settings")
        btn_settings.connect("clicked", self._on_settings_clicked)
        header.pack_end(btn_settings)

        # Speed indicator label
        self._speed_label = Gtk.Label(label="")
        self._speed_label.add_css_class("caption")
        self._speed_label.set_margin_end(8)
        header.pack_end(self._speed_label)

        # ── Overlay split view ─────────────────────────────────────────
        split_view = Adw.NavigationSplitView()
        split_view.set_vexpand(True)
        root_box.append(split_view)

        # ── Sidebar (left pane) ────────────────────────────────────────
        sidebar_nav_page = Adw.NavigationPage(title="Filters")
        sidebar_nav_page.set_tag("sidebar")
        split_view.set_sidebar(sidebar_nav_page)

        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_nav_page.set_child(sidebar_box)

        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_show_end_title_buttons(False)
        sidebar_box.append(sidebar_header)

        self._filter_list = Gtk.ListBox()
        self._filter_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._filter_list.add_css_class("navigation-sidebar")
        self._filter_list.connect("row-selected", self._on_filter_changed)

        for key, label, _statuses in _FILTERS:
            row = Gtk.ListBoxRow()
            row.set_name(key)
            row_label = Gtk.Label(
                label=label,
                xalign=0,
                margin_start=12,
                margin_end=12,
                margin_top=8,
                margin_bottom=8,
            )
            row.set_child(row_label)
            self._filter_list.append(row)

        # Select "All Downloads" by default
        self._filter_list.select_row(self._filter_list.get_row_at_index(0))

        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_vexpand(True)
        sidebar_scroll.set_child(self._filter_list)
        sidebar_box.append(sidebar_scroll)

        # ── Content pane (right) ───────────────────────────────────────
        content_nav_page = Adw.NavigationPage(title="Downloads")
        content_nav_page.set_tag("content")
        split_view.set_content(content_nav_page)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_nav_page.set_child(content_box)

        content_header = Adw.HeaderBar()
        content_header.set_show_end_title_buttons(True)
        content_box.append(content_header)

        # Scrollable download list
        content_scroll = Gtk.ScrolledWindow()
        content_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content_scroll.set_vexpand(True)
        content_box.append(content_scroll)

        # Overlay to show empty state on top of the list
        overlay = Gtk.Overlay()
        content_scroll.set_child(overlay)

        self._download_list = Gtk.ListBox()
        self._download_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._download_list.add_css_class("boxed-list")
        self._download_list.set_vexpand(True)
        overlay.set_child(self._download_list)

        # Empty state
        self._empty_state = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._empty_state.set_halign(Gtk.Align.CENTER)
        self._empty_state.set_valign(Gtk.Align.CENTER)

        empty_icon = Gtk.Image.new_from_icon_name("folder-download-symbolic")
        empty_icon.set_pixel_size(64)
        empty_icon.add_css_class("dim-label")
        self._empty_state.append(empty_icon)

        empty_label = Gtk.Label(label="No downloads yet")
        empty_label.add_css_class("title-2")
        empty_label.add_css_class("dim-label")
        self._empty_state.append(empty_label)

        empty_sub = Gtk.Label(label="Click + to add a YouTube URL")
        empty_sub.add_css_class("body")
        empty_sub.add_css_class("dim-label")
        self._empty_state.append(empty_sub)

        overlay.add_overlay(self._empty_state)

        # ── Status bar (bottom) ────────────────────────────────────────
        self._status_bar = Gtk.Label(label="Ready")
        self._status_bar.set_halign(Gtk.Align.START)
        self._status_bar.set_margin_start(12)
        self._status_bar.set_margin_top(4)
        self._status_bar.set_margin_bottom(4)
        self._status_bar.add_css_class("caption")
        root_box.append(self._status_bar)

        # ── Close-to-tray: intercept the window's delete event ─────────
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------

    def _register_callbacks(self) -> None:
        """Register the queue update callback with the QueueManager."""
        try:
            self._qm.set_update_callback(self._on_item_updated)
        except Exception as exc:
            logger.warning("Could not register update callback: %s", exc)

    def _load_existing(self) -> None:
        """Populate the list with any items already in the queue/history."""
        try:
            for item in self._qm.get_all():
                self._add_or_update_row(item)
        except Exception as exc:
            logger.warning("Could not load existing items: %s", exc)
        self._refresh_empty_state()

    # ------------------------------------------------------------------
    # Queue update callback (may be called from background thread)
    # ------------------------------------------------------------------

    def _on_item_updated(self, item: "DownloadItem") -> None:
        """Called by the QueueManager whenever an item changes state."""
        GLib.idle_add(self._add_or_update_row, item)

    def _add_or_update_row(self, item: "DownloadItem") -> bool:
        """Add a new DownloadRow or refresh an existing one.  Always on main thread."""
        if item.id in self._rows:
            row = self._rows[item.id]
            row.update(item)
        else:
            row = DownloadRow(item, self._qm)
            self._rows[item.id] = row
            self._download_list.append(row)
            # Notify on status changes via notifications (lazy import to avoid circulars)
            self._maybe_notify(item)

        self._apply_filter_to_row(row)
        self._refresh_empty_state()
        self._update_status_bar()
        return GLib.SOURCE_REMOVE

    def _maybe_notify(self, item: "DownloadItem") -> None:
        """Fire a desktop notification if the application owns one."""
        try:
            app = self.get_application()
            if hasattr(app, "_notifications"):
                status_key = (
                    item.status.value
                    if hasattr(item.status, "value")
                    else str(item.status)
                )
                notif = app._notifications
                if status_key == "completed":
                    import os

                    filepath = os.path.join(item.save_path or "", item.filename or "")
                    notif.notify_download_complete(item.title, filepath)
                elif status_key == "failed":
                    notif.notify_download_failed(
                        item.title, item.error_message or "Unknown error"
                    )
                elif status_key == "downloading":
                    notif.notify_download_started(item.title)
        except Exception as exc:
            logger.debug("Notification dispatch failed: %s", exc)

    # ------------------------------------------------------------------
    # Filter logic
    # ------------------------------------------------------------------

    def _on_filter_changed(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        """Called when the user picks a different filter in the sidebar."""
        if row is None:
            return
        key = row.get_name()
        # Look up the status tuple for this key
        self._active_filter = next(
            (statuses for k, _lbl, statuses in _FILTERS if k == key),
            None,
        )
        for download_row in self._rows.values():
            self._apply_filter_to_row(download_row)
        self._refresh_empty_state()

    def _apply_filter_to_row(self, row: DownloadRow) -> None:
        """Show or hide a DownloadRow based on the active filter."""
        if self._active_filter is None:
            row.set_visible(True)
            return
        status_key = (
            row._item.status.value
            if hasattr(row._item.status, "value")
            else str(row._item.status)
        )
        row.set_visible(status_key in self._active_filter)

    def _refresh_empty_state(self) -> None:
        """Show the empty-state overlay when no rows are visible."""
        # During UI construction, selecting the default filter can fire before
        # the empty-state widget has been created. In that case, do nothing;
        # _build_ui() calls this again after construction via _load_existing().
        if not hasattr(self, "_empty_state"):
            return
        any_visible = any(row.get_visible() for row in self._rows.values())
        self._empty_state.set_visible(not any_visible)

    # ------------------------------------------------------------------
    # Header bar button callbacks
    # ------------------------------------------------------------------

    def _on_add_clicked(self, _btn: Gtk.Button) -> None:
        dialog = AddDialog(self, self._qm, self._downloader, self._db)
        dialog.present(self)

    def _on_settings_clicked(self, _btn: Gtk.Button) -> None:
        dialog = SettingsDialog(self, self._db)
        dialog.present()

    # ------------------------------------------------------------------
    # Speed label (updated every second by GLib timer)
    # ------------------------------------------------------------------

    def _update_speed_label(self) -> bool:
        """Refresh the header speed indicator.  Returns True to keep timer alive."""
        try:
            speed = self._qm.get_total_speed()
            if speed > 0:
                self._speed_label.set_text(_fmt_speed(speed))
            else:
                self._speed_label.set_text("")
        except Exception:
            pass
        self._update_status_bar()
        return GLib.SOURCE_CONTINUE

    def _update_status_bar(self) -> None:
        """Rebuild the bottom status bar text."""
        try:
            from ydm.core.models import DownloadStatus  # noqa: PLC0415

            items = self._qm.get_all()
            active = sum(1 for i in items if i.status == DownloadStatus.DOWNLOADING)
            speed = self._qm.get_total_speed()
            parts = []
            if active:
                parts.append(f"{active} active")
            if speed > 0:
                parts.append(_fmt_speed(speed))
            self._status_bar.set_text(" • ".join(parts) if parts else "Ready")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Close-to-tray
    # ------------------------------------------------------------------

    def _on_close_request(self, _window: Gtk.Window) -> bool:
        """Hide to tray when a tray exists; otherwise quit normally."""
        app = self.get_application()
        tray = getattr(app, "_tray", None) if app is not None else None
        tray_available = bool(
            tray and hasattr(tray, "is_available") and tray.is_available()
        )

        if tray_available:
            self.set_visible(False)
        elif app is not None:
            app.quit()

        return True  # prevent default close/destroy
