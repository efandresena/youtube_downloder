"""
YDM Local HTTP Server

Exposes a minimal REST-like API on localhost:50123 so that:
  • The browser extension (via native_host.py) can submit download requests.
  • The GTK GUI can query queue status via HTTP if needed.
  • Any other local client can integrate with YDM.

The server runs in its own daemon thread using asyncio + aiohttp, keeping the
GTK main loop completely independent.

CORS:
  All origins are allowed so the browser extension popup can call the API
  directly (the native host already proxies requests, but this also works
  in development / testing).

Endpoint summary:
  GET  /ping                → { "status": "ok", "version": "1.0.0" }
  POST /get_formats         → { "url": "..." } → { "formats": [...] }
  POST /download            → { "url", "format_id", "title", "save_path"? }
                              → { "status": "queued", "id": "..." }
  GET  /status              → { "active", "queued", "total_speed", "downloads" }
  DELETE /download/{id}     → { "status": "cancelled" }

Error responses always have the shape: { "error": "message" }

NOTE FOR INTEGRATORS:
  The asyncio event loop lives in a background thread.  All yt-dlp / DB work
  is synchronous; we run it in the thread-pool executor so as not to block the
  aiohttp event loop.
"""

import asyncio
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from aiohttp import web

from .database import Database
from .downloader import Downloader
from .models import DownloadItem, DownloadStatus
from .queue_manager import QueueManager

logger = logging.getLogger(__name__)

_PORT = 50123
_HOST = "127.0.0.1"
_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# CORS helper
# ---------------------------------------------------------------------------

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}


def _cors(response: web.Response) -> web.Response:
    """Attach CORS headers to *response* and return it."""
    response.headers.update(_CORS_HEADERS)
    return response


def _json_response(data: Any, status: int = 200) -> web.Response:
    """Create a JSON response with CORS headers."""
    return _cors(
        web.Response(
            text=json.dumps(data, ensure_ascii=False),
            status=status,
            content_type="application/json",
        )
    )


def _error(message: str, status: int = 400) -> web.Response:
    """Convenience helper for error responses."""
    return _json_response({"error": message}, status=status)


# ---------------------------------------------------------------------------
# YDMServer
# ---------------------------------------------------------------------------


class YDMServer:
    """
    aiohttp-based local HTTP server for YDM.

    The server runs its own asyncio event loop in a background daemon thread,
    so it does not conflict with the GTK main loop.

    Usage::

        server = YDMServer(queue_manager, database, downloader)
        server.start_in_background()   # call from GTK app startup
        # ... later:
        server.stop()
    """

    def __init__(
        self,
        queue_manager: QueueManager,
        database: Database,
        downloader: Downloader,
    ) -> None:
        self._qm: QueueManager = queue_manager
        self._db: Database = database
        self._dl: Downloader = downloader

        self._app: web.Application = web.Application()
        self._runner: web.AppRunner | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="ydm-server-worker"
        )

        self._register_routes()

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------

    def _register_routes(self) -> None:
        router = self._app.router
        router.add_get("/ping", self._handle_ping)
        router.add_post("/get_formats", self._handle_get_formats)
        router.add_post("/download", self._handle_download)
        router.add_get("/status", self._handle_status)
        router.add_delete("/download/{id}", self._handle_delete_download)
        # Handle pre-flight CORS OPTIONS for all routes
        router.add_route("OPTIONS", "/{path_info:.*}", self._handle_options)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_options(self, request: web.Request) -> web.Response:
        """Respond to CORS pre-flight requests."""
        return _cors(web.Response(status=204))

    async def _handle_ping(self, request: web.Request) -> web.Response:
        """
        GET /ping
        Health-check endpoint used by the browser extension to verify
        the YDM app is running before attempting a download.
        """
        return _json_response({"status": "ok", "version": _VERSION})

    async def _handle_get_formats(self, request: web.Request) -> web.Response:
        """
        POST /get_formats
        Body: { "url": "https://youtu.be/..." }
        Returns: { "formats": [ <VideoFormat.to_dict()>, ... ] }

        Format extraction is CPU-bound (yt-dlp network call); we run it in
        the thread-pool executor to avoid blocking the event loop.
        """
        try:
            body = await request.json()
        except Exception:
            return _error("Invalid JSON body", 400)

        url = (body.get("url") or "").strip()
        if not url:
            return _error("Missing 'url' field", 400)

        loop = asyncio.get_running_loop()
        try:
            formats = await loop.run_in_executor(
                self._executor, self._dl.get_video_formats, url
            )
        except Exception as exc:
            logger.exception("get_formats failed for %s", url)
            return _error(f"Failed to fetch formats: {exc}", 502)

        return _json_response({"formats": [f.to_dict() for f in formats]})

    async def _handle_download(self, request: web.Request) -> web.Response:
        """
        POST /download
        Body: {
            "url":       "https://youtu.be/...",
            "format_id": "bestvideo+bestaudio/best",   (optional, default "best")
            "title":     "My Video Title",             (optional)
            "save_path": "/home/user/Videos"           (optional, falls back to DB default)
        }
        Returns: { "status": "queued", "id": "<uuid>" }
        """
        try:
            body = await request.json()
        except Exception:
            return _error("Invalid JSON body", 400)

        url = (body.get("url") or "").strip()
        if not url:
            return _error("Missing 'url' field", 400)

        format_id = (body.get("format_id") or "bestvideo+bestaudio/best").strip()
        title = (body.get("title") or "").strip()
        save_path = (body.get("save_path") or "").strip()

        if not save_path:
            save_path = self._db.get_default_save_path()

        item = DownloadItem(
            url=url,
            title=title,
            format_id=format_id,
            save_path=save_path,
            status=DownloadStatus.QUEUED,
        )

        # If title is missing, try a quick metadata fetch in the executor.
        # We don't block the queue on this — the downloader will fill it in.
        if not title:
            loop = asyncio.get_running_loop()
            try:
                title_info = await loop.run_in_executor(
                    self._executor, self._fetch_title, url
                )
                if title_info:
                    item.title = title_info.get("title", "")
                    item.thumbnail_url = title_info.get("thumbnail", "")
            except Exception as exc:
                logger.warning("Could not prefetch title for %s: %s", url, exc)

        # Queue the download (this is thread-safe)
        self._qm.add(item)
        logger.info("Enqueued via HTTP: %s (id=%s)", url, item.id)

        return _json_response({"status": "queued", "id": item.id}, status=202)

    async def _handle_status(self, request: web.Request) -> web.Response:
        """
        GET /status
        Returns a snapshot of the current queue state:
        {
            "active":      <int>,
            "queued":      <int>,
            "total_speed": <float bytes/sec>,
            "downloads":   [ <DownloadItem.to_dict()>, ... ]
        }
        """
        items = self._qm.get_all()
        queued_count = sum(1 for it in items if it.status == DownloadStatus.QUEUED)

        return _json_response(
            {
                "active": self._qm.get_active_count(),
                "queued": queued_count,
                "total_speed": self._qm.get_total_speed(),
                "downloads": [it.to_dict() for it in reversed(items)],
            }
        )

    async def _handle_delete_download(self, request: web.Request) -> web.Response:
        """
        DELETE /download/{id}
        Cancels the download if active, then removes the record from the queue
        and the database.
        Returns: { "status": "cancelled" }  or  { "error": "..." }
        """
        item_id = request.match_info.get("id", "").strip()
        if not item_id:
            return _error("Missing download id", 400)

        # Cancel (no-op if already terminal)
        self._qm.cancel(item_id)
        # Remove from in-memory queue (ignore failure — might be terminal already)
        self._qm.remove(item_id)
        # Hard-delete from DB so it disappears from history
        self._db.delete_download(item_id)

        logger.info("Deleted download %s via HTTP", item_id)
        return _json_response({"status": "cancelled"})

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_title(self, url: str) -> dict[str, Any]:
        """
        Quickly extract just the title and thumbnail from yt-dlp (no download).
        Returns a dict with 'title' and 'thumbnail' keys.
        """
        import yt_dlp

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return {
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Start the aiohttp application and begin listening on localhost:50123.
        This coroutine runs inside the server's private event loop.
        """
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, _HOST, _PORT)
        await site.start()
        logger.info("YDM server listening on http://%s:%d", _HOST, _PORT)

    async def stop(self) -> None:
        """Gracefully shut down the aiohttp server."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            logger.info("YDM server stopped")

    # ------------------------------------------------------------------
    # Thread-based entry points (used by the GTK app)
    # ------------------------------------------------------------------

    def start_in_background(self) -> None:
        """
        Start the HTTP server in a background daemon thread.

        Creates a new asyncio event loop dedicated to the server so that it
        does not interfere with the GTK / GLib main loop.  Call this once
        during application startup.
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Server is already running")
            return

        def _run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self.start())
                self._loop.run_forever()
            except Exception:
                logger.exception("Server event loop exited unexpectedly")
            finally:
                self._loop.run_until_complete(self.stop())
                self._loop.close()

        self._thread = threading.Thread(
            target=_run,
            name="ydm-http-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("Server background thread started")

    def stop_background(self) -> None:
        """
        Signal the server's background event loop to stop.

        Safe to call from the GTK main thread during application shutdown.
        """
        if self._loop is not None and self._loop.is_running():
            # Schedule stop() on the server's loop, then stop the loop itself.
            async def _shutdown():
                await self.stop()
                self._loop.stop()

            asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
            logger.info("Server shutdown requested")

        self._executor.shutdown(wait=False)
