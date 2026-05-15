"use strict";

const api = typeof browser !== "undefined" ? browser : chrome;

const views = {
  loading: document.getElementById("view-loading"),
  noVideo: document.getElementById("view-no-video"),
  noApp: document.getElementById("view-no-app"),
  main: document.getElementById("view-main"),
};

const elThumbnail = document.getElementById("thumbnail");
const elVideoTitle = document.getElementById("video-title");
const elFormatSelect = document.getElementById("format-select");
const elRenameInput = document.getElementById("rename-input");
const elBtnDownload = document.getElementById("btn-download");
const elStatusDot = document.getElementById("status-dot");
const elStatusText = document.getElementById("status-text");

function showView(name) {
  Object.entries(views).forEach(([key, el]) => {
    el.classList.toggle("hidden", key !== name);
  });
}

function setStatus(type, text) {
  elStatusDot.className = "status-dot " + type;
  elStatusText.className = "status-text " + (type === "loading" ? "" : type);
  elStatusText.textContent = text;
}

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

  const result = [...videoFormats];
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

let currentVideoInfo = null;

function sendToBackground(msg) {
  return new Promise((resolve, reject) => {
    api.runtime.sendMessage(msg, (response) => {
      if (api.runtime.lastError) {
        reject(new Error(api.runtime.lastError.message));
        return;
      }
      resolve(response);
    });
  });
}

async function init() {
  showView("loading");
  setStatus("loading", "Connecting to YDM\u2026");

  let videoInfo = null;
  try {
    const resp = await sendToBackground({ action: "get_video_info" });
    videoInfo = resp && resp.videoInfo ? resp.videoInfo : null;
  } catch (err) {
    showView("noApp");
    setStatus("error", "Cannot reach extension background.");
    return;
  }

  if (!videoInfo) {
    showView("noVideo");
    return;
  }

  currentVideoInfo = videoInfo;

  elThumbnail.src =
    "https://img.youtube.com/vi/" + videoInfo.videoId + "/hqdefault.jpg";
  elThumbnail.onerror = function () {
    elThumbnail.src =
      "https://img.youtube.com/vi/" + videoInfo.videoId + "/mqdefault.jpg";
  };
  elVideoTitle.textContent = videoInfo.title || "(Unknown title)";

  elRenameInput.value = videoInfo.title || "";

  elFormatSelect.innerHTML = '<option value="">Loading formats\u2026</option>';
  elFormatSelect.disabled = true;
  elBtnDownload.disabled = true;

  showView("main");
  setStatus("loading", "Fetching available formats\u2026");

  await fetchFormats();
}

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
    setStatus("success", elFormatSelect.options.length + " option(s) loaded.");
  } catch (err) {
    setStatus("error", "Error: " + err.message);
  }
}

elBtnDownload.addEventListener("click", async () => {
  if (!currentVideoInfo) return;

  const formatId = elFormatSelect.value;
  if (!formatId) {
    setStatus("error", "Please select a format first.");
    return;
  }

  const customTitle = elRenameInput.value.trim() || currentVideoInfo.title;

  elBtnDownload.disabled = true;
  elFormatSelect.disabled = true;
  setStatus("loading", "Sending to YDM\u2026");

  try {
    const resp = await sendToBackground({
      action: "download",
      url: currentVideoInfo.url,
      format_id: formatId,
      title: customTitle,
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

document.addEventListener("DOMContentLoaded", init);
