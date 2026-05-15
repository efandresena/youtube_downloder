#!/usr/bin/env bash
# =============================================================================
# YDM — YouTube Download Manager
# Master Installer Script
# =============================================================================
#
# What this script does:
#   1. Checks that Python 3.10+ is installed
#   2. Creates a virtualenv at ~/.local/share/ydm/venv
#   3. Installs Python dependencies (yt-dlp, aiohttp, PyGObject)
#   4. Creates ~/.local/share/ydm/ data directory
#   5. Runs native-messaging/install_native_host.sh
#   6. Creates a GNOME .desktop launcher
#   7. Prints a summary
#
# Usage:
#   chmod +x install.sh
#   ./install.sh [--chrome-extension-id EXTENSION_ID]
#
# =============================================================================

set -euo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✔${RESET}  $*"; }
fail() { echo -e "  ${RED}✘${RESET}  $*" >&2; }
info() { echo -e "  ${CYAN}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
step() {
  echo ""
  echo -e "${BOLD}${MAGENTA}$*${RESET}"
  echo -e "${MAGENTA}$(printf '─%.0s' $(seq 1 62))${RESET}"
}

# Print a fatal error message and exit
die() {
  echo ""
  echo -e "${RED}${BOLD}FATAL:${RESET} ${RED}$*${RESET}"
  echo ""
  exit 1
}

# ── Argument Parsing ──────────────────────────────────────────────────────────

CHROME_EXT_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --chrome-extension-id)
      CHROME_EXT_ID="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--chrome-extension-id EXTENSION_ID]"
      echo ""
      echo "  --chrome-extension-id ID   Chrome/Brave extension ID (from chrome://extensions)"
      exit 0
      ;;
    *)
      warn "Unknown argument: $1"
      shift
      ;;
  esac
done

# ── Paths ─────────────────────────────────────────────────────────────────────

INSTALL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YDM_DATA_DIR="${HOME}/.local/share/ydm"
YDM_VENV="${YDM_DATA_DIR}/venv"
YDM_MAIN="${INSTALL_SCRIPT_DIR}/ydm/main.py"
DESKTOP_DIR="${HOME}/.local/share/applications"
DESKTOP_FILE="${DESKTOP_DIR}/ydm.desktop"
NATIVE_HOST_INSTALLER="${INSTALL_SCRIPT_DIR}/native-messaging/install_native_host.sh"

# ── Banner ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}${CYAN}"
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║   YDM — YouTube Download Manager          ║"
echo "  ║   Linux Installer v1.0                    ║"
echo "  ╚═══════════════════════════════════════════╝"
echo -e "${RESET}"
echo -e "  Install directory : ${INSTALL_SCRIPT_DIR}"
echo -e "  Data directory    : ${YDM_DATA_DIR}"
echo -e "  Virtualenv        : ${YDM_VENV}"
echo ""

# ── Step 1: Check Python 3.10+ ────────────────────────────────────────────────

step "Step 1 — Check Python version"

PYTHON_BIN="$(command -v python3 2>/dev/null || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  die "python3 not found. Please install Python 3.10 or later."
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_MAJOR="$(echo "${PYTHON_VERSION}" | cut -d. -f1)"
PYTHON_MINOR="$(echo "${PYTHON_VERSION}" | cut -d. -f2)"

info "Found Python ${PYTHON_VERSION} at ${PYTHON_BIN}"

if [[ "${PYTHON_MAJOR}" -lt 3 ]] || { [[ "${PYTHON_MAJOR}" -eq 3 ]] && [[ "${PYTHON_MINOR}" -lt 10 ]]; }; then
  die "Python 3.10+ required. Found ${PYTHON_VERSION}. Please upgrade Python."
fi
ok "Python ${PYTHON_VERSION} — OK"

# ── Step 2: Check pip ─────────────────────────────────────────────────────────

step "Step 2 — Check pip"

if ! python3 -m pip --version &>/dev/null; then
  die "pip not found. Install it with: sudo apt install python3-pip"
fi
ok "pip available: $(python3 -m pip --version)"

# ── Step 3: Create data directory ─────────────────────────────────────────────

step "Step 3 — Create YDM data directory"

if mkdir -p "${YDM_DATA_DIR}"; then
  ok "Data directory: ${YDM_DATA_DIR}"
else
  die "Failed to create data directory: ${YDM_DATA_DIR}"
fi

# Create a logs subdirectory too
mkdir -p "${YDM_DATA_DIR}/logs"
ok "Logs directory: ${YDM_DATA_DIR}/logs"

# ── Step 4: Create virtualenv ─────────────────────────────────────────────────

step "Step 4 — Create Python virtualenv"

if [[ -d "${YDM_VENV}" ]]; then
  warn "Virtualenv already exists at ${YDM_VENV} — reusing."
else
  info "Creating virtualenv at ${YDM_VENV}…"
  info "Using --system-site-packages so the venv can access distro GTK/PyGObject bindings."
  if python3 -m venv --system-site-packages "${YDM_VENV}"; then
    ok "Virtualenv created: ${YDM_VENV}"
  else
    die "Failed to create virtualenv. Try: sudo apt install python3-venv"
  fi
fi

VENV_PYTHON="${YDM_VENV}/bin/python3"
VENV_PIP="${YDM_VENV}/bin/pip"

# ── Step 5: Install Python dependencies ──────────────────────────────────────

step "Step 5 — Install Python dependencies"

info "Upgrading pip inside virtualenv…"
"${VENV_PIP}" install --quiet --upgrade pip
ok "pip upgraded"

# yt-dlp — download engine
info "Installing yt-dlp…"
if "${VENV_PIP}" install --quiet yt-dlp; then
  ok "yt-dlp installed ($(${VENV_PYTHON} -m yt_dlp --version 2>/dev/null || echo 'ok'))"
else
  die "Failed to install yt-dlp."
fi

# aiohttp — local HTTP server
info "Installing aiohttp…"
if "${VENV_PIP}" install --quiet aiohttp; then
  ok "aiohttp installed"
else
  die "Failed to install aiohttp."
fi

# PyGObject — GTK4 / libadwaita Python bindings
# PyGObject should come from the distro package manager. The venv was created
# with --system-site-packages so it can import system gi bindings.
info "Checking GTK4/libadwaita Python bindings…"
if "${VENV_PYTHON}" -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Gtk, Adw" 2>/dev/null; then
  ok "GTK4/libadwaita bindings available"
else
  warn "GTK4/libadwaita Python bindings are missing."
  warn "Install distro packages first, then re-run this installer."
  warn "Ubuntu/Debian: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1"
  warn "Fedora:        sudo dnf install python3-gobject gtk4 libadwaita"
  warn "Arch:          sudo pacman -S python-gobject gtk4 libadwaita"
fi

# Check for requirements.txt and install any additional deps
REQUIREMENTS_FILE="${INSTALL_SCRIPT_DIR}/requirements.txt"
if [[ -f "${REQUIREMENTS_FILE}" ]]; then
  info "Installing from requirements.txt…"
  if "${VENV_PIP}" install --quiet -r "${REQUIREMENTS_FILE}"; then
    ok "requirements.txt installed"
  else
    warn "Some requirements failed to install — check the log."
  fi
fi

# ── Step 6: Register Native Messaging Host ────────────────────────────────────

step "Step 6 — Register Native Messaging Host"

if [[ ! -f "${NATIVE_HOST_INSTALLER}" ]]; then
  warn "install_native_host.sh not found at: ${NATIVE_HOST_INSTALLER}"
  warn "Skipping native messaging host installation."
else
  chmod +x "${NATIVE_HOST_INSTALLER}"

  if [[ -n "${CHROME_EXT_ID}" ]]; then
    "${NATIVE_HOST_INSTALLER}" --chrome-extension-id "${CHROME_EXT_ID}"
  else
    "${NATIVE_HOST_INSTALLER}"
  fi
fi

# ── Step 7: Create GNOME .desktop launcher ────────────────────────────────────

step "Step 7 — Create GNOME .desktop launcher"

mkdir -p "${DESKTOP_DIR}"

# Determine the icon path (use a fallback system icon if our icon doesn't exist yet)
ICON_PATH="${INSTALL_SCRIPT_DIR}/browser-extension/firefox/icons/icon-128.png"
if [[ ! -f "${ICON_PATH}" ]]; then
  ICON_PATH="video-x-generic"  # GNOME fallback icon
fi

cat > "${DESKTOP_FILE}" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=YDM - YouTube Download Manager
Comment=One-click YouTube downloads with format selection and download queue
Exec=${VENV_PYTHON} ${YDM_MAIN}
Icon=${ICON_PATH}
Terminal=false
Categories=Network;Video;
Keywords=youtube;download;video;ydm;
StartupNotify=true
StartupWMClass=ydm
EOF

if [[ -f "${DESKTOP_FILE}" ]]; then
  ok "Desktop launcher created: ${DESKTOP_FILE}"
  # Make it executable
  chmod +x "${DESKTOP_FILE}" 2>/dev/null || true
  # Notify GNOME to pick it up
  if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "${DESKTOP_DIR}" 2>/dev/null || true
    ok "Desktop database updated"
  fi
else
  warn "Failed to create desktop file."
fi

# ── Step 8: Make scripts executable ───────────────────────────────────────────

step "Step 8 — Set permissions"

chmod +x "${NATIVE_HOST_INSTALLER}" 2>/dev/null && ok "install_native_host.sh +x" || true
chmod +x "${INSTALL_SCRIPT_DIR}/native-messaging/native_host.py" 2>/dev/null && ok "native_host.py +x" || true

# ── Final Summary ─────────────────────────────────────────────────────────────

step "Installation Summary"

echo ""
echo -e "  ${GREEN}${BOLD}YDM has been installed successfully!${RESET}"
echo ""
echo -e "  ${BOLD}How to launch:${RESET}"
echo -e "    ${CYAN}From GNOME:${RESET}  Search for 'YDM' in your application launcher"
echo -e "    ${CYAN}From terminal:${RESET}"
echo -e "      ${VENV_PYTHON} ${YDM_MAIN}"
echo ""
echo -e "  ${BOLD}Browser Extensions:${RESET}"
echo -e "    ${CYAN}Firefox:${RESET}"
echo -e "      about:debugging → Load Temporary Add-on"
echo -e "      → ${INSTALL_SCRIPT_DIR}/browser-extension/firefox/manifest.json"
echo ""
echo -e "    ${CYAN}Chrome / Brave:${RESET}"
echo -e "      chrome://extensions → Developer mode → Load unpacked"
echo -e "      → ${INSTALL_SCRIPT_DIR}/browser-extension/chrome/"
echo ""
echo -e "  ${BOLD}Log files:${RESET}"
echo -e "    Native host : ${YDM_DATA_DIR}/native_host.log"
echo -e "    App log     : ${YDM_DATA_DIR}/logs/ydm.log"
echo ""

if [[ -z "${CHROME_EXT_ID}" ]]; then
  echo -e "  ${YELLOW}${BOLD}Reminder:${RESET} ${YELLOW}After loading the Chrome/Brave extension, run:${RESET}"
  echo -e "  ${YELLOW}  ./install.sh --chrome-extension-id YOUR_EXTENSION_ID${RESET}"
  echo ""
fi

echo -e "${GREEN}${BOLD}All done! Enjoy YDM.${RESET}"
echo ""
