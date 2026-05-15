"""
main.py — Entry point for the YDM application.

Startup sequence
----------------
1. Initialise logging.
2. Create Database, Downloader, QueueManager.
3. Start YDMServer in a background thread (asyncio event loop).
4. Create YDMApp and run the GTK main loop.
5. On exit: stop the server thread and clean up.

Usage:
    python3 -m ydm.main
    # or via the .desktop launcher / install script
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import threading
from pathlib import Path

# Allow both launch styles to work:
#   python3 ydm/main.py
#   python3 -m ydm.main
# When this file is executed directly, sys.path points at ydm/, not the
# project root, so absolute imports like `from ydm.core...` would fail.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Logging setup — do this first so every subsequent import can use the logger
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ydm.main")

# ---------------------------------------------------------------------------
# GTK / libadwaita — must set version *before* importing anything from gi
# ---------------------------------------------------------------------------
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

# ---------------------------------------------------------------------------
# Core layer
# ---------------------------------------------------------------------------
from ydm.core.database import Database  # noqa: E402
from ydm.core.downloader import Downloader  # noqa: E402
from ydm.core.queue_manager import QueueManager  # noqa: E402
from ydm.core.server import YDMServer  # noqa: E402

# ---------------------------------------------------------------------------
# GUI layer
# ---------------------------------------------------------------------------
from ydm.gui.app import YDMApp  # noqa: E402

# ---------------------------------------------------------------------------
# Server thread helpers
# ---------------------------------------------------------------------------


def _run_server_in_thread(
    server: YDMServer,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """
    Target function for the background server thread.

    Sets the provided event loop as the current loop for this thread and
    runs it until ``loop.stop()`` is called from the main thread.
    """
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.start())
        loop.run_forever()
    except Exception as exc:
        logger.exception("Server thread crashed: %s", exc)
    finally:
        # Best-effort cleanup of aiohttp resources
        try:
            loop.run_until_complete(server.stop())
        except Exception:
            pass
        loop.close()
        logger.info("Server event loop closed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """
    Application entry point.  Returns the GTK exit code.
    """
    logger.info("YDM starting up…")

    # ── 1. Core objects ────────────────────────────────────────────────
    try:
        database = Database()
        database.initialize()
        logger.info("Database initialised.")
    except Exception as exc:
        logger.critical("Cannot initialise database: %s", exc)
        return 1

    try:
        downloader = Downloader()
        logger.info("Downloader initialised.")
    except Exception as exc:
        logger.critical("Cannot initialise downloader: %s", exc)
        return 1

    try:
        queue_manager = QueueManager(database, downloader)
        queue_manager.load_from_db()
        logger.info("QueueManager initialised.")
    except Exception as exc:
        logger.critical("Cannot initialise QueueManager: %s", exc)
        return 1

    # ── 2. Background aiohttp server ───────────────────────────────────
    server_loop = asyncio.new_event_loop()
    server = YDMServer(queue_manager, database, downloader)

    server_thread = threading.Thread(
        target=_run_server_in_thread,
        args=(server, server_loop),
        name="ydm-server",
        daemon=True,  # dies automatically when the main thread exits
    )
    server_thread.start()
    logger.info("Server thread started.")

    # ── 3. GTK application ─────────────────────────────────────────────
    app = YDMApp(queue_manager, database, downloader)

    # Stash references so YDMApp.do_shutdown() can join the server thread
    app._server_loop = server_loop  # type: ignore[attr-defined]
    app._server_thread = server_thread  # type: ignore[attr-defined]

    # Handle Ctrl-C in the terminal
    def _sigint_handler(signum, frame):  # noqa: ANN001, ARG001
        logger.info("KeyboardInterrupt — requesting application quit.")
        from gi.repository import GLib  # noqa: PLC0415

        GLib.idle_add(app.quit)

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        exit_code = app.run(sys.argv)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt caught outside GTK loop — exiting.")
        exit_code = 0

    # ── 4. Clean up server thread ──────────────────────────────────────
    logger.info("Stopping server thread…")
    try:
        server_loop.call_soon_threadsafe(server_loop.stop)
    except Exception:
        pass
    server_thread.join(timeout=5)
    if server_thread.is_alive():
        logger.warning("Server thread did not stop in time.")

    logger.info("YDM exited cleanly (code=%d).", exit_code)
    return exit_code


# ---------------------------------------------------------------------------
# Module guard
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sys.exit(main())
