"use strict";

const api = typeof browser !== "undefined" ? browser : chrome;
const POPUP_TIMEOUT_MS = 20000;

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
const elPathInput = document.getElementById("path-input");
const elBtnBrowse = document.getElementById("btn-browse");
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

let currentVideoInfo = null;
let formatError = false;

function showRetryBtn(show) {
  let btn = document.getElementById("btn-retry");
  if (!btn && show) {
    btn = document.createElement("button");
    btn.id = "btn-retry";
    btn.className = "btn-retry";
    btn.textContent = "Retry";
    btn.addEventListener("click", fetchFormats);
    document.querySelector(".controls").appendChild(btn);
  }
  if (btn) btn.style.display = show ? "" : "none";
}

function sendToBackground(msg) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error("Request timed out. Is YDM running?"));
    }, POPUP_TIMEOUT_MS);

    api.runtime.sendMessage(msg, (response) => {
      clearTimeout(timer);
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
  elPathInput.value = "~/Downloads";

  elFormatSelect.innerHTML = '<option value="">Loading formats\u2026</option>';
  elFormatSelect.disabled = true;
  elBtnDownload.disabled = true;

  showView("main");
  setStatus("loading", "Fetching available formats\u2026");

  await fetchFormats();
}

async function fetchFormats() {
  if (!currentVideoInfo) return;

  showRetryBtn(false);
  formatError = false;

  elBtnDownload.disabled = true;
  elFormatSelect.disabled = true;
  elFormatSelect.innerHTML = '<option value="">Loading formats\u2026</option>';
  setStatus("loading", "Fetching available formats\u2026");

  try {
    const resp = await sendToBackground({
      action: "get_formats",
      url: currentVideoInfo.url,
    });

    if (resp && resp.error) {
      formatError = true;
      setStatus("error", resp.error);
      showRetryBtn(true);
      return;
    }

    const formats = (resp && resp.formats) ? resp.formats : resp;
    if (!Array.isArray(formats) || formats.length === 0) {
      formatError = true;
      setStatus("error", "No formats returned by YDM server.");
      showRetryBtn(true);
      return;
    }

    populateFormats(formats);
    const count = elFormatSelect.options.length;
    setStatus("success", count + " option(s) loaded.");
  } catch (err) {
    formatError = true;
    setStatus("error", "Error: " + err.message);
    showRetryBtn(true);
  }
}

elBtnBrowse.addEventListener("click", async () => {
  try {
    const input = document.createElement("input");
    input.type = "file";
    input.webkitdirectory = true;
    input.style.display = "none";
    input.addEventListener("change", () => {
      if (input.files.length > 0) {
        elPathInput.value = input.files[0].webkitRelativePath.split("/")[0] || input.files[0].path || elPathInput.value;
      }
    });
    input.click();
  } catch {
    elPathInput.value = prompt("Enter download folder path:", elPathInput.value) || elPathInput.value;
  }
});

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

document.addEventListener("DOMContentLoaded", init);
