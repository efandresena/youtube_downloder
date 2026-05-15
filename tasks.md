# YDM - YouTube Download Manager
### A Linux-Native IDM Alternative with Browser Integration

---

## Overview

YDM is a feature-rich YouTube download manager for Linux (GNOME-based desktop environments).
It mimics the core behaviour of Internet Download Manager (IDM) for Windows:
one-click downloads directly from the browser, a persistent queue, pause/resume,
speed display, format selection, and a clean native GUI.

**Focus:** YouTube only (for now). Extendable to other sites later.

---

## Core Features (IDM Parity)

| Feature | Status |
|---|---|
| One-click download from Firefox & Brave | Planned |
| Auto-detect YouTube video on active tab | Planned |
| Format & quality selector (video + audio) | Planned |
| Download queue (multiple simultaneous) | Planned |
| Pause / Resume / Cancel | Planned |
| Real-time progress (%, speed, ETA, size) | Planned |
| Download history & search | Planned |
| GNOME desktop notifications | Planned |
| System tray icon | Planned |
| Clipboard monitoring (auto-detect YT links) | Planned |
| Auto-retry on failure | Planned |
| Dark / Light theme (follows GNOME setting) | Planned |
| Save location picker | Planned |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| GUI Framework | GTK4 + libadwaita (PyGObject) |
| Download Engine | yt-dlp |
| Database | SQLite (via sqlite3 stdlib) |
| Browser Extension (Firefox) | WebExtension API - Manifest V2 |
| Browser Extension (Brave/Chrome) | WebExtension API - Manifest V3 |
| Browser ↔ App Bridge | Native Messaging API (stdin/stdout JSON) |
| App Internal IPC | Local HTTP server (aiohttp on localhost:50123) |
| Notifications | libnotify (gi.repository.Notify) |
| System Tray | AppIndicator3 (gi.repository.AppIndicator3) |
| Packaging | Manual install script (.sh) for now |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  BROWSER (Firefox / Brave)           │
│                                                     │
│  ┌─────────────┐    ┌──────────────────────────┐   │
│  │ Content     │───▶│  Background Script       │   │
│  │ Script      │    │  (detects YT video page) │   │
│  │ (YouTube    │    │                          │   │
│  │  page)      │    │  Popup UI                │   │
│  └─────────────┘    └──────────┬───────────────┘   │
│                                │ Native Messaging   │
└────────────────────────────────┼───────────────────┘
                                 │
                    ┌────────────▼───────────┐
                    │  Native Messaging Host  │
                    │  (native_host.py)       │
                    │  stdin/stdout JSON      │
                    └────────────┬────────────┘
                                 │ HTTP POST
                                 │ localhost:50123
                    ┌────────────▼────────────┐
                    │   YDM Core App Server   │
                    │   (aiohttp server)      │
                    └────────┬────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
    ┌─────────▼──┐  ┌────────▼────┐  ┌─────▼──────┐
    │  Download  │  │    Queue    │  │  SQLite DB  │
    │  Engine    │  │  Manager   │  │  (history)  │
    │  (yt-dlp)  │  │            │  │             │
    └─────────┬──┘  └────────────┘  └─────────────┘
              │
    ┌─────────▼──────────────────────────────────┐
    │            GTK4 + libadwaita GUI            │
    │                                            │
    │  Main Window │ Download Rows │ Settings    │
    │  System Tray │ Notifications │ Add Dialog  │
    └─────────────────────────────────────────────┘
```

---

## How Browser Integration Works (Native Messaging)

1. User visits a YouTube video page in Firefox or Brave.
2. The **content script** detects it's a YouTube video URL and notifies the **background script**.
3. The browser extension **toolbar icon** becomes active (badge). User clicks it.
4. A **popup** appears with video title, thumbnail, and quality options.
5. User hits **"Download"** in the popup.
6. Background script sends a JSON message via **Native Messaging API** to `native_host.py`.
7. `native_host.py` reads the JSON from `stdin` (4-byte length-prefixed), then does an HTTP POST to `localhost:50123/download`.
8. The **YDM core server** (running inside the GTK app) receives the request, validates the URL, and adds it to the download queue.
9. The **GTK4 GUI** updates in real-time via `GLib.idle_add` signals.
10. A **GNOME notification** is fired: "Download started: [video title]".

---

## Project Directory Structure

```
youtube_downloder/
├── tasks.md                        # This file
├── README.md
├── requirements.txt
├── install.sh                      # Installs app + browser extension + native host
│
├── ydm/                            # Main Python package
│   ├── __init__.py
│   ├── main.py                     # Entry point (starts GTK app + aiohttp server)
│   │
│   ├── core/                       # Business logic (no GUI dependencies)
│   │   ├── __init__.py
│   │   ├── models.py               # DownloadItem dataclass, enums (Status, Quality)
│   │   ├── database.py             # SQLite CRUD - download history, settings
│   │   ├── downloader.py           # yt-dlp wrapper, format fetching, download execution
│   │   ├── queue_manager.py        # Queue: add, pause, resume, cancel, prioritize
│   │   └── server.py               # aiohttp local server (receives browser requests)
│   │
│   └── gui/                        # GTK4 + libadwaita UI
│       ├── __init__.py
│       ├── app.py                   # Adw.Application subclass
│       ├── main_window.py          # Main Adw.ApplicationWindow
│       ├── download_row.py         # Gtk.ListBoxRow for each download item
│       ├── add_dialog.py           # "Add Download" dialog (URL + format picker)
│       ├── settings_dialog.py      # Preferences (save path, max concurrent, theme)
│       ├── tray.py                 # AppIndicator3 system tray icon + menu
│       └── notifications.py        # libnotify wrapper
│
├── browser-extension/
│   ├── firefox/                    # Manifest V2 (Firefox)
│   │   ├── manifest.json
│   │   ├── background.js
│   │   ├── content.js
│   │   ├── icons/
│   │   │   ├── icon-16.png
│   │   │   ├── icon-48.png
│   │   │   └── icon-128.png
│   │   └── popup/
│   │       ├── popup.html
│   │       ├── popup.js
│   │       └── popup.css
│   │
│   └── chrome/                     # Manifest V3 (Brave / Chromium)
│       ├── manifest.json
│       ├── background.js           # Service Worker (MV3)
│       ├── content.js
│       ├── icons/
│       │   ├── icon-16.png
│       │   ├── icon-48.png
│       │   └── icon-128.png
│       └── popup/
│           ├── popup.html
│           ├── popup.js
│           └── popup.css
│
└── native-messaging/
    ├── com.ydm.native_host.json    # Firefox native messaging manifest
    ├── com.ydm.native_host_chrome.json  # Chrome/Brave native messaging manifest
    ├── native_host.py              # The native messaging host script
    └── install_native_host.sh      # Registers native host with browsers
```

---

## Agent Roles & Task Assignments

---

### Agent 1 — Claude Sonnet 4.6 (HARDEST TASK)
**Role: Browser Integration & Native Messaging**

**Why it's the hardest:**
- Must implement TWO different extension manifests (MV2 for Firefox, MV3 for Brave) with different APIs.
- Native Messaging is a complex, poorly-documented binary protocol (4-byte little-endian length prefix + JSON payload).
- Must handle cross-browser quirks and different native messaging host registration paths.
- Must coordinate between content scripts, background scripts, popup, and native host.
- Must handle async messaging flows across browser ↔ OS boundaries.

**Files to create:**
- `browser-extension/firefox/manifest.json`
- `browser-extension/firefox/background.js`
- `browser-extension/firefox/content.js`
- `browser-extension/firefox/popup/popup.html`
- `browser-extension/firefox/popup/popup.js`
- `browser-extension/firefox/popup/popup.css`
- `browser-extension/chrome/manifest.json`
- `browser-extension/chrome/background.js`
- `browser-extension/chrome/content.js`
- `browser-extension/chrome/popup/popup.html`
- `browser-extension/chrome/popup/popup.js`
- `browser-extension/chrome/popup/popup.css`
- `native-messaging/native_host.py`
- `native-messaging/com.ydm.native_host.json`
- `native-messaging/com.ydm.native_host_chrome.json`
- `native-messaging/install_native_host.sh`
- `install.sh`

**Detailed Task Breakdown:**

1. **Content Script** (`content.js`)
   - Detect if the current page is a YouTube video page (`/watch?v=`).
   - Extract video URL and title from the DOM.
   - Send a message to the background script with `{ action: "yt_detected", url, title }`.
   - Listen for tab URL changes (YouTube is a SPA - Single Page Application).

2. **Background Script** (`background.js` for Firefox / Service Worker for Chrome)
   - Receive messages from content script.
   - Set the browser action badge to "1" when a YT video is detected.
   - On user clicking "Download" in popup: send message to native host via `browser.runtime.connectNative("com.ydm.native_host")`.
   - Handle native messaging port: send JSON, receive response.
   - Manage state: current tab video info.

3. **Popup UI** (`popup.html` / `popup.js` / `popup.css`)
   - Show video thumbnail (from `https://img.youtube.com/vi/{video_id}/hqdefault.jpg`).
   - Show video title.
   - Show a dropdown for format selection (will be populated dynamically from native host response: "720p MP4", "1080p MP4", "Audio MP3", etc.).
   - A prominent "Download" button.
   - Status indicator (Connecting / Downloading / Error).
   - Match IDM's style: clean, minimal, functional.

4. **Native Messaging Host** (`native_host.py`)
   - Implement the Native Messaging binary protocol:
     - Read 4-byte little-endian message length from `sys.stdin.buffer`.
     - Read that many bytes and JSON-decode.
     - Process the message (forward to YDM app server via HTTP POST to `localhost:50123`).
     - Write response: encode JSON, write 4-byte length, write JSON bytes to `sys.stdout.buffer`.
   - Handle `get_formats` action: call YDM server to get available formats for a URL.
   - Handle `download` action: call YDM server to start a download.
   - Must be a standalone executable (shebang: `#!/usr/bin/env python3`).

5. **Native Messaging Manifests**
   - `com.ydm.native_host.json`: Firefox format, points to `native_host.py`, allowed extension ID.
   - `com.ydm.native_host_chrome.json`: Chrome/Brave format (same structure, different allowed_origins format).
   - Installation paths:
     - Firefox: `~/.mozilla/native-messaging-hosts/`
     - Chrome/Brave: `~/.config/google-chrome/NativeMessagingHosts/` or `~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/`

6. **Install Script** (`install.sh` + `install_native_host.sh`)
   - Install Python dependencies (`pip install -r requirements.txt`).
   - Copy native host manifest to correct browser paths.
   - Make `native_host.py` executable.
   - Create a `.desktop` file for GNOME app launcher.
   - Print success/failure per step.

---

### Agent 2 — Gemini
**Role: Core Download Engine & Backend**

**Files to create:**
- `ydm/__init__.py`
- `ydm/core/__init__.py`
- `ydm/core/models.py`
- `ydm/core/database.py`
- `ydm/core/downloader.py`
- `ydm/core/queue_manager.py`
- `ydm/core/server.py`
- `requirements.txt`

**Detailed Task Breakdown:**

1. **Data Models** (`models.py`)
   - `DownloadStatus` enum: `QUEUED`, `FETCHING_INFO`, `DOWNLOADING`, `PAUSED`, `COMPLETED`, `FAILED`, `CANCELLED`.
   - `VideoFormat` dataclass: `format_id`, `ext`, `resolution`, `filesize`, `vcodec`, `acodec`, `label` (human-readable).
   - `DownloadItem` dataclass: `id` (UUID), `url`, `title`, `thumbnail_url`, `selected_format`, `status`, `progress` (0.0-1.0), `speed`, `eta`, `filesize`, `downloaded_bytes`, `save_path`, `created_at`, `completed_at`, `error_message`.

2. **Database** (`database.py`)
   - SQLite database at `~/.local/share/ydm/ydm.db`.
   - Tables: `downloads` (full history), `settings` (key-value).
   - CRUD: `add_download()`, `update_download()`, `get_all_downloads()`, `get_download_by_id()`, `delete_download()`, `search_downloads()`.
   - Settings: `get_setting(key, default)`, `set_setting(key, value)`.
   - Default settings: `save_path = ~/Downloads`, `max_concurrent = 3`, `auto_retry = True`.

3. **Download Engine** (`downloader.py`)
   - `get_video_formats(url)` → list of `VideoFormat`. Uses `yt_dlp.YoutubeDL` with `extract_flat=False` to fetch info without downloading.
   - `start_download(item: DownloadItem, progress_callback, status_callback)` → runs yt-dlp download in a thread.
   - yt-dlp `progress_hook` to call `progress_callback(downloaded_bytes, total_bytes, speed, eta)`.
   - Support pause/resume via a threading `Event`.
   - On completion: call `status_callback(DownloadStatus.COMPLETED)`.
   - On error: call `status_callback(DownloadStatus.FAILED, error_message)`.

4. **Queue Manager** (`queue_manager.py`)
   - `add(item: DownloadItem)` → adds to queue, starts download if slots available.
   - `pause(item_id)` → signals the download thread to pause.
   - `resume(item_id)` → signals the download thread to resume.
   - `cancel(item_id)` → cancels and removes from active downloads.
   - `remove(item_id)` → removes from queue (only if not downloading).
   - `max_concurrent` setting respected (default 3).
   - Callbacks: `on_item_updated(item)` → notifies GUI of state changes via a registered callback.

5. **Local HTTP Server** (`server.py`)
   - `aiohttp` server on `localhost:50123`.
   - Routes:
     - `POST /download` → `{ "url": "...", "format_id": "...", "save_path": "..." }` → add to queue.
     - `POST /get_formats` → `{ "url": "..." }` → returns list of available formats as JSON.
     - `GET /status` → returns current queue status as JSON.
     - `GET /ping` → health check, returns `{ "status": "ok" }`.
   - Run alongside the GTK main loop using `asyncio` + GLib integration.

---

### Agent 3 — Gemini
**Role: GTK4 GUI & GNOME Integration**

**Files to create:**
- `ydm/gui/__init__.py`
- `ydm/gui/app.py`
- `ydm/gui/main_window.py`
- `ydm/gui/download_row.py`
- `ydm/gui/add_dialog.py`
- `ydm/gui/settings_dialog.py`
- `ydm/gui/tray.py`
- `ydm/gui/notifications.py`
- `ydm/main.py`
- `data/com.ydm.app.desktop`

**Detailed Task Breakdown:**

1. **App Entry Point** (`app.py` + `main.py`)
   - `Adw.Application` subclass with app ID `com.ydm.app`.
   - `activate()`: create and present `MainWindow`.
   - `main.py`: parse CLI args, instantiate app, run.

2. **Main Window** (`main_window.py`)
   - `Adw.ApplicationWindow` with `Adw.HeaderBar`.
   - Left sidebar: filter buttons (All, Downloading, Completed, Failed).
   - Main content: `Gtk.ListBox` of `DownloadRow` widgets.
   - Header actions: "+" Add Download, Settings gear icon, Speed indicator label.
   - Bottom bar: global progress summary (N downloads active, total speed).
   - Respond to `on_item_updated` callbacks from `QueueManager` via `GLib.idle_add`.
   - Connect to `QueueManager` on startup.

3. **Download Row Widget** (`download_row.py`)
   - `Gtk.ListBoxRow` subclass for each `DownloadItem`.
   - Shows: thumbnail (async loaded), title, status badge, progress bar, speed + ETA labels, file size.
   - Buttons: Pause/Resume (toggle), Cancel, Open File (on completion), Remove.
   - Smooth progress bar animation using `GLib.timeout_add`.
   - Different styling per status (green = done, red = failed, blue = downloading).

4. **Add Download Dialog** (`add_dialog.py`)
   - `Adw.Dialog` (or `Gtk.Dialog`) triggered by "+" button.
   - Step 1: URL input field with a "Fetch Formats" button. Pre-fills if clipboard contains a YouTube URL.
   - Step 2 (after fetch): Shows video thumbnail + title + a `Gtk.DropDown` of available formats.
   - Save path selector (`Gtk.FileChooserButton`).
   - "Download" button → calls `QueueManager.add()`.

5. **Settings Dialog** (`settings_dialog.py`)
   - `Adw.PreferencesWindow`.
   - Groups:
     - Downloads: Default save path, max concurrent downloads (1-5 spinner).
     - Network: Auto-retry toggle, retry count.
     - Integration: "Open browser extension guide" button.
   - Changes saved immediately to SQLite via `Database.set_setting()`.

6. **System Tray** (`tray.py`)
   - `AppIndicator3` tray icon (fallback to `Gtk.StatusIcon`).
   - Icon changes: idle (grey), active downloads (green/animated), error (red).
   - Right-click menu: Show/Hide YDM window, Pause All, Resume All, Quit.

7. **Notifications** (`notifications.py`)
   - `gi.repository.Notify` wrapper.
   - `notify_download_started(title)` → "Downloading: [title]".
   - `notify_download_complete(title, path)` → "✅ Done: [title]" with "Open File" action.
   - `notify_download_failed(title, reason)` → "❌ Failed: [title] - [reason]".

---

## Milestones

| Phase | Goal | Owner |
|---|---|---|
| Phase 1 | Core models + DB + yt-dlp engine + local server | Agent 2 |
| Phase 2 | GTK4 main window + download rows + add dialog | Agent 3 |
| Phase 3 | Browser extensions (Firefox + Brave) + native messaging | Agent 1 |
| Phase 4 | Tray + notifications + settings + clipboard monitoring | Agent 3 |
| Phase 5 | End-to-end testing + install script | Agent 1 |
| Phase 6 | Polish, error handling, README | All |

---

## Important Notes for All Agents

- All agents must follow the same `DownloadItem` and `DownloadStatus` models defined in `ydm/core/models.py`. Agent 2 defines these first.
- The local server always runs on `localhost:50123`. Do not hardcode other ports.
- Use `GLib.idle_add()` for any GTK updates triggered from background threads.
- The native messaging host must be a standalone Python 3 script. No imports outside stdlib + `requests`.
- Browser extensions must NOT use `eval()` or dynamic code execution (CSP restrictions).
- All dialogs should follow libadwaita design guidelines (use `Adw.*` widgets where available).
- Error messages shown to the user must be human-readable (not raw Python tracebacks).
