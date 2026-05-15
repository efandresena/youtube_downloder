"""
download_row.py — A single row in the YDM download list.

Each DownloadRow renders one DownloadItem with:
  • Async-loaded thumbnail (80×60)
  • Title, progress bar, speed/ETA, size labels
  • Coloured status badge
  • Contextual action buttons (Pause/Resume, Cancel, Open File, Remove)
"""

from __future__ import annotations

import logging
import os
import threading
import urllib.request
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import (  # noqa: E402
    GdkPixbuf,
    Gio,
    GLib,
    Gtk,
)

if TYPE_CHECKING:
    from ydm.core.models import DownloadItem
    from ydm.core.queue_manager import QueueManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status colours (CSS hex strings)
# ---------------------------------------------------------------------------
_STATUS_COLOURS = {
    "queued": ("#9a9996", "Queued"),
    "fetching_info": ("#3584e4", "Fetching…"),
    "downloading": ("#3584e4", "Downloading"),
    "paused": ("#e5a50a", "Paused"),
    "completed": ("#26a269", "Done"),
    "failed": ("#c01c28", "Failed"),
    "cancelled": ("#9a9996", "Cancelled"),
}

_THUMB_W = 80
_THUMB_H = 60


# ---------------------------------------------------------------------------
# Helper: format_* functions
# ---------------------------------------------------------------------------


def _fmt_speed(bps: float) -> str:
    """Return a human-readable speed string."""
    if bps <= 0:
        return ""
    if bps >= 1_048_576:
        return f"{bps / 1_048_576:.1f} MB/s"
    if bps >= 1_024:
        return f"{bps / 1_024:.0f} KB/s"
    return f"{bps:.0f} B/s"


def _fmt_eta(seconds: int) -> str:
    """Return a human-readable ETA string (H:MM:SS or M:SS)."""
    if seconds <= 0:
        return ""
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_size(n_bytes: int) -> str:
    """Return a human-readable file size string."""
    if n_bytes <= 0:
        return ""
    if n_bytes >= 1_073_741_824:
        return f"{n_bytes / 1_073_741_824:.1f} GB"
    if n_bytes >= 1_048_576:
        return f"{n_bytes / 1_048_576:.1f} MB"
    if n_bytes >= 1_024:
        return f"{n_bytes / 1_024:.0f} KB"
    return f"{n_bytes} B"


# ---------------------------------------------------------------------------
# DownloadRow
# ---------------------------------------------------------------------------


class DownloadRow(Gtk.ListBoxRow):
    """
    A single row widget representing one DownloadItem.

    Layout (horizontal box):
      [Thumbnail]  [Info column (expand)]  [Action buttons (vertical)]
    """

    def __init__(self, item: "DownloadItem", queue_manager: "QueueManager") -> None:
        super().__init__()
        self._item = item
        self._qm = queue_manager
        self._thumb_url_loaded: str | None = None  # track which URL was fetched

        self.set_activatable(False)
        self.add_css_class("download-row")
        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.set_margin_start(8)
        self.set_margin_end(8)

        self._build_ui()
        self.update(item)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ── Outer frame ────────────────────────────────────────────────
        frame = Gtk.Frame()
        frame.set_hexpand(True)
        self.set_child(frame)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        frame.set_child(outer)

        # ── Thumbnail ──────────────────────────────────────────────────
        self._thumb_image = Gtk.Image()
        self._thumb_image.set_pixel_size(_THUMB_H)
        self._thumb_image.set_size_request(_THUMB_W, _THUMB_H)
        self._thumb_image.set_from_icon_name("video-display-symbolic")
        self._thumb_image.add_css_class("thumbnail-placeholder")
        outer.append(self._thumb_image)

        # ── Info column ────────────────────────────────────────────────
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        info_box.set_hexpand(True)
        info_box.set_valign(Gtk.Align.CENTER)
        outer.append(info_box)

        # Title
        self._title_label = Gtk.Label(label="")
        self._title_label.set_halign(Gtk.Align.START)
        self._title_label.set_ellipsize(3)  # Pango.EllipsizeMode.END == 3
        self._title_label.set_max_width_chars(60)
        self._title_label.add_css_class("heading")
        info_box.append(self._title_label)

        # Progress bar
        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_hexpand(True)
        info_box.append(self._progress_bar)

        # Speed / ETA / percentage
        self._speed_label = Gtk.Label(label="")
        self._speed_label.set_halign(Gtk.Align.START)
        self._speed_label.add_css_class("caption")
        info_box.append(self._speed_label)

        # Size
        self._size_label = Gtk.Label(label="")
        self._size_label.set_halign(Gtk.Align.START)
        self._size_label.add_css_class("caption-heading")
        info_box.append(self._size_label)

        # Status badge
        self._status_label = Gtk.Label(label="")
        self._status_label.set_halign(Gtk.Align.START)
        # Inline CSS is applied dynamically in update()
        info_box.append(self._status_label)

        # ── Action buttons (vertical) ──────────────────────────────────
        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        btn_box.set_valign(Gtk.Align.CENTER)
        outer.append(btn_box)

        self._btn_pause_resume = Gtk.Button()
        self._btn_pause_resume.connect("clicked", self._on_pause_resume)
        btn_box.append(self._btn_pause_resume)

        self._btn_cancel = Gtk.Button(label="Cancel")
        self._btn_cancel.connect("clicked", self._on_cancel)
        self._btn_cancel.add_css_class("destructive-action")
        btn_box.append(self._btn_cancel)

        self._btn_open = Gtk.Button(label="Open File")
        self._btn_open.connect("clicked", self._on_open_file)
        self._btn_open.add_css_class("suggested-action")
        btn_box.append(self._btn_open)

        self._btn_remove = Gtk.Button(label="Remove")
        self._btn_remove.connect("clicked", self._on_remove)
        self._btn_remove.add_css_class("destructive-action")
        btn_box.append(self._btn_remove)

    # ------------------------------------------------------------------
    # Public update method
    # ------------------------------------------------------------------

    def update(self, item: "DownloadItem") -> None:
        """Refresh all widgets to reflect the current state of *item*."""
        self._item = item
        status_key = (
            item.status.value if hasattr(item.status, "value") else str(item.status)
        )
        colour, status_text = _STATUS_COLOURS.get(
            status_key, ("#9a9996", status_key.title())
        )

        # Title
        self._title_label.set_text(item.title or item.url or "Unknown")

        # Progress bar visibility
        is_active = status_key in ("downloading", "fetching_info", "paused")
        self._progress_bar.set_visible(is_active)
        if is_active:
            self._progress_bar.set_fraction(max(0.0, min(1.0, item.progress)))

        # Speed / ETA label
        if status_key == "downloading":
            parts = []
            pct = f"{item.progress * 100:.1f}%"
            parts.append(pct)
            spd = _fmt_speed(item.speed)
            if spd:
                parts.append(spd)
            eta = _fmt_eta(item.eta)
            if eta:
                parts.append(f"ETA {eta}")
            self._speed_label.set_text(" • ".join(parts))
            self._speed_label.set_visible(True)
        else:
            self._speed_label.set_visible(False)

        # Size label
        if item.filesize > 0:
            dl = _fmt_size(item.downloaded_bytes)
            total = _fmt_size(item.filesize)
            self._size_label.set_text(f"{dl} / {total}" if dl else total)
            self._size_label.set_visible(True)
        else:
            self._size_label.set_visible(False)

        # Status badge — use a CSS colour via markup
        self._status_label.set_markup(
            f'<span foreground="{colour}" weight="bold">{status_text}</span>'
        )

        # ── Buttons ────────────────────────────────────────────────────
        # Pause/Resume — visible only when downloading or paused
        if status_key in ("downloading", "fetching_info"):
            self._btn_pause_resume.set_label("Pause")
            self._btn_pause_resume.set_visible(True)
        elif status_key == "paused":
            self._btn_pause_resume.set_label("Resume")
            self._btn_pause_resume.set_visible(True)
        else:
            self._btn_pause_resume.set_visible(False)

        # Cancel — visible only while active
        self._btn_cancel.set_visible(
            status_key in ("downloading", "fetching_info", "paused", "queued")
        )

        # Open File — only when completed
        self._btn_open.set_visible(status_key == "completed")

        # Remove — always visible; disabled while actively downloading
        self._btn_remove.set_visible(True)
        self._btn_remove.set_sensitive(
            status_key not in ("downloading", "fetching_info")
        )

        # Thumbnail — load asynchronously if the URL changed
        thumb_url = getattr(item, "thumbnail_url", None)
        if thumb_url and thumb_url != self._thumb_url_loaded:
            self._thumb_url_loaded = thumb_url
            threading.Thread(
                target=self._fetch_thumbnail,
                args=(thumb_url,),
                daemon=True,
            ).start()

    # ------------------------------------------------------------------
    # Thumbnail loading
    # ------------------------------------------------------------------

    def _fetch_thumbnail(self, url: str) -> None:
        """Download thumbnail bytes in a background thread, then update UI."""
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "YDM/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read()

            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
            if pixbuf:
                # Scale to fit the thumbnail slot
                scaled = pixbuf.scale_simple(
                    _THUMB_W, _THUMB_H, GdkPixbuf.InterpType.BILINEAR
                )
                GLib.idle_add(self._apply_thumbnail, scaled)
        except Exception as exc:
            logger.debug("Thumbnail fetch failed for '%s': %s", url, exc)

    def _apply_thumbnail(self, pixbuf: GdkPixbuf.Pixbuf) -> bool:
        """Called on the main thread to set the thumbnail pixbuf."""
        try:
            self._thumb_image.set_from_pixbuf(pixbuf)
        except Exception as exc:
            logger.debug("apply_thumbnail failed: %s", exc)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------
    # Button callbacks
    # ------------------------------------------------------------------

    def _on_pause_resume(self, _btn: Gtk.Button) -> None:
        status_key = (
            self._item.status.value
            if hasattr(self._item.status, "value")
            else str(self._item.status)
        )
        try:
            if status_key in ("downloading", "fetching_info"):
                self._qm.pause(self._item.id)
            elif status_key == "paused":
                self._qm.resume(self._item.id)
        except Exception as exc:
            logger.warning("pause/resume failed: %s", exc)

    def _on_cancel(self, _btn: Gtk.Button) -> None:
        try:
            self._qm.cancel(self._item.id)
        except Exception as exc:
            logger.warning("cancel failed: %s", exc)

    def _on_open_file(self, _btn: Gtk.Button) -> None:
        try:
            filepath = os.path.join(
                self._item.save_path or "",
                self._item.filename or "",
            )
            Gio.AppInfo.launch_default_for_uri(f"file://{filepath}", None)
        except Exception as exc:
            logger.warning("open_file failed: %s", exc)

    def _on_remove(self, _btn: Gtk.Button) -> None:
        try:
            self._qm.remove(self._item.id)
        except Exception as exc:
            logger.warning("remove failed: %s", exc)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def item_id(self) -> str:
        return self._item.id
