"""
tray.py — System tray icon for YDM.

Tries AppIndicator3 first (most GNOME/Ubuntu setups); falls back to
Gtk.StatusIcon for other desktops.  Either way, a right-click menu is
provided with the standard actions.
"""

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

# GTK4 removed Gtk.Menu entirely.  AppIndicator3 requires GTK3's GtkMenu*.
# We detect this at runtime and disable the tray gracefully.
_GTK3_MENUS_AVAILABLE = hasattr(Gtk, "Menu")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Attempt to import AppIndicator3 (optional dependency)
# ---------------------------------------------------------------------------
_INDICATOR_AVAILABLE = False
try:
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3  # noqa: F401

    _INDICATOR_AVAILABLE = True
    logger.info("AppIndicator3 available — using indicator tray icon.")
except Exception:
    logger.info("AppIndicator3 not available — falling back to Gtk.StatusIcon.")


# ---------------------------------------------------------------------------
# Icon name constants
# ---------------------------------------------------------------------------
_ICON_IDLE = "video-display-symbolic"
_ICON_ACTIVE = "folder-download-symbolic"
_ICON_ERROR = "dialog-warning-symbolic"


class TrayIcon:
    """
    Cross-backend system tray icon.

    Wraps either AppIndicator3 or a legacy Gtk.StatusIcon and exposes a
    unified ``update_icon`` interface.
    """

    def __init__(self, app, queue_manager) -> None:  # noqa: ANN001
        self._app = app
        self._queue_manager = queue_manager
        self._indicator = None
        self._status_icon = None
        self._tray_available = False

        if not _GTK3_MENUS_AVAILABLE:
            logger.warning(
                "GTK4 is loaded — Gtk.Menu does not exist. "
                "System tray icon is disabled. "
                "(AppIndicator3 requires GTK3's GtkMenu which is incompatible with GTK4.)"
            )
            return

        # Build the menu first (shared between both backends)
        self._menu = self._build_menu()

        if _INDICATOR_AVAILABLE:
            self._init_indicator()
        else:
            self._init_status_icon()

        self._tray_available = True

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------

    def _init_indicator(self) -> None:
        """Set up an AppIndicator3 indicator."""
        self._indicator = AppIndicator3.Indicator.new(
            "ydm-tray",
            _ICON_IDLE,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self._indicator.set_menu(self._menu)
        logger.debug("AppIndicator3 indicator created.")

    def _init_status_icon(self) -> None:
        """Set up a legacy Gtk.StatusIcon (GTK 3 compat layer)."""
        # Gtk.StatusIcon was removed in GTK4 but may still be provided via
        # a compatibility shim on some distributions.
        try:
            self._status_icon = Gtk.StatusIcon.new_from_icon_name(_ICON_IDLE)
            self._status_icon.set_tooltip_text("YDM — YouTube Download Manager")
            self._status_icon.connect("activate", self._on_activate)
            self._status_icon.connect("popup-menu", self._on_popup_menu)
            logger.debug("Gtk.StatusIcon created.")
        except Exception as exc:
            logger.warning("Could not create Gtk.StatusIcon: %s", exc)

    def _build_menu(self) -> Gtk.Menu:
        """Construct the right-click context menu."""
        # Gtk.Menu is still the standard for tray indicators.
        menu = Gtk.Menu()

        item_show = Gtk.MenuItem(label="Show YDM")
        item_show.connect("activate", self._on_show)
        menu.append(item_show)

        menu.append(Gtk.SeparatorMenuItem())

        item_pause_all = Gtk.MenuItem(label="Pause All")
        item_pause_all.connect("activate", self._on_pause_all)
        menu.append(item_pause_all)

        item_resume_all = Gtk.MenuItem(label="Resume All")
        item_resume_all.connect("activate", self._on_resume_all)
        menu.append(item_resume_all)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Quit")
        item_quit.connect("activate", self._on_quit)
        menu.append(item_quit)

        menu.show_all()
        return menu

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_icon(self, downloading: bool = False, error: bool = False) -> None:
        """
        Switch the tray icon to reflect the current application state.

        Parameters
        ----------
        downloading : bool
            ``True`` while any download is in progress.
        error : bool
            ``True`` when at least one download has failed.
        """
        if not self._tray_available:
            return
        if error:
            icon_name = _ICON_ERROR
        elif downloading:
            icon_name = _ICON_ACTIVE
        else:
            icon_name = _ICON_IDLE

        try:
            if self._indicator is not None:
                self._indicator.set_icon_full(icon_name, "YDM")
            elif self._status_icon is not None:
                self._status_icon.set_from_icon_name(icon_name)
        except Exception as exc:
            logger.warning("update_icon failed: %s", exc)

    def is_available(self) -> bool:
        """Return True if a tray icon was successfully created."""
        return self._tray_available

    # ------------------------------------------------------------------
    # Menu / signal callbacks
    # ------------------------------------------------------------------

    def _on_show(self, _item) -> None:
        """Bring the main window to the foreground."""
        try:
            windows = self._app.get_windows()
            if windows:
                windows[0].present()
        except Exception as exc:
            logger.warning("_on_show failed: %s", exc)

    def _on_pause_all(self, _item) -> None:
        """Pause every active download."""
        try:
            from ydm.core.models import DownloadStatus  # noqa: PLC0415

            for item in self._queue_manager.get_all():
                if item.status == DownloadStatus.DOWNLOADING:
                    self._queue_manager.pause(item.id)
        except Exception as exc:
            logger.warning("_on_pause_all failed: %s", exc)

    def _on_resume_all(self, _item) -> None:
        """Resume every paused download."""
        try:
            from ydm.core.models import DownloadStatus  # noqa: PLC0415

            for item in self._queue_manager.get_all():
                if item.status == DownloadStatus.PAUSED:
                    self._queue_manager.resume(item.id)
        except Exception as exc:
            logger.warning("_on_resume_all failed: %s", exc)

    def _on_quit(self, _item) -> None:
        """Quit the application cleanly."""
        GLib.idle_add(self._app.quit)

    # ------------------------------------------------------------------
    # StatusIcon-only callbacks
    # ------------------------------------------------------------------

    def _on_activate(self, _icon) -> None:
        """Left-click on StatusIcon → show main window."""
        self._on_show(None)

    def _on_popup_menu(self, _icon, button, activate_time) -> None:
        """Right-click on StatusIcon → show context menu."""
        self._menu.popup(None, None, None, None, button, activate_time)
