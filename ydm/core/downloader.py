"""
YDM Download Engine

Wraps yt-dlp to:
  • Fetch available formats for a given URL.
  • Execute downloads in background threads with progress reporting.
  • Support pause / resume / cancel without killing the process.

Thread model:
  Each download runs in its own daemon thread.  The thread checks a
  threading.Event (pause_event) and a threading.Event (cancel_event)
  inside the yt-dlp progress hook to implement cooperative pause/cancel.

NOTE FOR GUI LAYER:
  progress_callback and status_callback are called from the download thread.
  Wrap any GTK mutations in GLib.idle_add() on the GUI side.
"""

import glob
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import yt_dlp
from yt_dlp.utils import DownloadError as YtdlpDownloadError

from .models import DownloadItem, DownloadStatus, VideoFormat

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal state — one entry per in-flight download
# ---------------------------------------------------------------------------


class _DownloadState:
    """Holds the synchronisation primitives for one active download thread."""

    __slots__ = ("pause_event", "cancel_event", "thread")

    def __init__(self) -> None:
        self.pause_event: threading.Event = threading.Event()  # set → pause
        self.cancel_event: threading.Event = threading.Event()  # set → cancel
        self.thread: Optional[threading.Thread] = None


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def _filesize_label(size: Optional[int]) -> str:
    """Return a compact size string such as '~150 MB' or '' if unknown."""
    if size is None or size <= 0:
        return ""
    val: float = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024.0:
            return f"~{val:.0f} {unit}"
        val /= 1024.0
    return f"~{val:.1f} GB"


def _resolution_from_format(fmt: dict[str, Any]) -> str:
    """Derive a human-readable resolution string from a yt-dlp format dict."""
    height = fmt.get("height")
    if height:
        return f"{height}p"
    # audio-only stream
    vcodec = fmt.get("vcodec", "none") or "none"
    if vcodec == "none":
        return "audio only"
    return fmt.get("format_note", "") or fmt.get("resolution", "") or "unknown"


def _build_label(fmt: dict[str, Any], resolution: str, filesize: Optional[int]) -> str:
    """Build the human-readable label displayed in the format picker."""
    ext = (fmt.get("ext") or "").upper()
    size_str = _filesize_label(filesize)
    if resolution == "audio only":
        abr = fmt.get("abr")
        abr_str = f" {abr:.0f}kbps" if abr else ""
        return (
            f"Audio Only {ext}{abr_str} ({size_str})"
            if size_str
            else f"Audio Only {ext}{abr_str}"
        )
    parts = [resolution, ext]
    if size_str:
        parts.append(f"({size_str})")
    return " ".join(filter(None, parts))


# ---------------------------------------------------------------------------
# Downloader
# ---------------------------------------------------------------------------


class Downloader:
    """
    yt-dlp adapter for YDM.

    Public API::

        formats = downloader.get_video_formats(url)
        thread  = downloader.start_download(item, progress_cb, status_cb)
        downloader.pause_download(item_id)
        downloader.resume_download(item_id)
        downloader.cancel_download(item_id)
    """

    def __init__(self) -> None:
        # Maps item.id → _DownloadState
        self._states: dict[str, _DownloadState] = {}
        self._states_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Format discovery
    # ------------------------------------------------------------------

    def get_video_formats(self, url: str) -> list[VideoFormat]:
        """
        Extract and return available formats for *url* sorted best-first.

        Always prepends two synthetic entries:
          • "Best Quality" (format_id = "bestvideo+bestaudio/best")
          • "Audio Only MP3" (format_id = "bestaudio/best")

        Returns:
            list[VideoFormat]
        """
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }

        logger.info("Fetching formats for %s", url)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except YtdlpDownloadError as exc:
            logger.error("Failed to fetch formats: %s", exc)
            raise

        raw_formats: list[dict[str, Any]] = info.get("formats") or []
        seen_ids: set[str] = set()
        parsed: list[VideoFormat] = []

        for fmt in raw_formats:
            fmt_id = fmt.get("format_id", "")
            vcodec = (fmt.get("vcodec") or "none").lower()
            acodec = (fmt.get("acodec") or "none").lower()

            # Skip dash manifests with no usable streams
            if vcodec == "none" and acodec == "none":
                continue
            # Skip formats without a direct URL (DASH etc. that yt-dlp merges)
            if not fmt.get("url"):
                continue
            if fmt_id in seen_ids:
                continue
            seen_ids.add(fmt_id)

            filesize = fmt.get("filesize") or fmt.get("filesize_approx")
            resolution = _resolution_from_format(fmt)
            label = _build_label(fmt, resolution, filesize)

            parsed.append(
                VideoFormat(
                    format_id=fmt_id,
                    ext=fmt.get("ext", ""),
                    resolution=resolution,
                    filesize=int(filesize) if filesize else None,
                    vcodec=vcodec,
                    acodec=acodec,
                    label=label,
                    tbr=fmt.get("tbr"),
                )
            )

        # Sort: video formats by height desc, then audio-only by bitrate desc
        def _sort_key(vf: VideoFormat):
            # Extract numeric height if available
            try:
                h = int(vf.resolution.replace("p", ""))
            except ValueError:
                h = 0
            tbr = vf.tbr or 0
            return (h, tbr)

        parsed.sort(key=_sort_key, reverse=True)

        # Prepend the two synthetic convenience entries
        best = VideoFormat(
            format_id="bestvideo+bestaudio/best",
            ext="mp4",
            resolution="best",
            filesize=None,
            vcodec="avc1",
            acodec="mp4a",
            label="Best Quality (auto)",
        )
        audio_only = VideoFormat(
            format_id="bestaudio/best",
            ext="mp3",
            resolution="audio only",
            filesize=None,
            vcodec="none",
            acodec="mp3",
            label="Audio Only MP3 (best)",
        )

        return [best, audio_only] + parsed

    # ------------------------------------------------------------------
    # Download lifecycle
    # ------------------------------------------------------------------

    def start_download(
        self,
        item: DownloadItem,
        progress_callback: Callable[..., None],
        status_callback: Callable[..., None],
    ) -> threading.Thread:
        """
        Spawn a daemon thread that downloads *item* using yt-dlp.

        Args:
            item:              The DownloadItem to download.
            progress_callback: Called with (downloaded_bytes, total_bytes, speed, eta)
                               whenever yt-dlp reports progress.
            status_callback:   Called with (status: DownloadStatus, **kwargs) on
                               terminal events (COMPLETED or FAILED).
                               kwargs may contain 'filename' or 'error_message'.

        Returns:
            The started threading.Thread object.
        """
        state = _DownloadState()

        with self._states_lock:
            self._states[item.id] = state

        thread = threading.Thread(
            target=self._download_thread,
            args=(item, state, progress_callback, status_callback),
            name=f"ydm-dl-{item.id[:8]}",
            daemon=True,
        )
        state.thread = thread
        thread.start()
        logger.info("Started download thread for %s", item.id)
        return thread

    def pause_download(self, item_id: str) -> None:
        """Signal the download thread for *item_id* to pause."""
        with self._states_lock:
            state = self._states.get(item_id)
        if state:
            state.pause_event.set()
            logger.debug("Pause signalled for %s", item_id)

    def resume_download(self, item_id: str) -> None:
        """Clear the pause signal, allowing the download thread to continue."""
        with self._states_lock:
            state = self._states.get(item_id)
        if state:
            state.pause_event.clear()
            logger.debug("Resume signalled for %s", item_id)

    def cancel_download(self, item_id: str) -> None:
        """Signal the download thread for *item_id* to abort immediately."""
        with self._states_lock:
            state = self._states.get(item_id)
        if state:
            # Make sure we also clear the pause so the thread can wake up and see cancel
            state.pause_event.clear()
            state.cancel_event.set()
            logger.debug("Cancel signalled for %s", item_id)

    # ------------------------------------------------------------------
    # Private: download thread body
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_title(title: str) -> str:
        return "".join(c for c in title if c.isalnum() or c in " ._-()[]{}!@#$%^&'+,").strip()

    def _download_thread(
        self,
        item: DownloadItem,
        state: _DownloadState,
        progress_callback: Callable[..., None],
        status_callback: Callable[..., None],
    ) -> None:
        """
        Runs inside a background thread.  Uses yt-dlp to perform the actual
        download.  Calls *progress_callback* and *status_callback* as the
        download progresses or finishes.
        """

        def _progress_hook(d: dict[str, Any]) -> None:
            # ---- Handle cancel ----
            if state.cancel_event.is_set():
                raise _CancelDownloadError("Download cancelled by user")

            # ---- Handle pause (busy-wait with short sleep) ----
            while state.pause_event.is_set():
                if state.cancel_event.is_set():
                    raise _CancelDownloadError("Download cancelled during pause")
                time.sleep(0.25)

            dl_status = d.get("status", "")

            if dl_status == "downloading":
                downloaded = int(d.get("downloaded_bytes") or 0)
                total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                speed = float(d.get("speed") or 0.0)
                eta = int(d.get("eta") or 0)
                try:
                    progress_callback(downloaded, total, speed, eta)
                except Exception as cb_exc:  # noqa: BLE001
                    logger.warning("progress_callback raised: %s", cb_exc)

        # Build yt-dlp options
        save_path = item.save_path or str(Path.home() / "Downloads")
        is_audio_only = item.format_id.startswith("bestaudio")

        if item.title:
            safe_title = self._sanitize_title(item.title)
            outtmpl = os.path.join(save_path, safe_title + ".%(ext)s")
        else:
            safe_title = None
            outtmpl = os.path.join(save_path, "%(title)s.%(ext)s")

        ydl_opts: dict[str, Any] = {
            "format": item.format_id,
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [_progress_hook],
            "merge_output_format": "mp4",
            "nooverwrites": True,
        }

        if is_audio_only:
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
            ydl_opts.pop("merge_output_format", None)

        logger.info(
            "Downloading %s → %s (format=%s)", item.url, save_path, item.format_id
        )

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([item.url])

            # Determine actual filename on disk
            if safe_title:
                if is_audio_only:
                    final_ext = "mp3"
                else:
                    final_ext = "mp4"
                expected = os.path.join(save_path, f"{safe_title}.{final_ext}")
                if os.path.exists(expected):
                    actual_filename = os.path.basename(expected)
                else:
                    matches = sorted(
                        glob.glob(os.path.join(save_path, f"{safe_title}*")),
                        key=os.path.getmtime,
                        reverse=True,
                    )
                    actual_filename = os.path.basename(matches[0]) if matches else ""
            else:
                actual_filename = ""

            status_callback(DownloadStatus.COMPLETED, filename=actual_filename)

        except _CancelDownloadError:
            logger.info("Download %s was cancelled", item.id)
            status_callback(DownloadStatus.CANCELLED, error_message="Cancelled by user")

        except YtdlpDownloadError as exc:
            logger.error("Download %s failed: %s", item.id, exc)
            status_callback(DownloadStatus.FAILED, error_message=str(exc))

        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error in download thread %s", item.id)
            status_callback(DownloadStatus.FAILED, error_message=str(exc))

        finally:
            # Clean up state entry
            with self._states_lock:
                self._states.pop(item.id, None)


# ---------------------------------------------------------------------------
# Private exception for cooperative cancellation
# ---------------------------------------------------------------------------


class _CancelDownloadError(Exception):
    """Raised inside the yt-dlp progress hook to abort a download."""
