/**
 * YDM Background Service Worker — Chrome/Brave (Manifest V3).
 *
 * KEY DIFFERENCES from Firefox MV2 background page:
 *  - Service Workers are ephemeral: they spin up on demand and shut down
 *    when idle. In-memory state is lost between invocations.
 *  - We use chrome.storage.session (in-memory, cleared on browser close)
 *    to persist per-tab video state across service worker restarts.
 *  - Use chrome.action instead of chrome.browserAction (MV3 rename).
 *  - connectNative works the same way; chrome.runtime.connectNative.
 *
 * Responsibilities:
 *  - Receive messages from content scripts about YouTube video detection
 *  - Persist per-tab video state in chrome.storage.session
 *  - Update the action badge
 *  - Handle popup requests: get_video_info, get_formats, download
 *  - Bridge to native host via chrome.runtime.connectNative
 */

"use strict";

// ─── Storage Key Helpers ──────────────────────────────────────────────────────

function tabKey(tabId) {
  return "tab_" + tabId;
}

async function getTabVideoInfo(tabId) {
  const key = tabKey(tabId);
  const result = await chrome.storage.session.get(key);
  return result[key] || null;
}

async function setTabVideoInfo(tabId, info) {
  const key = tabKey(tabId);
  await chrome.storage.session.set({ [key]: info });
}

async function clearTabVideoInfo(tabId) {
  const key = tabKey(tabId);
  await chrome.storage.session.remove(key);
}

// ─── Badge Helpers ────────────────────────────────────────────────────────────

function setBadgeActive(tabId) {
  chrome.action.setBadgeText({ text: "\u25BC", tabId });
  chrome.action.setBadgeBackgroundColor({ color: "#e74c3c", tabId });
}

function clearBadge(tabId) {
  chrome.action.setBadgeText({ text: "", tabId });
}

// ─── Content Script Message Listener ─────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab && sender.tab.id;

  // ── yt_video_detected ───────────────────────────────────────────────────────
  if (message.action === "yt_video_detected" && tabId != null) {
    const info = {
      url: message.url,
      title: message.title,
      videoId: message.videoId,
    };
    setTabVideoInfo(tabId, info).then(() => {
      setBadgeActive(tabId);
    });
    return false; // synchronous handling
  }

  // ── yt_video_left ───────────────────────────────────────────────────────────
  if (message.action === "yt_video_left" && tabId != null) {
    clearTabVideoInfo(tabId).then(() => {
      clearBadge(tabId);
    });
    return false;
  }

  // ── get_video_info (from popup) ──────────────────────────────────────────────
  if (message.action === "get_video_info") {
    chrome.tabs.query({ active: true, currentWindow: true }).then(async (tabs) => {
      if (!tabs.length) {
        sendResponse({ videoInfo: null });
        return;
      }
      const info = await getTabVideoInfo(tabs[0].id);
      sendResponse({ videoInfo: info });
    });
    return true; // async response
  }

  // ── get_formats (from popup) ─────────────────────────────────────────────────
  if (message.action === "get_formats") {
    sendNativeMessage({ action: "get_formats", url: message.url })
      .then((response) => sendResponse(response))
      .catch((err) => sendResponse({ error: err.message }));
    return true; // async
  }

  // ── download (from popup) ────────────────────────────────────────────────────
  if (message.action === "download") {
    sendNativeMessage({
      action: "download",
      url: message.url,
      format_id: message.format_id,
      title: message.title,
      save_path: message.save_path || "",
    })
      .then((response) => sendResponse(response))
      .catch((err) => sendResponse({ error: err.message }));
    return true; // async
  }

  return false;
});

// ─── Tab Cleanup ──────────────────────────────────────────────────────────────

chrome.tabs.onRemoved.addListener((tabId) => {
  clearTabVideoInfo(tabId);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.url && !changeInfo.url.includes("youtube.com/watch")) {
    clearTabVideoInfo(tabId).then(() => {
      clearBadge(tabId);
    });
  }
});

// ─── Native Messaging Helper ──────────────────────────────────────────────────

/**
 * Send a single JSON message to native_host.py and resolve with its response.
 * Uses chrome.runtime.connectNative for a port-based connection,
 * then immediately sends the message and waits for one response.
 *
 * @param {Object} msg
 * @returns {Promise<Object>}
 */
function sendNativeMessage(msg) {
  return new Promise((resolve, reject) => {
    let port;
    let settled = false;

    function settle(fn, value) {
      if (!settled) {
        settled = true;
        fn(value);
      }
    }

    try {
      port = chrome.runtime.connectNative("com.ydm.native_host");
    } catch (err) {
      reject(new Error("Could not connect to native host: " + err.message));
      return;
    }

    const timeout = setTimeout(() => {
      port.disconnect();
      settle(reject, new Error("Native host timed out after 15 seconds."));
    }, 15000);

    port.onMessage.addListener((response) => {
      clearTimeout(timeout);
      port.disconnect();
      settle(resolve, response);
    });

    port.onDisconnect.addListener(() => {
      clearTimeout(timeout);
      const lastErr = chrome.runtime.lastError;
      settle(
        reject,
        new Error(lastErr ? lastErr.message : "Native host disconnected unexpectedly.")
      );
    });

    port.postMessage(msg);
  });
}
