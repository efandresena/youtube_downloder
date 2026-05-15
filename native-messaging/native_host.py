#!/usr/bin/env python3
"""
YDM Native Messaging Host
=========================
Bridges the browser extension to the YDM desktop application.

Protocol (Native Messaging binary framing):
  READ  : 4-byte little-endian uint32 length prefix, then that many UTF-8 bytes (JSON).
  WRITE : 4-byte little-endian uint32 length prefix, then that many UTF-8 bytes (JSON).

stdin/stdout are used in raw binary mode; logging goes to a file only.

Actions handled:
  ping        → { "status": "ok" }
  get_formats → HTTP POST localhost:50123/get_formats → forward response
  download    → HTTP POST localhost:50123/download    → forward response

If the YDM server is unreachable: { "error": "YDM app is not running. Please start YDM first." }
"""

import json
import logging
import os
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ── Logging Setup ─────────────────────────────────────────────────────────────

LOG_DIR = Path.home() / ".local" / "share" / "ydm"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "native_host.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ydm.native_host")

# ── Constants ─────────────────────────────────────────────────────────────────

YDM_BASE_URL = "http://localhost:50123"
REQUEST_TIMEOUT = 30  # seconds

# ── Binary I/O Helpers ────────────────────────────────────────────────────────


def read_message() -> dict:
    """
    Read one Native Messaging message from stdin.

    Format:
        [4 bytes, little-endian uint32 = N] [N bytes UTF-8 JSON]

    Returns the parsed JSON dict.
    Raises EOFError when stdin is closed (browser disconnected).
    """
    raw_len = sys.stdin.buffer.read(4)
    if len(raw_len) == 0:
        raise EOFError("stdin closed — browser disconnected.")
    if len(raw_len) < 4:
        raise ValueError(f"Incomplete length prefix: got {len(raw_len)} bytes.")

    msg_len = struct.unpack("<I", raw_len)[0]
    log.debug("Reading message of %d bytes.", msg_len)

    raw_msg = sys.stdin.buffer.read(msg_len)
    if len(raw_msg) < msg_len:
        raise ValueError(
            f"Incomplete message body: expected {msg_len}, got {len(raw_msg)} bytes."
        )

    decoded = raw_msg.decode("utf-8")
    log.debug("Received: %s", decoded)
    return json.loads(decoded)


def send_message(payload: dict) -> None:
    """
    Write one Native Messaging message to stdout.

    Format:
        [4 bytes, little-endian uint32 = N] [N bytes UTF-8 JSON]
    """
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    length_prefix = struct.pack("<I", len(encoded))

    log.debug("Sending %d bytes: %s", len(encoded), encoded.decode("utf-8"))

    sys.stdout.buffer.write(length_prefix)
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


# ── HTTP Helper ───────────────────────────────────────────────────────────────


def http_post(path: str, body: dict) -> dict:
    """
    Perform an HTTP POST to the YDM local server.

    :param path:  URL path, e.g. "/download" or "/get_formats"
    :param body:  Dict to JSON-encode as the request body
    :returns:     Parsed JSON response dict
    :raises:      urllib.error.URLError / urllib.error.HTTPError on failure
    """
    url = YDM_BASE_URL + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8"))


def http_get(path: str) -> dict:
    """
    Perform an HTTP GET to the YDM local server.

    :param path:  URL path, e.g. "/ping"
    :returns:     Parsed JSON response dict
    """
    url = YDM_BASE_URL + path
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8"))


# ── Action Handlers ───────────────────────────────────────────────────────────


def handle_ping() -> dict:
    """Health check — confirm the native host itself is alive."""
    try:
        result = http_get("/ping")
        return result  # expects { "status": "ok" } from server
    except (urllib.error.URLError, OSError) as exc:
        log.warning("Ping failed — YDM server not running: %s", exc)
        return {"error": "YDM app is not running. Please start YDM first."}


def handle_get_formats(url: str) -> dict:
    """Fetch available download formats for a YouTube URL."""
    if not url:
        return {"error": "No URL provided."}
    try:
        log.info("get_formats: %s", url)
        result = http_post("/get_formats", {"url": url})
        return result
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        log.error("get_formats HTTP error %s: %s", exc.code, body)
        try:
            return json.loads(body)
        except Exception:
            return {"error": f"YDM server returned HTTP {exc.code}: {body}"}
    except (urllib.error.URLError, OSError) as exc:
        log.error("get_formats connection error: %s", exc)
        return {"error": "YDM app is not running. Please start YDM first."}
    except Exception as exc:
        log.exception("get_formats unexpected error: %s", exc)
        return {"error": f"Unexpected error: {exc}"}


def handle_download(url: str, format_id: str, title: str, save_path: str = "") -> dict:
    """Send a download request to the YDM app server."""
    if not url:
        return {"error": "No URL provided."}
    if not format_id:
        return {"error": "No format_id provided."}

    body = {
        "url": url,
        "format_id": format_id,
        "title": title or "",
        "save_path": save_path or "",
    }

    try:
        log.info("download: url=%s format_id=%s title=%s", url, format_id, title)
        result = http_post("/download", body)
        return result
    except urllib.error.HTTPError as exc:
        body_raw = exc.read().decode("utf-8", errors="replace")
        log.error("download HTTP error %s: %s", exc.code, body_raw)
        try:
            return json.loads(body_raw)
        except Exception:
            return {"error": f"YDM server returned HTTP {exc.code}: {body_raw}"}
    except (urllib.error.URLError, OSError) as exc:
        log.error("download connection error: %s", exc)
        return {"error": "YDM app is not running. Please start YDM first."}
    except Exception as exc:
        log.exception("download unexpected error: %s", exc)
        return {"error": f"Unexpected error: {exc}"}


# ── Message Dispatcher ────────────────────────────────────────────────────────


def dispatch(message: dict) -> dict:
    """Route an incoming message to the appropriate handler."""
    action = message.get("action", "")
    log.debug("Dispatch action=%r", action)

    if action == "ping":
        return handle_ping()

    if action == "get_formats":
        return handle_get_formats(message.get("url", ""))

    if action == "download":
        return handle_download(
            url=message.get("url", ""),
            format_id=message.get("format_id", ""),
            title=message.get("title", ""),
            save_path=message.get("save_path", ""),
        )

    log.warning("Unknown action: %r", action)
    return {"error": f"Unknown action: {action!r}"}


# ── Main Loop ─────────────────────────────────────────────────────────────────


def main() -> None:
    log.info("YDM Native Messaging Host started. PID=%d", os.getpid())

    while True:
        try:
            message = read_message()
        except EOFError:
            log.info("Browser disconnected — exiting.")
            break
        except Exception as exc:
            log.error("Error reading message: %s", exc)
            # Try to send an error back, then continue
            try:
                send_message({"error": f"Failed to read message: {exc}"})
            except Exception:
                pass
            break

        try:
            response = dispatch(message)
        except Exception as exc:
            log.exception("Unhandled error in dispatch: %s", exc)
            response = {"error": f"Internal native host error: {exc}"}

        try:
            send_message(response)
        except Exception as exc:
            log.error("Failed to send response: %s", exc)
            break

    log.info("YDM Native Messaging Host exiting.")


if __name__ == "__main__":
    main()
