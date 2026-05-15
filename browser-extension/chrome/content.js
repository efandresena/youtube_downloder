/**
 * YDM Content Script — injected into all YouTube pages.
 * Identical to the Firefox version; uses chrome.* API which is available in
 * both Chrome/Brave (natively) and Firefox (also supports chrome.* alias).
 *
 * Responsibilities:
 *  - Detect when the user is on a YouTube video watch page (/watch?v=...)
 *  - Handle YouTube's SPA navigation (yt-navigate-finish event + MutationObserver)
 *  - Extract video ID, URL, and title
 *  - Notify the background script via runtime messages
 */

"use strict";

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Extract the YouTube video ID from a given URL string.
 * Returns null if the URL is not a /watch?v= page.
 */
function extractVideoId(url) {
  try {
    const u = new URL(url);
    if (u.pathname === "/watch") {
      return u.searchParams.get("v") || null;
    }
  } catch (_) {
    // malformed URL – ignore
  }
  return null;
}

/**
 * Grab the best available page title.
 * YouTube sets document.title to "Video Title - YouTube"; strip the suffix.
 */
function getCleanTitle() {
  const raw = document.title || "";
  return raw.replace(/\s*-\s*YouTube\s*$/, "").trim();
}

// ─── State ────────────────────────────────────────────────────────────────────

let lastReportedVideoId = null;

// ─── Core Detection Logic ─────────────────────────────────────────────────────

function checkAndNotify() {
  const videoId = extractVideoId(window.location.href);

  if (videoId && videoId !== lastReportedVideoId) {
    // We just landed on (or navigated to) a new video page
    lastReportedVideoId = videoId;

    const payload = {
      action: "yt_video_detected",
      url: window.location.href,
      title: getCleanTitle(),
      videoId: videoId,
    };

    chrome.runtime.sendMessage(payload, () => {
      // Suppress errors if background service worker is waking up
      void chrome.runtime.lastError;
    });

  } else if (!videoId && lastReportedVideoId !== null) {
    // We left a video page
    lastReportedVideoId = null;

    chrome.runtime.sendMessage({ action: "yt_video_left" }, () => {
      void chrome.runtime.lastError;
    });
  }
}

// ─── Title Observer ───────────────────────────────────────────────────────────
// YouTube updates document.title after a SPA navigation settles; observing it
// gives us a reliable hook without polling.

const titleElement = document.querySelector("head > title") || document.head;
const titleObserver = new MutationObserver(() => {
  checkAndNotify();
});

titleObserver.observe(titleElement, {
  subtree: true,
  childList: true,
  characterData: true,
});

// ─── YouTube SPA Navigation Event ─────────────────────────────────────────────
// yt-navigate-finish fires on every YouTube SPA transition.

window.addEventListener("yt-navigate-finish", () => {
  // Give the DOM a short moment to settle title / URL
  setTimeout(checkAndNotify, 150);
});

// ─── popstate (back/forward nav) ─────────────────────────────────────────────

window.addEventListener("popstate", () => {
  setTimeout(checkAndNotify, 150);
});

// ─── Initial Page Load ────────────────────────────────────────────────────────

// Run immediately in case the user lands directly on a watch page.
checkAndNotify();
