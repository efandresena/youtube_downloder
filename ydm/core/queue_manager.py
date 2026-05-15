"""
YDM Queue Manager

Orchestrates the download queue:
  • Maintains an ordered list of DownloadItems.
  • Starts new downloads automatically when a slot is free.
  • Delegates pause / resume / cancel to the Downloader.
  • Persists every state change to the Database.
  • Notifies the GUI (or any registered callback) via on_item_updated.

Thread-safety:
  _queue_lock protects all reads/writes to self.queue and self.active.
  _notify() posts updates via the registered callback.  The GUI layer
  is responsible for dispatching those calls onto the GTK main loop
  using GLib.idle_add().

NOTE FOR GUI LAYER:
  Register your update handler with set_update_callback().
  Inside that handler, always use GLib.idle_add() if you touch any
  GTK widget, because the callback is invoked from a download thread.
"""

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from .database import Database
from .downloader import Downloader
from .models import DownloadItem, DownloadStatus

logger = logging.getLogger(__name__)


class QueueManager:
    """
    Central controller for the YDM download queue.

    Typical usage::

        db      = Database(); db.initialize()
        dl      = Downloader()
        qm      = QueueManager(db, dl)
        qm.set_update_callback(my_gui_refresh)
        qm.load_from_db()

        item = DownloadItem(url="https://youtu.be/…")
        qm.add(item)
    """

    def __init__(self, database: Database, downloader: Downloader) -> None:
        self._db: Database = database
        self._dl: Downloader = downloader

        # Ordered list of all tracked items (includes completed/failed/cancelled)
        self.queue: list[DownloadItem] = []

        # Maps item.id → threading.Thread for currently downloading items
        self.active: dict[str, threading.Thread] = {}

        # Maximum simultaneous downloads (read from DB on init)
        self.max_concurrent: int = database.get_max_concurrent()

        # GUI callback — called with (DownloadItem,) on every state change
        self.on_item_updated: Optional[Callable[[DownloadItem], None]] = None

        # Mutex for queue / active dict mutations
        self._queue_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def set_update_callback(self, callback: Callable[[DownloadItem], None]) -> None:
        """
        Register a function to be called whenever a DownloadItem changes state.

        The callback receives the updated DownloadItem as its sole argument.
        It will be invoked from a download thread — wrap GTK calls in
        GLib.idle_add() on the GUI side.
        """
        self.on_item_updated = callback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _notify(self, item: DownloadItem) -> None:
        """
        Safely fire the registered update callback.
        No-op if no callback has been registered.
        """
        if self.on_item_updated is not None:
            try:
                self.on_item_updated(item)
            except Exception as exc:  # noqa: BLE001
                logger.warning("on_item_updated callback raised: %s", exc)

    def _find_item(self, item_id: str) -> Optional[DownloadItem]:
        """Return the DownloadItem with the given id, or None."""
        with self._queue_lock:
            for item in self.queue:
                if item.id == item_id:
                    return item
        return None

    # ------------------------------------------------------------------
    # Queue mutations
    # ------------------------------------------------------------------

    def add(self, item: DownloadItem) -> None:
        """
        Append *item* to the queue, persist it, and trigger scheduling.

        If a free download slot is available, the item may start immediately.
        """
        item.status = DownloadStatus.QUEUED
        with self._queue_lock:
            self.queue.append(item)
        self._db.add_download(item)
        logger.info("Queued: %s (%s)", item.id, item.url)
        self._maybe_start_next()

    def remove(self, item_id: str) -> bool:
        """
        Remove an item from the queue.  Only items that are NOT currently
        downloading may be removed this way; use cancel() first if needed.

        Returns True if the item was removed, False if not found or active.
        """
        with self._queue_lock:
            if item_id in self.active:
                logger.warning(
                    "Cannot remove active download %s; cancel it first", item_id
                )
                return False
            for i, item in enumerate(self.queue):
                if item.id == item_id:
                    del self.queue[i]
                    logger.debug("Removed %s from queue", item_id)
                    return True
        return False

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _maybe_start_next(self) -> None:
        """
        Start as many QUEUED downloads as the concurrency limit allows.
        Called after adding an item or after a download finishes.
        """
        with self._queue_lock:
            active_count = len(self.active)
            queued = [
                item for item in self.queue if item.status == DownloadStatus.QUEUED
            ]

        slots_available = self.max_concurrent - active_count
        for item in queued[:slots_available]:
            self._start_download(item)

    def _start_download(self, item: DownloadItem) -> None:
        """
        Transition *item* to DOWNLOADING and hand it off to the Downloader.
        """
        item.status = DownloadStatus.DOWNLOADING
        self._db.update_download(item)
        self._notify(item)

        def _on_progress(
            downloaded_bytes: int,
            total_bytes: int,
            speed: float,
            eta: int,
        ) -> None:
            """Called from the download thread on each progress tick."""
            item.downloaded_bytes = downloaded_bytes
            item.filesize = total_bytes if total_bytes > 0 else item.filesize
            item.speed = speed
            item.eta = eta
            if total_bytes > 0:
                item.progress = downloaded_bytes / total_bytes
            # Throttle DB writes — only write when progress changes by ≥0.5%
            # to avoid hammering SQLite with hundreds of writes per second.
            if int(item.progress * 200) % 1 == 0:
                self._db.update_download(item)
            self._notify(item)

        def _on_status(status: DownloadStatus, **kwargs) -> None:
            """Called from the download thread on terminal events."""
            item.status = status
            if status == DownloadStatus.COMPLETED:
                item.progress = 1.0
                item.speed = 0.0
                item.eta = 0
                item.completed_at = datetime.now()
                item.filename = kwargs.get("filename", item.filename)
            elif status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                item.speed = 0.0
                item.eta = 0
                item.error_message = kwargs.get("error_message", "")

            self._db.update_download(item)
            self._notify(item)

            # Free the active slot and try to schedule the next item
            with self._queue_lock:
                self.active.pop(item.id, None)
            logger.info("Download %s finished with status %s", item.id, status.value)
            self._maybe_start_next()

        thread = self._dl.start_download(item, _on_progress, _on_status)
        with self._queue_lock:
            self.active[item.id] = thread
        logger.info("Started download thread for %s", item.id)

    # ------------------------------------------------------------------
    # Pause / Resume / Cancel
    # ------------------------------------------------------------------

    def pause(self, item_id: str) -> bool:
        """
        Pause an active download.  The download thread will keep the connection
        open but stop writing data until resumed.

        Returns True if the signal was sent, False if the item is not active.
        """
        item = self._find_item(item_id)
        if item is None or item.status != DownloadStatus.DOWNLOADING:
            return False

        self._dl.pause_download(item_id)
        item.status = DownloadStatus.PAUSED
        item.speed = 0.0
        self._db.update_download(item)
        self._notify(item)
        logger.info("Paused %s", item_id)
        return True

    def resume(self, item_id: str) -> bool:
        """
        Resume a previously paused download.

        Returns True if the signal was sent, False if the item is not paused.
        """
        item = self._find_item(item_id)
        if item is None or item.status != DownloadStatus.PAUSED:
            return False

        self._dl.resume_download(item_id)
        item.status = DownloadStatus.DOWNLOADING
        self._db.update_download(item)
        self._notify(item)
        logger.info("Resumed %s", item_id)
        return True

    def cancel(self, item_id: str) -> bool:
        """
        Cancel an active or paused download.

        If the item is still QUEUED (not yet started), it is simply marked
        CANCELLED without touching the Downloader.

        Returns True on success, False if the item was not found.
        """
        item = self._find_item(item_id)
        if item is None:
            return False

        if item.status in (DownloadStatus.DOWNLOADING, DownloadStatus.PAUSED):
            self._dl.cancel_download(item_id)

        item.status = DownloadStatus.CANCELLED
        item.speed = 0.0
        item.eta = 0
        item.error_message = "Cancelled by user"
        self._db.update_download(item)
        self._notify(item)

        with self._queue_lock:
            self.active.pop(item_id, None)

        logger.info("Cancelled %s", item_id)
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_all(self) -> list:
        """Return a snapshot of all tracked DownloadItems (newest-added last)."""
        with self._queue_lock:
            return list(self.queue)

    def get_active_count(self) -> int:
        """Return the number of currently downloading items."""
        with self._queue_lock:
            return len(self.active)

    def get_total_speed(self) -> float:
        """Return the aggregate download speed across all active items (bytes/sec)."""
        with self._queue_lock:
            active_ids = set(self.active.keys())
        total = 0.0
        for item in self.queue:
            if item.id in active_ids:
                total += item.speed
        return total

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    def load_from_db(self) -> None:
        """
        Load all previously-persisted downloads from the database on startup.

        Items that were DOWNLOADING or PAUSED when the app last closed are
        re-queued as QUEUED so they will be retried automatically.
        Items that are COMPLETED, FAILED, or CANCELLED are loaded into the
        queue list for display in the history view but are not re-started.
        """
        items = self._db.get_all_downloads()
        # DB returns newest-first; we want chronological order in the queue.
        items.reverse()

        recoverable_statuses = {
            DownloadStatus.DOWNLOADING,
            DownloadStatus.PAUSED,
            DownloadStatus.FETCHING_INFO,
        }

        with self._queue_lock:
            for item in items:
                if item.status in recoverable_statuses:
                    logger.info(
                        "Recovering interrupted download: %s (%s → queued)",
                        item.id,
                        item.status.value,
                    )
                    item.status = DownloadStatus.QUEUED
                    item.progress = 0.0
                    item.speed = 0.0
                    item.eta = 0
                self.queue.append(item)

        logger.info("Loaded %d items from database", len(items))

        # Now start any recoverable items that fit within the concurrency limit
        self._maybe_start_next()

    # ------------------------------------------------------------------
    # Settings propagation
    # ------------------------------------------------------------------

    def reload_settings(self) -> None:
        """Re-read max_concurrent from the database (call after settings change)."""
        self.max_concurrent = self._db.get_max_concurrent()
        logger.debug("max_concurrent reloaded → %d", self.max_concurrent)
        self._maybe_start_next()
