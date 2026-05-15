"""
YDM Core Models
Data classes and enumerations for download items, formats, and status tracking.

NOTE FOR GUI LAYER:
  Any callback that mutates GTK widgets must be wrapped in GLib.idle_add().
  These models themselves have no GTK dependency — they are safe to use from
  any thread.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class DownloadStatus(Enum):
    """Lifecycle states for a single download task."""

    QUEUED = "queued"
    FETCHING_INFO = "fetching_info"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# VideoFormat
# ---------------------------------------------------------------------------


@dataclass
class VideoFormat:
    """
    Represents a single downloadable format returned by yt-dlp.

    Attributes:
        format_id:   yt-dlp internal format identifier (e.g. "137+140").
        ext:         File extension (e.g. "mp4", "webm", "m4a").
        resolution:  Human-readable resolution string (e.g. "1080p", "audio only").
        filesize:    Approximate file size in bytes, or None if unknown.
        vcodec:      Video codec string (e.g. "avc1", "vp9") or "none".
        acodec:      Audio codec string (e.g. "mp4a", "opus") or "none".
        label:       Full human-readable label shown in the GUI
                     (e.g. "1080p MP4 (~150 MB)", "Audio Only MP3").
        tbr:         Total bitrate in kbps, or None if unknown.
    """

    format_id: str
    ext: str
    resolution: str
    filesize: Optional[int]
    vcodec: str
    acodec: str
    label: str
    tbr: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict representation."""
        return {
            "format_id": self.format_id,
            "ext": self.ext,
            "resolution": self.resolution,
            "filesize": self.filesize,
            "vcodec": self.vcodec,
            "acodec": self.acodec,
            "label": self.label,
            "tbr": self.tbr,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VideoFormat":
        """Reconstruct a VideoFormat from a dict (e.g. from JSON)."""
        return cls(
            format_id=d["format_id"],
            ext=d.get("ext", ""),
            resolution=d.get("resolution", ""),
            filesize=d.get("filesize"),
            vcodec=d.get("vcodec", "none"),
            acodec=d.get("acodec", "none"),
            label=d.get("label", ""),
            tbr=d.get("tbr"),
        )


# ---------------------------------------------------------------------------
# DownloadItem
# ---------------------------------------------------------------------------


@dataclass
class DownloadItem:
    """
    Represents a single download task managed by YDM.

    A DownloadItem is created when the user (or browser extension) submits a URL,
    persisted to SQLite, and updated in-place as the download progresses.

    Attributes:
        id:               Unique UUID string for this download task.
        url:              The YouTube (or other yt-dlp supported) URL.
        title:            Video title, populated after info extraction.
        thumbnail_url:    URL of the video thumbnail.
        format_id:        yt-dlp format string (e.g. "bestvideo+bestaudio/best").
        status:           Current lifecycle state (see DownloadStatus).
        progress:         Download progress as a fraction [0.0, 1.0].
        speed:            Current download speed in bytes/sec.
        eta:              Estimated time remaining in seconds.
        filesize:         Total file size in bytes (may be 0 until known).
        downloaded_bytes: Bytes received so far.
        save_path:        Directory where the file will be saved.
        filename:         Final filename on disk (populated after completion).
        created_at:       Timestamp when the item was enqueued.
        completed_at:     Timestamp when the download finished (or None).
        error_message:    Human-readable error description on failure.
    """

    # Identity
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    url: str = ""

    # Metadata (populated after yt-dlp info extraction)
    title: str = ""
    thumbnail_url: str = ""

    # Format selection
    format_id: str = "best"

    # Runtime state
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float = 0.0  # 0.0 – 1.0
    speed: float = 0.0  # bytes/sec
    eta: int = 0  # seconds remaining
    filesize: int = 0  # total bytes (0 = unknown)
    downloaded_bytes: int = 0

    # File system
    save_path: str = ""
    filename: str = ""

    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None

    # Error info
    error_message: str = ""

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize to a JSON-safe dict suitable for HTTP API responses.

        datetime objects are converted to ISO-8601 strings.
        DownloadStatus is converted to its string .value.
        """
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "thumbnail_url": self.thumbnail_url,
            "format_id": self.format_id,
            "status": self.status.value,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "filesize": self.filesize,
            "downloaded_bytes": self.downloaded_bytes,
            "save_path": self.save_path,
            "filename": self.filename,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DownloadItem":
        """
        Deserialize a DownloadItem from a dict (e.g. from JSON or SQLite row).

        Handles optional/missing keys gracefully so it works for both API
        payloads (sparse) and full DB rows (complete).
        """

        def _parse_dt(value) -> Optional[datetime]:
            if value is None or value == "":
                return None
            if isinstance(value, datetime):
                return value
            try:
                return datetime.fromisoformat(value)
            except (ValueError, TypeError):
                return None

        def _parse_status(value) -> DownloadStatus:
            if isinstance(value, DownloadStatus):
                return value
            try:
                return DownloadStatus(value)
            except (ValueError, KeyError):
                return DownloadStatus.QUEUED

        return cls(
            id=d.get("id", str(uuid.uuid4())),
            url=d.get("url", ""),
            title=d.get("title", ""),
            thumbnail_url=d.get("thumbnail_url", ""),
            format_id=d.get("format_id", "best"),
            status=_parse_status(d.get("status", DownloadStatus.QUEUED)),
            progress=float(d.get("progress", 0.0)),
            speed=float(d.get("speed", 0.0)),
            eta=int(d.get("eta", 0)),
            filesize=int(d.get("filesize", 0)),
            downloaded_bytes=int(d.get("downloaded_bytes", 0)),
            save_path=d.get("save_path", ""),
            filename=d.get("filename", ""),
            created_at=_parse_dt(d.get("created_at")) or datetime.now(),
            completed_at=_parse_dt(d.get("completed_at")),
            error_message=d.get("error_message", ""),
        )

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """True while the download thread is running."""
        return self.status == DownloadStatus.DOWNLOADING

    @property
    def is_done(self) -> bool:
        """True when the item has reached a terminal state."""
        return self.status in (
            DownloadStatus.COMPLETED,
            DownloadStatus.FAILED,
            DownloadStatus.CANCELLED,
        )

    @property
    def speed_human(self) -> str:
        """Speed formatted as a human-readable string (e.g. '2.4 MB/s')."""
        return _format_bytes(self.speed) + "/s"

    @property
    def filesize_human(self) -> str:
        """Total file size as a human-readable string."""
        return _format_bytes(self.filesize) if self.filesize else "Unknown"

    def __repr__(self) -> str:
        return (
            f"DownloadItem(id={self.id!r}, title={self.title!r}, "
            f"status={self.status.value!r}, progress={self.progress:.1%})"
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _format_bytes(num_bytes: float) -> str:
    """Convert a byte count to a compact human-readable string."""
    if num_bytes <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"
