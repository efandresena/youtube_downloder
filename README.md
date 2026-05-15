# YDM — YouTube Download Manager

A Linux-native YouTube download manager with a GTK4 GUI, download queue, and browser extension for Firefox/Brave/Chrome.

## Features

- Desktop app with GTK4 + libadwaita (GNOME-style)
- Browser extension auto-detects YouTube videos
- Shows MP4 formats from 360p to highest, plus best MP3 audio
- Rename files before downloading
- Download queue with pause/resume/cancel
- Download history (SQLite)

## Requirements

- Python 3.10+
- GTK4 + libadwaita + PyGObject (install via your package manager)
- `yt-dlp` and `aiohttp` (installed automatically)
- FFmpeg (for audio extraction and video/audio merging)
- Firefox, Brave, or Chrome (for the browser extension)

---

## Quick Start

### 1. Install system dependencies

<details>
<summary><b>Ubuntu / Debian / Linux Mint / Pop!_OS</b></summary>

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 ffmpeg git
```
</details>

<details>
<summary><b>Fedora</b></summary>

```bash
sudo dnf install python3 python3-pip python3-gobject gtk4 libadwaita ffmpeg git
```
</details>

<details>
<summary><b>Arch Linux / Manjaro / EndeavourOS</b></summary>

```bash
sudo pacman -S python python-pip python-gobject gtk4 libadwaita ffmpeg git
```
</details>

### 2. Run the installer

```bash
chmod +x install.sh
./install.sh
```

This creates a virtual environment, installs Python packages, registers native messaging hosts, and creates a desktop launcher.

### 3. Start YDM

Launch from your app menu ("YDM") or run:

```bash
python3 ydm/main.py
```

You should see: `YDM server listening on http://127.0.0.1:50123`

### 4. Install the browser extension

#### Firefox

1. Open Firefox → `about:debugging#/runtime/this-firefox`
2. Click **Load Temporary Add-on**
3. Select `browser-extension/firefox/manifest.json`

#### Brave / Chrome

1. Open `brave://extensions` or `chrome://extensions`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked**
4. Select the `browser-extension/chrome/` folder
5. Copy the **Extension ID** shown on the card
6. Run the installer again with that ID:
   ```bash
   ./install.sh --chrome-extension-id YOUR_EXTENSION_ID
   ```
7. Reload the extension (refresh icon on the extension card)

### 5. Use it

1. Go to any YouTube video
2. The YDM toolbar icon will turn red (▼) when a video is detected
3. Click the icon → formats load automatically
4. Pick a format, optionally rename the file, click Download

---

## Quick Uninstall

```bash
rm -rf ~/.local/share/ydm ~/.local/share/applications/ydm.desktop
rm -f ~/.mozilla/native-messaging-hosts/com.ydm.native_host.json
rm -f ~/.config/google-chrome/NativeMessagingHosts/com.ydm.native_host.json
rm -f ~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/com.ydm.native_host.json
rm -f ~/.config/chromium/NativeMessagingHosts/com.ydm.native_host.json
```

Then remove the extension from your browser (`chrome://extensions` → Remove).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `gi.repository` import error | Install GTK4/PyGObject system packages (see distro commands above) |
| "YDM app is not running" | Start YDM first: `python3 ydm/main.py` |
| "Native host not found" | Re-run: `./install.sh --chrome-extension-id YOUR_ID` |
| Downloads fail | Update yt-dlp: `pip install -U yt-dlp` |
| Video/audio not merging | Install FFmpeg |

---

## Packaging for Fedora (RPM)

To distribute YDM as a Fedora RPM package, you would:

1. **Create a `ydm.spec` file** with build requirements (python3-devel, gtk4-devel, libadwaita-devel, ffmpeg-free) and runtime dependencies (python3-gobject, gtk4, libadwaita, ffmpeg-free, yt-dlp).
2. **Bundle the extension** — package the browser extension files and install native messaging manifests to the system-wide locations (`/etc/chromium/native-messaging-hosts/`, `/usr/lib/mozilla/native-messaging-hosts/`).
3. **Submit to Fedora Review** — request a review at [bugzilla.redhat.com](https://bugzilla.redhat.com) under the Fedora component. The package must follow the [Fedora Packaging Guidelines](https://docs.fedoraproject.org/en-US/packaging-guidelines/).
4. **COPR as an alternative** — host the package on [copr.fedorainfracloud.org](https://copr.fedorainfracloud.org/) for easier distribution without going through the full review process.

---

## Support & Feedback

- **Report bugs or request features** — open an issue at [github.com/efandresena/youtube_downloder/issues](https://github.com/efandresena/youtube_downloder/issues)
- When reporting a problem, include:
  - Your Linux distribution and version
  - What you were doing when the issue occurred
  - Any error messages from the terminal or log files (`~/.local/share/ydm/native_host.log`, `~/.local/share/ydm/logs/ydm.log`)
  - Steps to reproduce the issue

---

## License

Personal use only. Respect YouTube's Terms of Service and copyright law.
