#!/usr/bin/env bash
# =============================================================================
# YDM — Native Messaging Host Installer
# =============================================================================
# Registers native_host.py with Firefox, Chrome, and Brave so the browser
# extensions can communicate with the YDM desktop application.
#
# Usage:
#   ./install_native_host.sh [--chrome-extension-id EXTENSION_ID]
#
# Options:
#   --chrome-extension-id ID    The Chrome/Brave extension ID (shown in
#                               chrome://extensions after loading the extension).
#                               If omitted, a placeholder is used and you must
#                               edit the installed manifest manually.
#
# =============================================================================

set -euo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✔${RESET}  $*"; }
fail() { echo -e "  ${RED}✘${RESET}  $*"; }
info() { echo -e "  ${CYAN}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
header() {
  echo ""
  echo -e "${BOLD}${CYAN}$*${RESET}"
  echo -e "${CYAN}$(printf '─%.0s' $(seq 1 60))${RESET}"
}

# ── Argument Parsing ──────────────────────────────────────────────────────────

CHROME_EXT_ID="__EXTENSION_ID__"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --chrome-extension-id)
      CHROME_EXT_ID="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--chrome-extension-id EXTENSION_ID]"
      exit 0
      ;;
    *)
      warn "Unknown argument: $1"
      shift
      ;;
  esac
done

# ── Resolve Paths ─────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NATIVE_HOST_PY="${SCRIPT_DIR}/native_host.py"

header "YDM Native Messaging Host Installer"

info "Script directory : ${SCRIPT_DIR}"
info "native_host.py   : ${NATIVE_HOST_PY}"

# Verify native_host.py exists
if [[ ! -f "${NATIVE_HOST_PY}" ]]; then
  fail "native_host.py not found at: ${NATIVE_HOST_PY}"
  echo ""
  echo -e "${RED}Aborting — cannot continue without the native host script.${RESET}"
  exit 1
fi

# ── Make native_host.py executable ────────────────────────────────────────────

header "Step 1 — Make native_host.py executable"

if chmod +x "${NATIVE_HOST_PY}"; then
  ok "chmod +x native_host.py"
else
  fail "Failed to chmod +x native_host.py"
  exit 1
fi

# Validate Python syntax without executing the native host.
# Do NOT run native_host.py directly here: it waits for Native Messaging
# binary-framed input from stdin and would make the installer appear stuck.
if python3 -m py_compile "${NATIVE_HOST_PY}"; then
  ok "native_host.py syntax OK ($(python3 --version 2>&1))"
else
  fail "native_host.py has a Python syntax error"
  exit 1
fi

# ── Build Temporary Manifests ──────────────────────────────────────────────────

header "Step 2 — Build manifests with absolute path"

FIREFOX_MANIFEST_SRC="${SCRIPT_DIR}/com.ydm.native_host.json"
CHROME_MANIFEST_SRC="${SCRIPT_DIR}/com.ydm.native_host_chrome.json"

TMP_FIREFOX_MANIFEST="$(mktemp /tmp/ydm_firefox_manifest.XXXXXX.json)"
TMP_CHROME_MANIFEST="$(mktemp /tmp/ydm_chrome_manifest.XXXXXX.json)"

# Escape the path for sed (handle slashes)
ESCAPED_PATH="${NATIVE_HOST_PY//\//\\/}"

sed "s|__NATIVE_HOST_PATH__|${NATIVE_HOST_PY}|g" \
    "${FIREFOX_MANIFEST_SRC}" > "${TMP_FIREFOX_MANIFEST}"
ok "Firefox manifest built   → ${TMP_FIREFOX_MANIFEST}"

sed \
  -e "s|__NATIVE_HOST_PATH__|${NATIVE_HOST_PY}|g" \
  -e "s|__EXTENSION_ID__|${CHROME_EXT_ID}|g" \
  "${CHROME_MANIFEST_SRC}" > "${TMP_CHROME_MANIFEST}"
ok "Chrome manifest built    → ${TMP_CHROME_MANIFEST}"

if [[ "${CHROME_EXT_ID}" == "__EXTENSION_ID__" ]]; then
  warn "Chrome extension ID not provided — placeholder left in manifest."
  warn "After loading the extension in Chrome/Brave, edit the manifest:"
  warn "  Replace '__EXTENSION_ID__' with the real ID shown in chrome://extensions"
fi

# ── Install Firefox Manifest ───────────────────────────────────────────────────

header "Step 3 — Install Firefox native messaging manifest"

FIREFOX_NMHOSTS="${HOME}/.mozilla/native-messaging-hosts"
FIREFOX_DEST="${FIREFOX_NMHOSTS}/com.ydm.native_host.json"

if mkdir -p "${FIREFOX_NMHOSTS}"; then
  ok "Directory exists: ${FIREFOX_NMHOSTS}"
else
  fail "Could not create: ${FIREFOX_NMHOSTS}"
  exit 1
fi

if cp "${TMP_FIREFOX_MANIFEST}" "${FIREFOX_DEST}"; then
  ok "Installed Firefox manifest → ${FIREFOX_DEST}"
else
  fail "Failed to copy Firefox manifest to ${FIREFOX_DEST}"
  exit 1
fi

# ── Install Chrome Manifest ────────────────────────────────────────────────────

header "Step 4 — Install Chrome native messaging manifest"

CHROME_NMHOSTS="${HOME}/.config/google-chrome/NativeMessagingHosts"
CHROME_DEST="${CHROME_NMHOSTS}/com.ydm.native_host.json"

if mkdir -p "${CHROME_NMHOSTS}"; then
  ok "Directory exists: ${CHROME_NMHOSTS}"
else
  warn "Could not create Chrome directory: ${CHROME_NMHOSTS} (Chrome may not be installed)"
fi

if cp "${TMP_CHROME_MANIFEST}" "${CHROME_DEST}" 2>/dev/null; then
  ok "Installed Chrome manifest → ${CHROME_DEST}"
else
  warn "Failed to install Chrome manifest (Chrome may not be installed — skipping)"
fi

# ── Install Brave Manifest ─────────────────────────────────────────────────────

header "Step 5 — Install Brave native messaging manifest"

BRAVE_NMHOSTS="${HOME}/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts"
BRAVE_DEST="${BRAVE_NMHOSTS}/com.ydm.native_host.json"

if mkdir -p "${BRAVE_NMHOSTS}"; then
  ok "Directory exists: ${BRAVE_NMHOSTS}"
else
  warn "Could not create Brave directory: ${BRAVE_NMHOSTS} (Brave may not be installed)"
fi

if cp "${TMP_CHROME_MANIFEST}" "${BRAVE_DEST}" 2>/dev/null; then
  ok "Installed Brave manifest  → ${BRAVE_DEST}"
else
  warn "Failed to install Brave manifest (Brave may not be installed — skipping)"
fi

# ── Chromium Support ──────────────────────────────────────────────────────────

CHROMIUM_NMHOSTS="${HOME}/.config/chromium/NativeMessagingHosts"
CHROMIUM_DEST="${CHROMIUM_NMHOSTS}/com.ydm.native_host.json"

if [[ -d "${HOME}/.config/chromium" ]]; then
  header "Step 5b — Install Chromium native messaging manifest"
  if mkdir -p "${CHROMIUM_NMHOSTS}" && cp "${TMP_CHROME_MANIFEST}" "${CHROMIUM_DEST}"; then
    ok "Installed Chromium manifest → ${CHROMIUM_DEST}"
  else
    warn "Failed to install Chromium manifest"
  fi
fi

# ── Cleanup Temp Files ─────────────────────────────────────────────────────────

rm -f "${TMP_FIREFOX_MANIFEST}" "${TMP_CHROME_MANIFEST}"

# ── Summary ───────────────────────────────────────────────────────────────────

header "Installation Complete"

echo ""
echo -e "${BOLD}Next Steps:${RESET}"
echo ""
echo -e "  ${CYAN}Firefox:${RESET}"
echo -e "    1. Open Firefox → about:debugging → This Firefox → Load Temporary Add-on"
echo -e "    2. Select: browser-extension/firefox/manifest.json"
echo -e "    3. The YDM toolbar icon should appear."
echo ""
echo -e "  ${CYAN}Chrome / Brave:${RESET}"
echo -e "    1. Open chrome://extensions (or brave://extensions)"
echo -e "    2. Enable 'Developer mode' (top right toggle)"
echo -e "    3. Click 'Load unpacked' → select: browser-extension/chrome/"
echo -e "    4. Copy the Extension ID shown on the card."
if [[ "${CHROME_EXT_ID}" == "__EXTENSION_ID__" ]]; then
  echo ""
  echo -e "    ${YELLOW}5. Update the Chrome/Brave manifest with your Extension ID:${RESET}"
  echo -e "       ${CHROME_DEST}"
  echo -e "       ${BRAVE_DEST}"
  echo -e "       Replace '__EXTENSION_ID__' with the actual ID, then reload the extension."
  echo ""
  echo -e "    ${YELLOW}   OR re-run this installer with:${RESET}"
  echo -e "       ./install_native_host.sh --chrome-extension-id YOUR_EXTENSION_ID"
fi
echo ""
echo -e "  ${CYAN}Log file:${RESET} ${HOME}/.local/share/ydm/native_host.log"
echo ""
echo -e "${GREEN}${BOLD}Done!${RESET}"
echo ""
