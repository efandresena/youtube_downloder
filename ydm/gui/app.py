"""
app.py — Adw.Application subclass for YDM.

Wires the application lifecycle:
  • do_activate()  → create / present the MainWindow
  • do_startup()   → set up tray icon and notifications
  • do_shutdown()  → signal the background server thread to stop

App ID: com.ydm.app
"""

from __future__ import annotations

import logging
import signal
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from .main_window import MainWindow
from .notifications import Notifications
from .tray import TrayIcon

if TYPE_CHECKING:
    from ydm.core.database import Database
    from ydm.core.downloader import Downloader
    from ydm.core.queue_manager import QueueManager

logger = logging.getLogger(__name__)

_APP_ID = "com.ydm.app"


class YDMApp(Adw.Application):
    """
    Root GTK/libadwaita application object.

    One instance is created in main.py and ``run()`` is called on it,
    which starts the GLib main loop.
    """

    def __init__(
        self,
        queue_manager: "QueueManager",
        database: "Database",
        downloader: "Downloader",
    ) -> None:
        super().__init__(
            application_id=_APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._qm = queue_manager
        self._db = database
        self._downloader = downloader

        self._window: MainWindow | None = None
        self._tray: TrayIcon | None = None
        self._notifications = Notifications()

        # Allow the OS to send SIGTERM (e.g. system shutdown) cleanly
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, self._on_sigterm)

    # ------------------------------------------------------------------
    # Adw.Application lifecycle overrides
    # ------------------------------------------------------------------

    def do_startup(self) -> None:
        """Called once before the first window is created."""
        Adw.Application.do_startup(self)
        self._setup_actions()
        logger.info("YDMApp started (app-id=%s)", _APP_ID)

    def do_activate(self) -> None:
        """Called when the application is launched or re-activated."""
        if self._window is None:
            self._window = MainWindow(
                self,
                self._qm,
                self._db,
                self._downloader,
            )
            # Create tray icon after window exists (needs app reference)
            try:
                self._tray = TrayIcon(self, self._qm)
            except Exception as exc:
                logger.warning("Tray icon could not be created: %s", exc)

        self._window.present()

    def do_shutdown(self) -> None:
        """Called when the application is about to quit."""
        logger.info("YDMApp shutting down…")
        self._stop_server()
        Adw.Application.do_shutdown(self)

    # ------------------------------------------------------------------
    # GAction helpers
    # ------------------------------------------------------------------

    def _setup_actions(self) -> None:
        """Register GActions for the application menu / shortcuts."""
        # quit action — also triggered from tray
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda _a, _p: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Ctrl>q"])

        # show-window action
        show_action = Gio.SimpleAction.new("show-window", None)
        show_action.connect("activate", self._on_show_window)
        self.add_action(show_action)

    def _on_show_window(self, _action, _param) -> None:
        if self._window:
            self._window.set_visible(True)
            self._window.present()

    # ------------------------------------------------------------------
    # Server shutdown helper
    # ------------------------------------------------------------------

    def _stop_server(self) -> None:
        """
        Signal the background aiohttp server thread to stop.

        The server thread is stored on the application object by main.py
        as ``_server_thread`` and ``_server_loop``.
        """
        loop = getattr(self, "_server_loop", None)
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
                logger.info("Server event loop stop requested.")
            except Exception as exc:
                logger.warning("Could not stop server loop: %s", exc)

        thread = getattr(self, "_server_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
            logger.info("Server thread joined.")

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_sigterm(self) -> bool:
        """Handle SIGTERM from the OS (e.g. `systemctl stop` or logout)."""
        logger.info("SIGTERM received — quitting.")
        self.quit()
        return GLib.SOURCE_REMOVE
