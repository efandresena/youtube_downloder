"""
add_dialog.py — "Add Download" dialog for YDM.

Two-step flow:
  Step 1 — URL entry (pre-fills from clipboard if it looks like YouTube)
  Step 2 — Format selection (video thumbnail, title, quality dropdown, path)

All network calls are run in background threads; UI updates come back via
GLib.idle_add() so the main thread is never blocked.
"""

from __future__ import annotations

import logging
import os
import threading
import urllib.request
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

if TYPE_CHECKING:
    from ydm.core.downloader import Downloader
    from ydm.core.models import DownloadItem, VideoFormat
    from ydm.core.queue_manager import QueueManager

logger = logging.getLogger(__name__)

_THUMB_W = 320
_THUMB_H = 180


class AddDialog(Adw.Dialog):
    """
    Modal dialog that lets the user paste a YouTube URL and start a download.

    It is deliberately a two-step process:
      1. Enter URL → "Fetch Formats" button
      2. Choose format & save path → "Download" button
    """

    def __init__(
        self,
        parent: Gtk.Window,
        queue_manager: "QueueManager",
        downloader: "Downloader",
        database,
    ) -> None:
        super().__init__()
        self._parent = parent
        self._qm = queue_manager
        self._downloader = downloader
        self._database = database
        self._formats: list["VideoFormat"] = []

        self.set_title("Add Download")
        self.set_content_width(480)
        self.set_content_height(520)

        self._build_ui()
        self._try_prefill_clipboard()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Build the entire dialog content."""
        # ToolbarView wraps header + scrollable content (Adw pattern)
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        # Cancel button (left)
        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(btn_cancel)

        # Download button (right) — only active in step 2
        self._btn_download = Gtk.Button(label="Download")
        self._btn_download.add_css_class("suggested-action")
        self._btn_download.set_sensitive(False)
        self._btn_download.connect("clicked", self._on_download)
        header.pack_end(self._btn_download)

        # ── Scrollable content ─────────────────────────────────────────
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        toolbar_view.set_content(scroll)

        self._content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self._content_box.set_margin_top(16)
        self._content_box.set_margin_bottom(16)
        self._content_box.set_margin_start(16)
        self._content_box.set_margin_end(16)
        scroll.set_child(self._content_box)

        # ── Step 1: URL entry group ────────────────────────────────────
        url_group = Adw.PreferencesGroup(title="YouTube URL")
        self._content_box.append(url_group)

        self._url_row = Adw.EntryRow(title="Paste URL here…")
        self._url_row.connect("entry-activated", lambda _r: self._on_fetch())
        url_group.add(self._url_row)

        self._btn_fetch = Gtk.Button(label="Fetch Formats")
        self._btn_fetch.add_css_class("suggested-action")
        self._btn_fetch.set_halign(Gtk.Align.CENTER)
        self._btn_fetch.set_margin_top(8)
        self._btn_fetch.connect("clicked", lambda _b: self._on_fetch())
        self._content_box.append(self._btn_fetch)

        # Spinner (hidden by default)
        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        self._content_box.append(self._spinner)

        # Error label (hidden by default)
        self._error_label = Gtk.Label(label="")
        self._error_label.add_css_class("error")
        self._error_label.set_wrap(True)
        self._error_label.set_visible(False)
        self._content_box.append(self._error_label)

        # ── Step 2: format selection (hidden until fetch succeeds) ─────
        self._step2_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._step2_box.set_visible(False)
        self._content_box.append(self._step2_box)

        # Thumbnail
        self._thumb_image = Gtk.Image()
        self._thumb_image.set_size_request(_THUMB_W, _THUMB_H)
        self._thumb_image.set_from_icon_name("video-display-symbolic")
        self._thumb_image.set_halign(Gtk.Align.CENTER)
        self._step2_box.append(self._thumb_image)

        # Video title
        self._video_title_label = Gtk.Label(label="")
        self._video_title_label.add_css_class("title-2")
        self._video_title_label.set_wrap(True)
        self._video_title_label.set_halign(Gtk.Align.CENTER)
        self._step2_box.append(self._video_title_label)

        # Format dropdown group
        fmt_group = Adw.PreferencesGroup(title="Quality")
        self._step2_box.append(fmt_group)

        fmt_row = Adw.ActionRow(title="Format")
        fmt_group.add(fmt_row)

        self._format_dropdown = Gtk.DropDown()
        self._format_dropdown.set_valign(Gtk.Align.CENTER)
        fmt_row.add_suffix(self._format_dropdown)
        fmt_row.set_activatable_widget(self._format_dropdown)

        # Save path group
        path_group = Adw.PreferencesGroup(title="Save Location")
        self._step2_box.append(path_group)

        self._path_row = Adw.ActionRow(title="Folder")
        path_group.add(self._path_row)

        self._path_label = Gtk.Label(label="")
        self._path_label.set_ellipsize(3)  # END
        self._path_label.set_valign(Gtk.Align.CENTER)
        self._path_row.add_suffix(self._path_label)

        btn_browse = Gtk.Button(label="Browse…")
        btn_browse.set_valign(Gtk.Align.CENTER)
        btn_browse.connect("clicked", self._on_browse_path)
        self._path_row.add_suffix(btn_browse)

        # Set default path
        try:
            default_path = self._database.get_default_save_path()
        except Exception:
            default_path = os.path.expanduser("~/Downloads")
        self._save_path = default_path
        self._path_label.set_text(default_path)

    # ------------------------------------------------------------------
    # Clipboard pre-fill
    # ------------------------------------------------------------------

    def _try_prefill_clipboard(self) -> None:
        """Read the clipboard; if it looks like a YouTube URL, pre-fill the entry."""
        try:
            display = Gdk.Display.get_default()
            if display is None:
                return
            clipboard = display.get_clipboard()
            clipboard.read_text_async(None, self._on_clipboard_text, None)
        except Exception as exc:
            logger.debug("Clipboard read failed: %s", exc)

    def _on_clipboard_text(self, clipboard, result, _user_data) -> None:
        try:
            text = clipboard.read_text_finish(result)
            if text and ("youtube.com/watch" in text or "youtu.be/" in text):
                self._url_row.set_text(text.strip())
        except Exception as exc:
            logger.debug("Clipboard text callback failed: %s", exc)

    # ------------------------------------------------------------------
    # Step 1 — Fetch formats
    # ------------------------------------------------------------------

    def _on_fetch(self) -> None:
        """Start fetching video formats in a background thread."""
        url = self._url_row.get_text().strip()
        if not url:
            self._show_error("Please enter a YouTube URL.")
            return

        self._error_label.set_visible(False)
        self._step2_box.set_visible(False)
        self._btn_fetch.set_sensitive(False)
        self._btn_download.set_sensitive(False)
        self._spinner.set_visible(True)
        self._spinner.start()

        threading.Thread(
            target=self._fetch_formats_thread,
            args=(url,),
            daemon=True,
        ).start()

    def _fetch_formats_thread(self, url: str) -> None:
        """Background thread: call downloader and return to main thread."""
        try:
            formats = self._downloader.get_video_formats(url)
            GLib.idle_add(self._on_formats_ready, url, formats)
        except Exception as exc:
            GLib.idle_add(self._on_fetch_error, str(exc))

    def _on_formats_ready(self, url: str, formats: list["VideoFormat"]) -> bool:
        """Called on the main thread once formats are fetched."""
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._btn_fetch.set_sensitive(True)

        if not formats:
            self._show_error("No downloadable formats found for this URL.")
            return GLib.SOURCE_REMOVE

        self._formats = formats
        self._current_url = url

        # Populate dropdown
        fmt_labels = [
            getattr(f, "label", None)
            or f"{getattr(f, 'ext', 'mp4')} {getattr(f, 'resolution', '')}".strip()
            for f in formats
        ]
        string_list = Gtk.StringList.new(fmt_labels)
        self._format_dropdown.set_model(string_list)
        self._format_dropdown.set_selected(0)

        # VideoFormat does not carry title/thumbnail — extract video ID from URL
        # and build a thumbnail URL from it. Title will be fetched separately.
        title = ""
        thumb_url = ""
        try:
            from urllib.parse import parse_qs, urlparse  # noqa: PLC0415

            qs = parse_qs(urlparse(url).query)
            video_id = qs.get("v", [""])[0]
            if video_id:
                thumb_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        except Exception:
            pass

        self._video_title_label.set_text(title or url)
        self._step2_box.set_visible(True)
        self._btn_download.set_sensitive(True)

        if thumb_url:
            threading.Thread(
                target=self._fetch_thumbnail,
                args=(thumb_url,),
                daemon=True,
            ).start()

        return GLib.SOURCE_REMOVE

    def _on_fetch_error(self, message: str) -> bool:
        """Called on the main thread when format fetching fails."""
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._btn_fetch.set_sensitive(True)
        self._show_error(f"Could not fetch formats: {message}")
        return GLib.SOURCE_REMOVE

    def _show_error(self, message: str) -> None:
        self._error_label.set_text(message)
        self._error_label.set_visible(True)

    # ------------------------------------------------------------------
    # Thumbnail
    # ------------------------------------------------------------------

    def _fetch_thumbnail(self, url: str) -> None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "YDM/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
            if pixbuf:
                scaled = pixbuf.scale_simple(
                    _THUMB_W, _THUMB_H, GdkPixbuf.InterpType.BILINEAR
                )
                GLib.idle_add(self._apply_thumbnail, scaled)
        except Exception as exc:
            logger.debug("Thumbnail fetch failed: %s", exc)

    def _apply_thumbnail(self, pixbuf: GdkPixbuf.Pixbuf) -> bool:
        self._thumb_image.set_from_pixbuf(pixbuf)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------
    # Step 2 — Browse path
    # ------------------------------------------------------------------

    def _on_browse_path(self, _btn: Gtk.Button) -> None:
        """Open a native folder-chooser dialog."""
        chooser = Gtk.FileDialog()
        chooser.set_title("Choose Save Folder")
        chooser.set_initial_folder(Gio_file_for_path(self._save_path))
        chooser.select_folder(self._parent, None, self._on_folder_chosen, None)

    def _on_folder_chosen(self, dialog, result, _user_data) -> None:
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                self._save_path = path
                self._path_label.set_text(path)
        except Exception as exc:
            logger.debug("Folder chooser cancelled or failed: %s", exc)

    # ------------------------------------------------------------------
    # Step 2 — Start download
    # ------------------------------------------------------------------

    def _on_download(self, _btn: Gtk.Button) -> None:
        """Create a DownloadItem and hand it to the QueueManager."""
        try:
            from ydm.core.models import DownloadItem, DownloadStatus  # noqa: PLC0415

            idx = self._format_dropdown.get_selected()
            chosen_fmt = self._formats[idx] if self._formats else None
            fmt_id = (
                getattr(chosen_fmt, "format_id", "bestvideo+bestaudio/best")
                if chosen_fmt
                else "bestvideo+bestaudio/best"
            )
            title = getattr(chosen_fmt, "title", "") if chosen_fmt else ""
            thumb_url = getattr(chosen_fmt, "thumbnail_url", "") if chosen_fmt else ""

            item = DownloadItem(
                id=str(uuid.uuid4()),
                url=self._current_url,
                title=title or self._current_url,
                thumbnail_url=thumb_url,
                format_id=fmt_id,
                status=DownloadStatus.QUEUED,
                progress=0.0,
                speed=0.0,
                eta=0,
                filesize=0,
                downloaded_bytes=0,
                save_path=self._save_path,
                filename="",
                created_at=datetime.now(),
                completed_at=None,
                error_message="",
            )
            self._qm.add(item)
            self.close()
        except Exception as exc:
            logger.exception("Failed to start download: %s", exc)
            self._show_error(f"Error: {exc}")


# ---------------------------------------------------------------------------
# Small helper — not importing Gio at module level to keep the try/except clean
# ---------------------------------------------------------------------------


def Gio_file_for_path(path: str):  # noqa: N802
    """Return a Gio.File for *path*, or None on failure."""
    try:
        from gi.repository import Gio  # noqa: PLC0415

        return Gio.File.new_for_path(path)
    except Exception:
        return None
