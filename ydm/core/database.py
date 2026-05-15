"""
YDM SQLite Database Layer

Handles all persistence: download history and user settings.

Schema:
  downloads  — one row per DownloadItem
  settings   — simple key/value store for user preferences

Thread-safety:
  Each public method opens its own connection with check_same_thread=False
  and closes it after the operation.  This is safe because SQLite serialises
  writes internally, and download threads + the asyncio server thread may
  both call into this module concurrently.
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import DownloadItem, DownloadStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL_DOWNLOADS = """
CREATE TABLE IF NOT EXISTS downloads (
    id               TEXT PRIMARY KEY,
    url              TEXT NOT NULL,
    title            TEXT,
    thumbnail_url    TEXT,
    format_id        TEXT,
    status           TEXT,
    progress         REAL,
    speed            REAL,
    eta              INTEGER,
    filesize         INTEGER,
    downloaded_bytes INTEGER,
    save_path        TEXT,
    filename         TEXT,
    created_at       TEXT,
    completed_at     TEXT,
    error_message    TEXT
);
"""

_DDL_SETTINGS = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

_DEFAULT_SAVE_PATH = str(Path.home() / "Downloads")
_DEFAULT_MAX_CONCURRENT = 3


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class Database:
    """
    SQLite-backed persistence layer for YDM.

    Usage::

        db = Database()          # uses ~/.local/share/ydm/ydm.db
        db.initialize()
        db.add_download(item)
        items = db.get_all_downloads()
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            data_dir = Path.home() / ".local" / "share" / "ydm"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "ydm.db")

        self.db_path: str = db_path
        logger.debug("Database path: %s", self.db_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with row_factory set for dict-like access."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")  # better concurrency
        return conn

    def _row_to_item(self, row: sqlite3.Row) -> DownloadItem:
        """Convert a sqlite3.Row to a DownloadItem."""
        return DownloadItem.from_dict(dict(row))

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create tables if they do not already exist."""
        with self._connect() as conn:
            conn.execute(_DDL_DOWNLOADS)
            conn.execute(_DDL_SETTINGS)
            conn.commit()
        logger.info("Database initialised at %s", self.db_path)

    # ------------------------------------------------------------------
    # Downloads CRUD
    # ------------------------------------------------------------------

    def add_download(self, item: DownloadItem) -> None:
        """Insert a new DownloadItem row.  Raises if the id already exists."""
        sql = """
            INSERT INTO downloads (
                id, url, title, thumbnail_url, format_id,
                status, progress, speed, eta, filesize,
                downloaded_bytes, save_path, filename,
                created_at, completed_at, error_message
            ) VALUES (
                :id, :url, :title, :thumbnail_url, :format_id,
                :status, :progress, :speed, :eta, :filesize,
                :downloaded_bytes, :save_path, :filename,
                :created_at, :completed_at, :error_message
            )
        """
        with self._connect() as conn:
            conn.execute(sql, self._item_to_params(item))
            conn.commit()
        logger.debug("Added download %s (%s)", item.id, item.url)

    def update_download(self, item: DownloadItem) -> None:
        """Update all mutable fields of an existing DownloadItem row."""
        sql = """
            UPDATE downloads SET
                url              = :url,
                title            = :title,
                thumbnail_url    = :thumbnail_url,
                format_id        = :format_id,
                status           = :status,
                progress         = :progress,
                speed            = :speed,
                eta              = :eta,
                filesize         = :filesize,
                downloaded_bytes = :downloaded_bytes,
                save_path        = :save_path,
                filename         = :filename,
                created_at       = :created_at,
                completed_at     = :completed_at,
                error_message    = :error_message
            WHERE id = :id
        """
        with self._connect() as conn:
            conn.execute(sql, self._item_to_params(item))
            conn.commit()
        logger.debug("Updated download %s status=%s", item.id, item.status.value)

    def get_all_downloads(self) -> list:
        """Return all downloads ordered newest first."""
        sql = "SELECT * FROM downloads ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._row_to_item(r) for r in rows]

    def get_download_by_id(self, id: str) -> Optional[DownloadItem]:
        """Return a single DownloadItem by its UUID, or None if not found."""
        sql = "SELECT * FROM downloads WHERE id = ?"
        with self._connect() as conn:
            row = conn.execute(sql, (id,)).fetchone()
        return self._row_to_item(row) if row else None

    def delete_download(self, id: str) -> None:
        """Permanently remove a download record."""
        sql = "DELETE FROM downloads WHERE id = ?"
        with self._connect() as conn:
            conn.execute(sql, (id,))
            conn.commit()
        logger.debug("Deleted download %s", id)

    def search_downloads(self, query: str) -> list:
        """
        Return downloads whose title contains *query* (case-insensitive).
        Results are ordered newest first.
        """
        sql = """
            SELECT * FROM downloads
            WHERE title LIKE ?
            ORDER BY created_at DESC
        """
        pattern = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(sql, (pattern,)).fetchall()
        return [self._row_to_item(r) for r in rows]

    # ------------------------------------------------------------------
    # Settings CRUD
    # ------------------------------------------------------------------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Return the stored value for *key*, or *default* if not set."""
        sql = "SELECT value FROM settings WHERE key = ?"
        with self._connect() as conn:
            row = conn.execute(sql, (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Upsert a setting key/value pair."""
        sql = """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """
        with self._connect() as conn:
            conn.execute(sql, (key, value))
            conn.commit()
        logger.debug("Setting %r = %r", key, value)

    # ------------------------------------------------------------------
    # Convenience settings accessors
    # ------------------------------------------------------------------

    def get_default_save_path(self) -> str:
        """
        Return the configured default download directory.
        Falls back to ~/Downloads if the setting has never been set.
        """
        return (
            self.get_setting("default_save_path", _DEFAULT_SAVE_PATH)
            or _DEFAULT_SAVE_PATH
        )

    def get_max_concurrent(self) -> int:
        """
        Return the maximum number of simultaneous downloads.
        Falls back to 3 if the setting has never been configured.
        """
        raw = self.get_setting("max_concurrent", str(_DEFAULT_MAX_CONCURRENT))
        try:
            return int(raw)
        except (TypeError, ValueError):
            return _DEFAULT_MAX_CONCURRENT

    # ------------------------------------------------------------------
    # Private serialisation helper
    # ------------------------------------------------------------------

    @staticmethod
    def _item_to_params(item: DownloadItem) -> dict:
        """
        Convert a DownloadItem to a dict of SQL-safe primitives.
        datetimes → ISO-8601 strings, DownloadStatus → .value string.
        """
        return {
            "id": item.id,
            "url": item.url,
            "title": item.title,
            "thumbnail_url": item.thumbnail_url,
            "format_id": item.format_id,
            "status": item.status.value,
            "progress": item.progress,
            "speed": item.speed,
            "eta": item.eta,
            "filesize": item.filesize,
            "downloaded_bytes": item.downloaded_bytes,
            "save_path": item.save_path,
            "filename": item.filename,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "completed_at": item.completed_at.isoformat()
            if item.completed_at
            else None,
            "error_message": item.error_message,
        }
