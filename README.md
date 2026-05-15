# YDM — YouTube Download Manager

A Linux-native YouTube download manager with a GTK4 GUI, download queue, and browser extension for Firefox/Brave/Chrome.

## Features

- Desktop app with GTK4 + libadwaita (GNOME-style)
- Browser extension auto-detects YouTube videos
- Shows MP4 formats from 360p to highest, plus best MP3 audio
- Rename files before downloading
- Download queue with pause/resume/cancel
- Download history (SQLite)

---

## Step-by-Step Installation

### Step 1: Clone the repository

```bash
git clone https://github.com/efandresena/youtube_downloder.git
cd youtube_downloder
```

### Step 2: Install system dependencies

Choose your distribution below and run the command.

**Ubuntu / Debian / Linux Mint / Pop!_OS**

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 ffmpeg git
```

**Fedora**

```bash
sudo dnf install python3 python3-pip python3-gobject gtk4 libadwaita ffmpeg git
```

**Arch Linux / Manjaro / EndeavourOS**

```bash
sudo pacman -S python python-pip python-gobject gtk4 libadwaita ffmpeg git
```

### Step 3: Run the installer

```bash
chmod +x install.sh
./install.sh
```

This will:
- Create a Python virtual environment at `~/.local/share/ydm/venv`
- Install `yt-dlp`, `aiohttp`, and other Python packages
- Register the native messaging host for Firefox and Chrome-based browsers
- Create a GNOME desktop launcher

### Step 4: Start YDM

Launch from your app menu (search for "YDM") or run from the terminal:

```bash
python3 ydm/main.py
```

You should see this in the terminal output:

```
YDM server listening on http://127.0.0.1:50123
```

Keep YDM running in the background while you use the browser extension.

### Step 5: Install the browser extension

#### Firefox

1. Open Firefox
2. Go to `about:debugging#/runtime/this-firefox`
3. Click **Load Temporary Add-on**
4. Select `browser-extension/firefox/manifest.json`
5. The YDM icon should appear in the toolbar

#### Brave / Chrome

1. Open `brave://extensions` (or `chrome://extensions`)
2. Enable **Developer mode** (toggle in the top right corner)
3. Click **Load unpacked**
4. Select the `browser-extension/chrome/` folder
5. Copy the **Extension ID** shown on the extension card
6. Run the installer again with that ID:
   ```bash
   ./install.sh --chrome-extension-id YOUR_EXTENSION_ID
   ```
7. Reload the extension by clicking the refresh icon on the extension card

### Step 6: Download a video

1. Go to any YouTube video page
2. The YDM toolbar icon will turn red with a ▼ badge
3. Click the icon — formats load automatically
4. Select a format (MP4 video from 360p to highest, or best MP3 audio)
5. Optionally rename the file in the text box
6. Click **Download**
7. Check the YDM app window to see the download progress

---

## Uninstall

```bash
rm -rf ~/.local/share/ydm ~/.local/share/applications/ydm.desktop
rm -f ~/.mozilla/native-messaging-hosts/com.ydm.native_host.json
rm -f ~/.config/google-chrome/NativeMessagingHosts/com.ydm.native_host.json
rm -f ~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/com.ydm.native_host.json
rm -f ~/.config/chromium/NativeMessagingHosts/com.ydm.native_host.json
```

Then remove the extension from your browser (extensions page → Remove).

---

## Troubleshooting

### `gi.repository` import error

GTK4/PyGObject system packages are missing. Run the install command for your distribution from Step 2 above, then try again.

### "YDM app is not running"

Start YDM first with `python3 ydm/main.py` and keep it running. The browser extension communicates with YDM through a local server.

### "Native host not found" (Chrome/Brave)

The extension ID is not registered. Re-run the installer with the correct ID:

```bash
./install.sh --chrome-extension-id YOUR_EXTENSION_ID
```

Get the ID from `brave://extensions` or `chrome://extensions`.

### Downloads fail or yt-dlp errors

Update yt-dlp to the latest version:

```bash
pip install -U yt-dlp
```

If using the virtual environment:

```bash
~/.local/share/ydm/venv/bin/pip install -U yt-dlp
```

### Video has no audio or audio-only has no video

FFmpeg is required for merging. Install it:

- Ubuntu/Debian: `sudo apt install ffmpeg`
- Fedora: `sudo dnf install ffmpeg`
- Arch: `sudo pacman -S ffmpeg`

---

## Support & Feedback

- **Report bugs or request features** — open an issue at [github.com/efandresena/youtube_downloder/issues](https://github.com/efandresena/youtube_downloder/issues)
- When reporting a problem, include:
  - Your Linux distribution and version
  - What you were doing when the issue occurred
  - Any error messages from the terminal
  - Log files from `~/.local/share/ydm/native_host.log` and `~/.local/share/ydm/logs/ydm.log`
  - Steps to reproduce the issue

---

## Packaging for Fedora (RPM)

To distribute YDM as a Fedora RPM package:

1. **Create a `ydm.spec` file** with build requirements (python3-devel, gtk4-devel, libadwaita-devel, ffmpeg-free) and runtime dependencies (python3-gobject, gtk4, libadwaita, ffmpeg-free, yt-dlp).
2. **Bundle the extension** — package the browser extension files and install native messaging manifests to the system-wide locations (`/etc/chromium/native-messaging-hosts/`, `/usr/lib/mozilla/native-messaging-hosts/`).
3. **Submit to Fedora Review** — request a review at [bugzilla.redhat.com](https://bugzilla.redhat.com) under the Fedora component. The package must follow the [Fedora Packaging Guidelines](https://docs.fedoraproject.org/en-US/packaging-guidelines/).
4. **COPR as an alternative** — host the package on [copr.fedorainfracloud.org](https://copr.fedorainfracloud.org/) for easier distribution without going through the full review process.

---

## License

Personal use only. Respect YouTube's Terms of Service and copyright law.
