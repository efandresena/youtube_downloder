/**
 * YDM Popup Script — Chrome/Brave (Manifest V3).
 *
 * Uses chrome.* API exclusively (no browser.* shim needed for Chrome).
 *
 * Flow:
 *  1. On open: query background for current tab's video info
 *  2. If no video info → show "no-video" view
 *  3. If video found  → show main view with thumbnail + title
 *  4. "Fetch Formats" → ask background to call native host get_formats
 *  5. Populate the format dropdown
 *  6. "Download" → ask background to call native host download
 */

"use strict";

// ─── DOM References ───────────────────────────────────────────────────────────

const views = {
  loading: document.getElementById("view-loading"),
  noVideo: document.getElementById("view-no-video"),
  noApp:   document.getElementById("view-no-app"),
  main:    document.getElementById("view-main"),
};

const elThumbnail    = document.getElementById("thumbnail");
const elVideoTitle   = document.getElementById("video-title");
const elFormatSelect = document.getElementById("format-select");
const elRenameInput  = document.getElementById("rename-input");
const elPathInput    = document.getElementById("path-input");
const elBtnDownload  = document.getElementById("btn-download");
const elStatusDot    = document.getElementById("status-dot");
const elStatusText   = document.getElementById("status-text");

// ─── View Helpers ─────────────────────────────────────────────────────────────

function showView(name) {
  Object.entries(views).forEach(([key, el]) => {
    el.classList.toggle("hidden", key !== name);
  });
}

// ─── Status Helpers ───────────────────────────────────────────────────────────

function setStatus(type, text) {
  // type: idle | loading | success | error
  elStatusDot.className = "status-dot " + type;
  elStatusText.className = "status-text " + (type === "loading" ? "" : type);
  elStatusText.textContent = text;
}

// ─── Format Filtering ─────────────────────────────────────────────────────────

function filterAndSortFormats(formats) {
  let videoFormats = [];
  let audioFormat = null;

  formats.forEach(fmt => {
    if (fmt.vcodec === "none" || fmt.resolution === "audio only") {
      if (!audioFormat) audioFormat = fmt;
    } else if (fmt.ext === "mp4") {
      videoFormats.push(fmt);
    }
  });

  videoFormats.sort((a, b) => {
    const aRes = parseInt(a.resolution) || 0;
    const bRes = parseInt(b.resolution) || 0;
    return aRes - bRes;
  });

  let result = [...videoFormats];
  if (audioFormat) result.push(audioFormat);
  return result;
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function populateFormats(formats) {
  elFormatSelect.innerHTML = "";

  const filtered = filterAndSortFormats(formats);

  if (!filtered.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No formats available";
    elFormatSelect.appendChild(opt);
    elFormatSelect.disabled = true;
    return;
  }

  filtered.forEach((fmt) => {
    const opt = document.createElement("option");
    opt.value = fmt.format_id;
    let label = fmt.label || fmt.resolution + " " + (fmt.ext || "").toUpperCase();
    if (fmt.filesize) label += " (~" + formatBytes(fmt.filesize) + ")";
    opt.textContent = label;
    elFormatSelect.appendChild(opt);
  });

  elFormatSelect.disabled = false;
  elBtnDownload.disabled = false;
}

// ─── State ────────────────────────────────────────────────────────────────────

let currentVideoInfo = null;

// ─── Message Helper ───────────────────────────────────────────────────────────

function sendToBackground(msg) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(msg, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve(response);
    });
  });
}

// ─── Initialisation ───────────────────────────────────────────────────────────

async function init() {
  showView("loading");
  setStatus("loading", "Connecting to YDM\u2026");

  // Get current tab's video info from background service worker
  let videoInfo = null;
  try {
    const resp = await sendToBackground({ action: "get_video_info" });
    videoInfo = resp && resp.videoInfo ? resp.videoInfo : null;
  } catch (err) {
    showView("noApp");
    setStatus("error", "Cannot reach extension background: " + err.message);
    return;
  }

  if (!videoInfo) {
    showView("noVideo");
    return;
  }

  currentVideoInfo = videoInfo;

  // Populate thumbnail and title
  elThumbnail.src = "https://img.youtube.com/vi/" + videoInfo.videoId + "/hqdefault.jpg";
  elThumbnail.onerror = function () {
    elThumbnail.src = "https://img.youtube.com/vi/" + videoInfo.videoId + "/mqdefault.jpg";
  };
  elVideoTitle.textContent = videoInfo.title || "(Unknown title)";

  // Pre-fill rename input with video title
  elRenameInput.value = videoInfo.title || "";

  // Pre-fill save path with default Downloads folder
  elPathInput.value = "~/Downloads";

  // Reset controls
  elFormatSelect.innerHTML = '<option value="">Loading formats…</option>';
  elFormatSelect.disabled = true;
  elBtnDownload.disabled = true;

  showView("main");
  setStatus("loading", "Fetching available formats\u2026");

  // Auto-fetch formats
  await fetchFormats();
}

// ─── Auto-fetch Formats ───────────────────────────────────────────────────────

async function fetchFormats() {
  if (!currentVideoInfo) return;

  elBtnDownload.disabled = true;
  elFormatSelect.disabled = true;
  setStatus("loading", "Fetching available formats\u2026");

  try {
    const resp = await sendToBackground({
      action: "get_formats",
      url: currentVideoInfo.url,
    });

    if (resp && resp.error) {
      setStatus("error", resp.error);
      return;
    }

    const formats = (resp && resp.formats) ? resp.formats : resp;
    if (!Array.isArray(formats) || formats.length === 0) {
      setStatus("error", "No formats returned by YDM server.");
      return;
    }

    populateFormats(formats);
    const count = elFormatSelect.options.length;
    setStatus("success", count + " option(s) loaded.");
  } catch (err) {
    setStatus("error", "Error: " + err.message);
  }
}

// ─── Event: Download ─────────────────────────────────────────────────────────

elBtnDownload.addEventListener("click", async () => {
  if (!currentVideoInfo) return;

  const formatId = elFormatSelect.value;
  if (!formatId) {
    setStatus("error", "Please select a format first.");
    return;
  }

  const customTitle = elRenameInput.value.trim() || currentVideoInfo.title;
  const customPath = elPathInput.value.trim() || "";

  elBtnDownload.disabled = true;
  elFormatSelect.disabled = true;
  setStatus("loading", "Sending to YDM\u2026");

  try {
    const resp = await sendToBackground({
      action: "download",
      url: currentVideoInfo.url,
      format_id: formatId,
      title: customTitle,
      save_path: customPath,
    });

    if (resp && resp.error) {
      setStatus("error", resp.error);
      elBtnDownload.disabled = false;
      elFormatSelect.disabled = false;
      return;
    }

    setStatus("success", "Download started! Check the YDM app.");
    setTimeout(() => {
      elBtnDownload.disabled = false;
      elFormatSelect.disabled = false;
      setStatus("idle", "Ready.");
    }, 4000);

  } catch (err) {
    setStatus("error", "Error: " + err.message);
    elBtnDownload.disabled = false;
    elFormatSelect.disabled = false;
  }
});

// ─── Boot ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", init);
