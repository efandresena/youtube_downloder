"""
notifications.py — libnotify wrapper for YDM.

Sends desktop notifications for download lifecycle events.
All methods are wrapped in try/except so that a missing libnotify
installation never crashes the application.
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional imports — gracefully degrade when the library is absent
# ---------------------------------------------------------------------------
try:
    import gi

    gi.require_version("Notify", "0.7")
    from gi.repository import Gio, Notify  # noqa: F401

    _NOTIFY_AVAILABLE = True
except Exception:
    _NOTIFY_AVAILABLE = False
    logger.warning("libnotify not available — desktop notifications disabled.")


class Notifications:
    """Thin wrapper around libnotify for YDM download events."""

    def __init__(self) -> None:
        self._available = False
        if _NOTIFY_AVAILABLE:
            try:
                Notify.init("YDM")
                self._available = True
                logger.info("Notifications initialised.")
            except Exception as exc:
                logger.warning("Notify.init failed: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_download_started(self, title: str) -> None:
        """Fire a notification when a download begins."""
        self._send(
            summary="Download Started",
            body=f"Downloading: {title}",
            icon="video-display-symbolic",
        )

    def notify_download_complete(self, title: str, filepath: str) -> None:
        """Fire a notification when a download finishes successfully."""
        if not self._available:
            return
        try:
            notification = Notify.Notification.new(
                "Download Complete",
                title,
                "emblem-ok-symbolic",
            )

            # Add an "Open File" action
            def _open_file(notif, action_name, _user_data):  # noqa: ANN001
                try:
                    uri = f"file://{filepath}"
                    Gio.AppInfo.launch_default_for_uri(uri, None)
                except Exception as exc:
                    logger.warning("Could not open file '%s': %s", filepath, exc)

            notification.add_action(
                "open-file",
                "Open File",
                _open_file,
                None,
            )
            notification.show()
        except Exception as exc:
            logger.warning("notify_download_complete failed: %s", exc)

    def notify_download_failed(self, title: str, reason: str) -> None:
        """Fire a notification when a download fails."""
        self._send(
            summary="Download Failed",
            body=f"{title}: {reason}",
            icon="dialog-error-symbolic",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send(self, summary: str, body: str, icon: str) -> None:
        """Create and show a simple notification (no actions)."""
        if not self._available:
            return
        try:
            notification = Notify.Notification.new(summary, body, icon)
            notification.show()
        except Exception as exc:
            logger.warning("Notification '%s' failed: %s", summary, exc)
