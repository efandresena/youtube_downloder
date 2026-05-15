/**
 * YDM Background Script — Firefox (Manifest V2, persistent background page).
 *
 * Responsibilities:
 *  - Receive messages from content scripts about YouTube video detection
 *  - Maintain per-tab video state (currentTabVideoInfo)
 *  - Update the browser action badge
 *  - Handle popup requests: get_video_info, get_formats, download
 *  - Bridge to native host via browser.runtime.connectNative
 */

"use strict";

// ─── Per-tab State ────────────────────────────────────────────────────────────
// Map<tabId, { url, title, videoId }>
const tabVideoInfo = new Map();

// ─── Badge Helpers ────────────────────────────────────────────────────────────

function setBadgeActive(tabId) {
  browser.browserAction.setBadgeText({ text: "▼", tabId });
  browser.browserAction.setBadgeBackgroundColor({ color: "#e74c3c", tabId });
}

function clearBadge(tabId) {
  browser.browserAction.setBadgeText({ text: "", tabId });
}

// ─── Content Script Message Listener ─────────────────────────────────────────

browser.runtime.onMessage.addListener((message, sender) => {
  const tabId = sender.tab && sender.tab.id;
  if (tabId === undefined || tabId === null) return;

  if (message.action === "yt_video_detected") {
    tabVideoInfo.set(tabId, {
      url: message.url,
      title: message.title,
      videoId: message.videoId,
    });
    setBadgeActive(tabId);
    return;
  }

  if (message.action === "yt_video_left") {
    tabVideoInfo.delete(tabId);
    clearBadge(tabId);
    return;
  }
});

// ─── Tab Cleanup ──────────────────────────────────────────────────────────────

browser.tabs.onRemoved.addListener((tabId) => {
  tabVideoInfo.delete(tabId);
});

browser.tabs.onUpdated.addListener((tabId, changeInfo) => {
  // If the tab navigated away (URL changed to non-YT), clean up
  if (changeInfo.url && !changeInfo.url.includes("youtube.com/watch")) {
    if (tabVideoInfo.has(tabId)) {
      tabVideoInfo.delete(tabId);
      clearBadge(tabId);
    }
  }
});

// ─── Native Messaging Helper ──────────────────────────────────────────────────

/**
 * Send a single message to the native host and resolve with the response.
 * Opens a new port per request (simple request/response pattern).
 *
 * @param {Object} msg  The JSON payload to send to native_host.py
 * @returns {Promise<Object>}
 */
function sendNativeMessage(msg) {
  return new Promise((resolve, reject) => {
    let port;
    try {
      port = browser.runtime.connectNative("com.ydm.native_host");
    } catch (err) {
      reject(new Error("Could not connect to native host: " + err.message));
      return;
    }

    const timeout = setTimeout(() => {
      port.disconnect();
      reject(new Error("Native host timed out after 30 seconds."));
    }, 30000);

    port.onMessage.addListener((response) => {
      clearTimeout(timeout);
      port.disconnect();
      resolve(response);
    });

    port.onDisconnect.addListener(() => {
      clearTimeout(timeout);
      const err = browser.runtime.lastError;
      // Only reject if we haven't resolved yet (disconnect before response)
      reject(new Error(err ? err.message : "Native host disconnected unexpectedly."));
    });

    port.postMessage(msg);
  });
}

// ─── Popup Message Listener ───────────────────────────────────────────────────

browser.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  // ── get_video_info ──────────────────────────────────────────────────────────
  if (message.action === "get_video_info") {
    browser.tabs.query({ active: true, currentWindow: true }).then((tabs) => {
      if (!tabs.length) {
        sendResponse({ error: "No active tab found." });
        return;
      }
      const tabId = tabs[0].id;
      const info = tabVideoInfo.get(tabId) || null;
      sendResponse({ videoInfo: info });
    });
    return true; // async
  }

  // ── get_formats ─────────────────────────────────────────────────────────────
  if (message.action === "get_formats") {
    sendNativeMessage({ action: "get_formats", url: message.url })
      .then((response) => sendResponse(response))
      .catch((err) => sendResponse({ error: err.message }));
    return true; // async
  }

  // ── download ─────────────────────────────────────────────────────────────────
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
});
