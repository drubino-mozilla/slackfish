/* Slackfish content script.
 *
 * Tracks Slack SPA navigation and sends the current channel context
 * to the background script.
 */

console.log("[Slackfish] Content script loaded on:", window.location.href);

function parseChannelId(url) {
  // URLs like /client/E07DB2PSS3W/C1234567890 or /client/E07DB2PSS3W/D1234567890
  const match = url.match(/\/client\/[A-Z0-9]+\/([A-Z][A-Z0-9]+)/);
  return match ? match[1] : null;
}

function notifyNavigation(method, url) {
  const channelId = parseChannelId(url);
  console.log(`[Slackfish] Navigation (${method}): ${url} -> channel: ${channelId}`);
  browser.runtime.sendMessage({
    type: "navigation",
    method,
    url,
    channelId,
  });
}

// Patch history.pushState/replaceState via wrappedJSObject (bypasses CSP)
const pageHistory = window.wrappedJSObject.history;
const origPushState = pageHistory.pushState.bind(pageHistory);
const origReplaceState = pageHistory.replaceState.bind(pageHistory);

exportFunction(function (state, title, url) {
  origPushState(state, title, url);
  notifyNavigation("pushState", url || window.location.href);
}, pageHistory, { defineAs: "pushState" });

exportFunction(function (state, title, url) {
  origReplaceState(state, title, url);
  notifyNavigation("replaceState", url || window.location.href);
}, pageHistory, { defineAs: "replaceState" });

window.addEventListener("popstate", () => {
  notifyNavigation("popstate", window.location.href);
});

// Send initial context
notifyNavigation("load", window.location.href);
