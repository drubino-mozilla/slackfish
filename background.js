console.log("[Slackfish BG] Script starting...");

// Clear Slack's IndexedDB on startup so the web client can't serve
// cached responses — forces fresh API fetches with full payloads.
browser.browsingData.remove(
  { hostnames: ["app.slack.com", "edgeapi.slack.com"] },
  { indexedDB: true, serviceWorkers: true }
).then(() => {
  console.log("[Slackfish] Cleared Slack IndexedDB + service workers");
}).catch(e => {
  console.warn("[Slackfish] browsingData.remove failed:", e);
});

/* Slackfish background script.
 *
 * Passively intercepts Slack API responses flowing through the browser
 * and forwards structured data to the native messaging host.
 */

const SLACK_API_PATTERNS = [
  "https://edgeapi.slack.com/cache/*",
  "https://slack.com/api/*",
  "https://*.slack.com/api/*",
];

const INTERESTING_METHODS = new Set([
  "conversations.history",
  "conversations.view",
  "conversations.replies",
  "conversations.info",
  "conversations.list",
  "im.history",
  "mpim.history",
  "channels.info",
  "users.info",
  "users.list",
  "users.conversations",
  "client.counts",
  "client.boot",
]);

let nativePort = null;
let currentContext = { channelId: null, url: null };

function connectNative() {
  try {
    nativePort = browser.runtime.connectNative("slackfish");
    nativePort.onMessage.addListener(msg => {
      console.log("[Slackfish] From native host:", msg);
    });
    nativePort.onDisconnect.addListener(() => {
      console.log("[Slackfish] Native host disconnected:", browser.runtime.lastError);
      nativePort = null;
    });
    console.log("[Slackfish] Connected to native host");
  } catch (e) {
    console.log("[Slackfish] Native host not available:", e.message);
    nativePort = null;
  }
}

function sendToHost(payload) {
  if (nativePort) {
    try {
      nativePort.postMessage(payload);
    } catch (e) {
      console.warn("[Slackfish] Failed to send to native host:", e.message);
      nativePort = null;
    }
  }
}

function sendLog(level, message) {
  console.log(`[Slackfish] ${level}: ${message}`);
  sendToHost({ type: "log", level, message });
}

function extractApiMethod(url) {
  // https://mozilla.enterprise.slack.com/api/conversations.history?... -> conversations.history
  let match = url.match(/\/api\/([a-zA-Z_.]+)/);
  if (match) return match[1];

  // https://edgeapi.slack.com/cache/E07DB2PSS3W/users/info -> users.info
  match = url.match(/\/cache\/[^/]+\/([a-zA-Z]+)\/([a-zA-Z]+)/);
  if (match) return `${match[1]}.${match[2]}`;

  // https://edgeapi.slack.com/cache/E07DB2PSS3W/users/list -> users
  match = url.match(/\/cache\/[^/]+\/([a-zA-Z_.]+)/);
  if (match) return match[1];

  return null;
}

function extractChannelFromUrl(url) {
  // Slack sends channel= as a POST form param, but it may also appear in query string
  const match = url.match(/[?&]channel=([A-Z][A-Z0-9]+)/);
  return match ? match[1] : null;
}

function processApiResponse(method, url, responseText, postChannel) {
  let data;
  try {
    data = JSON.parse(responseText);
  } catch (e) {
    return;
  }

  if (!data.ok && data.ok !== undefined) {
    sendLog("debug", `${method}: ok=false, skipping`);
    return;
  }

  const keys = Object.keys(data);
  sendLog("info", `Captured ${method}: keys=[${keys.join(",")}] messages=${data.messages?.length ?? "none"} results=${data.results ? typeof data.results : "none"}`);

  if (data.messages) {
    const channelId = data.channel_id || data.channel
      || postChannel
      || extractChannelFromUrl(url)
      || extractChannelFromMessages(data.messages)
      || currentContext.channelId;
    if (channelId) {
      const isThread = data.messages.length > 0 && data.messages[0].thread_ts
        && method.includes("replies");
      const payload = {
        type: "messages",
        method,
        channel_id: channelId,
        messages: data.messages.map(normalizeMessage),
        thread_ts: isThread ? data.messages[0].thread_ts : null,
        has_more: data.has_more || false,
      };
      sendLog("info", `-> ${payload.messages.length} messages for ${channelId} (src: ${data.channel_id ? "resp" : postChannel ? "post" : extractChannelFromUrl(url) ? "url" : "ctx"})`);
      sendToHost(payload);
    } else {
      sendLog("warning", `Messages found but no channel ID for ${method}. Keys: ${Object.keys(data)}`);
    }
  }

  // Edge API wraps data in "results" which can be an array or an object keyed by ID
  if (data.results && !data.messages) {
    const results = data.results;
    if (Array.isArray(results)) {
      processResultsArray(method, results);
    } else if (typeof results === "object") {
      processResultsObject(method, results);
    }
  }

  if (data.channels || data.ims || data.groups) {
    const channels = [
      ...(data.channels || []),
      ...(data.ims || []),
      ...(data.groups || []),
    ];
    if (channels.length > 0) {
      const payload = {
        type: "channels",
        channels: channels.map(normalizeChannel),
      };
      console.log(`[Slackfish] -> ${payload.channels.length} channels`);
      sendToHost(payload);
    }
  }

  if (data.members || data.users) {
    const users = data.members || data.users || [];
    if (users.length > 0) {
      const payload = {
        type: "users",
        users: users.map(normalizeUser),
      };
      console.log(`[Slackfish] -> ${payload.users.length} users`);
      sendToHost(payload);
    }
  }

  if (data.user && data.user.id) {
    sendToHost({
      type: "users",
      users: [normalizeUser(data.user)],
    });
  }

  if (data.channel && typeof data.channel === "object" && data.channel.id) {
    sendToHost({
      type: "channels",
      channels: [normalizeChannel(data.channel)],
    });
  }

  // client.boot contains a wealth of initial data
  if (method === "client.boot") {
    processBootData(data);
  }
}

function processResultsArray(method, results) {
  const withId = results.filter(r => r && r.id);
  if (withId.length === 0) return;
  const sample = withId[0];

  if (sample.name !== undefined && (sample.is_channel !== undefined || sample.is_im !== undefined || sample.is_mpim !== undefined)) {
    const payload = { type: "channels", channels: withId.map(normalizeChannel) };
    console.log(`[Slackfish] -> ${payload.channels.length} channels (from results)`);
    sendToHost(payload);
  } else if (sample.real_name !== undefined || sample.profile !== undefined) {
    const payload = { type: "users", users: withId.map(normalizeUser) };
    console.log(`[Slackfish] -> ${payload.users.length} users (from results)`);
    sendToHost(payload);
  }
}

function processResultsObject(method, results) {
  // Edge API returns {id: {data}} where the key IS the id
  const entries = Object.entries(results)
    .filter(([k, v]) => v && typeof v === "object")
    .map(([k, v]) => {
      if (!v.id) v.id = k;
      return v;
    });
  if (entries.length === 0) return;
  const s = entries[0];

  if (s.real_name !== undefined || s.profile !== undefined) {
    const payload = { type: "users", users: entries.map(normalizeUser) };
    console.log(`[Slackfish] -> ${payload.users.length} users (from results obj)`);
    sendToHost(payload);
  } else if (s.name !== undefined || s.name_normalized !== undefined || s.is_im !== undefined || s.is_channel !== undefined) {
    const payload = { type: "channels", channels: entries.map(normalizeChannel) };
    console.log(`[Slackfish] -> ${payload.channels.length} channels (from results obj)`);
    sendToHost(payload);
  }
}

function processBootData(data) {
  if (data.self) {
    sendToHost({
      type: "self",
      user_id: data.self.id,
      user_name: data.self.name,
      team_id: data.team?.id,
      team_name: data.team?.name,
    });
  }
  if (data.users && data.users.length > 0) {
    sendToHost({
      type: "users",
      users: data.users.map(normalizeUser),
    });
  }
  if (data.channels && data.channels.length > 0) {
    sendToHost({
      type: "channels",
      channels: data.channels.map(normalizeChannel),
    });
  }
  if (data.ims && data.ims.length > 0) {
    sendToHost({
      type: "channels",
      channels: data.ims.map(normalizeChannel),
    });
  }
}

function normalizeMessage(msg) {
  return {
    ts: msg.ts,
    user: msg.user || msg.bot_id || null,
    text: msg.text || "",
    thread_ts: msg.thread_ts || null,
    reply_count: msg.reply_count || 0,
    reactions: (msg.reactions || []).map(r => ({
      name: r.name,
      count: r.count,
    })),
    files: (msg.files || []).map(f => ({
      name: f.name,
      mimetype: f.mimetype,
      url: f.url_private || f.permalink || null,
    })),
    subtype: msg.subtype || null,
    bot_id: msg.bot_id || null,
    username: msg.username || null,
  };
}

function normalizeChannel(ch) {
  return {
    id: ch.id,
    name: ch.name || ch.name_normalized || null,
    is_im: ch.is_im || false,
    is_mpim: ch.is_mpim || false,
    is_private: ch.is_private || ch.is_group || false,
    user: ch.user || null, // for DMs, the other user's ID
    topic: ch.topic?.value || null,
    purpose: ch.purpose?.value || null,
    num_members: ch.num_members || null,
    last_read: ch.last_read || null,
  };
}

function normalizeUser(u) {
  return {
    id: u.id,
    name: u.name || null,
    real_name: u.real_name || u.profile?.real_name || null,
    display_name: u.profile?.display_name || null,
    email: u.profile?.email || null,
    title: u.profile?.title || null,
    is_bot: u.is_bot || false,
  };
}

function extractChannelFromMessages(messages) {
  // Some responses include channel info in the message metadata
  for (const msg of messages) {
    if (msg.channel) return msg.channel;
  }
  return null;
}

// --- webRequest interception ---

browser.webRequest.onBeforeRequest.addListener(
  details => {
    const method = extractApiMethod(details.url);

    if (!method) return {};

    // Capture all methods that might contain messages or useful data
    const shouldCapture = INTERESTING_METHODS.has(method) ||
      method.startsWith("conversations.") ||
      method.startsWith("channels.") ||
      method.startsWith("users.") ||
      method.startsWith("im.") ||
      method.startsWith("mpim.") ||
      method.startsWith("client.");

    if (!shouldCapture) return {};

    

    // Extract channel from POST body (form data or raw JSON)
    let postChannel = null;
    if (details.requestBody) {
      if (details.requestBody.formData && details.requestBody.formData.channel) {
        postChannel = details.requestBody.formData.channel[0];
      } else if (details.requestBody.raw && details.requestBody.raw.length > 0) {
        try {
          const decoder = new TextDecoder("utf-8");
          const bodyBytes = details.requestBody.raw.map(r => r.bytes ? new Uint8Array(r.bytes) : new Uint8Array(0));
          const totalLen = bodyBytes.reduce((s, b) => s + b.length, 0);
          const merged = new Uint8Array(totalLen);
          let offset = 0;
          for (const b of bodyBytes) { merged.set(b, offset); offset += b.length; }
          const bodyText = decoder.decode(merged);
          // Try JSON body
          try {
            const bodyJson = JSON.parse(bodyText);
            if (bodyJson.channel) postChannel = bodyJson.channel;
          } catch (_) {
            // Try URL-encoded body
            const params = new URLSearchParams(bodyText);
            if (params.get("channel")) postChannel = params.get("channel");
          }
        } catch (_) {}
      }
    }
    let filter;
    try {
      filter = browser.webRequest.filterResponseData(details.requestId);
    } catch (e) {
      sendLog("error", `filterResponseData failed for ${method}: ${e.message}`);
      return {};
    }
    const decoder = new TextDecoder("utf-8");
    const chunks = [];

    filter.ondata = event => {
      chunks.push(decoder.decode(event.data, { stream: true }));
      filter.write(event.data);
    };

    filter.onstop = () => {
      const responseText = chunks.join("");
      
      try {
        processApiResponse(method, details.url, responseText, postChannel);
      } catch (e) {
        sendLog("error", `Error processing ${method}: ${e.message}\n${e.stack}`);
      }
      filter.close();
    };

    filter.onerror = () => {
      sendLog("error", `Filter error for ${method}: ${filter.error}`);
    };

    return {};
  },
  { urls: SLACK_API_PATTERNS },
  ["blocking", "requestBody"]
);

// --- WebSocket event processing ---

function processWsEvent(event) {
  const t = event.type;

  if (t === "message" && event.channel) {
    // Ignore subtypes that aren't real messages (typing, etc.)
    const skip = new Set(["message_changed", "message_deleted", "channel_join", "channel_leave"]);
    if (event.subtype && skip.has(event.subtype)) {
      if (event.subtype === "message_changed" && event.message) {
        // Edited message — update with new content
        const edited = {
          ...event.message,
          channel: event.channel,
        };
        sendToHost({
          type: "messages",
          method: "websocket",
          channel_id: event.channel,
          messages: [normalizeMessage(edited)],
          thread_ts: edited.thread_ts || null,
          has_more: false,
        });
        sendLog("info", `WS: edited message in ${event.channel}`);
      }
      return;
    }

    sendToHost({
      type: "messages",
      method: "websocket",
      channel_id: event.channel,
      messages: [normalizeMessage(event)],
      thread_ts: event.thread_ts || null,
      has_more: false,
    });
    sendLog("info", `WS: message in ${event.channel} from ${event.user || "bot"}`);
  }

  if (t === "reaction_added" && event.item) {
    sendLog("debug", `WS: reaction ${event.reaction} in ${event.item.channel}`);
  }

  if (t === "user_change" && event.user) {
    sendToHost({
      type: "users",
      users: [normalizeUser(event.user)],
    });
  }
}

// --- Listen for messages from content script ---

browser.runtime.onMessage.addListener((msg, sender) => {
  if (msg.type === "navigation") {
    currentContext = {
      channelId: msg.channelId,
      url: msg.url,
      timestamp: Date.now(),
    };
    console.log("[Slackfish] Context updated:", currentContext);
    sendToHost({ type: "context", ...currentContext });
  } else if (msg.type === "ws_event") {
    try {
      processWsEvent(msg.payload);
    } catch (e) {
      sendLog("error", `WS event error: ${e.message}`);
    }
  }
});

// Try connecting to native host on startup
connectNative();

console.log("[Slackfish] Background script loaded, intercepting Slack API responses");
