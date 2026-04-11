/* Slackfish WebSocket interception (document_start).
 *
 * Patches the page's WebSocket constructor to observe incoming Slack
 * real-time events and forward them to the background script.
 */

const pageWindow = window.wrappedJSObject;
const OrigWebSocket = pageWindow.WebSocket;

function onWsMessage(url, event) {
  if (typeof event.data !== "string") return;
  try {
    const msg = JSON.parse(event.data);
    if (!msg.type) return;
    browser.runtime.sendMessage({
      type: "ws_event",
      payload: msg,
    });
  } catch (e) {}
}

const patchedConstruct = exportFunction(function (url, protocols) {
  const ws =
    protocols !== undefined
      ? new OrigWebSocket(url, protocols)
      : new OrigWebSocket(url);

  if (typeof url === "string" && url.includes("slack.com")) {
    console.log("[Slackfish] Observing WebSocket:", url);
    const handler = exportFunction(function (event) {
      onWsMessage(url, event);
    }, pageWindow);
    ws.addEventListener("message", handler);
  }

  return ws;
}, pageWindow);

patchedConstruct.prototype = OrigWebSocket.prototype;
for (const key of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) {
  if (key in OrigWebSocket) {
    patchedConstruct[key] = OrigWebSocket[key];
  }
}

pageWindow.WebSocket = patchedConstruct;

console.log("[Slackfish] WebSocket constructor patched");
