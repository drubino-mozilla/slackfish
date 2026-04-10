# Slackfish

A Firefox extension that passively captures Slack data as you browse and exposes it to AI agents via MCP.

## How It Works

Slackfish intercepts Slack's own API responses as they flow through Firefox — no external tokens, no admin approval, no detectable footprint. When you browse channels, read DMs, or open threads, the extension captures the structured JSON that Slack's servers return and caches it locally.

A Python MCP server then serves this cached data to your AI assistant (Cursor, Claude Code, etc.).

## Architecture

```
Firefox (Slack tab)
  └── background.js (webRequest interceptor)
        └── Native Messaging (stdin/stdout)
              └── slackfish_host.py (caches to ~/.slackfish/)

Cursor Agent
  └── MCP (stdio)
        └── slackfish_mcp.py (reads from ~/.slackfish/)
```

## Setup

### 1. Install the native messaging host

```powershell
python host/install.py
```

### 2. Load the extension in Firefox

1. Open `about:debugging#/runtime/this-firefox`
2. Click "Load Temporary Add-on"
3. Select `manifest.json` from this directory

### 3. Add the MCP server to Cursor

Add to `~/.cursor/mcp.json`:

```json
{
  "slackfish": {
    "command": "python",
    "args": ["C:/mozilla-source/slackfish/host/slackfish_mcp.py"]
  }
}
```

### 4. Browse Slack

Open Slack in Firefox and browse normally. The extension passively captures data as you navigate. The more you browse, the more data is available to the agent.

## MCP Tools

| Tool | Description |
|------|-------------|
| `slack_list_channels` | List all cached channels |
| `slack_get_messages` | Get recent messages from a channel or DM |
| `slack_get_thread` | Get replies in a thread |
| `slack_search` | Full-text search across cached messages |
| `slack_get_context` | What channel the user is currently viewing |
| `slack_get_stats` | Cache statistics |

## Limitations

- Only captures what you browse — channels you haven't opened aren't cached
- Thread replies require opening the thread in Slack
- No real-time push — the agent reads cached data, not live data
- Extension must be loaded in Firefox (temporary add-on for now)

## Files

```
manifest.json       Extension manifest (MV2)
background.js       webRequest interceptor + native messaging
content.js          Navigation tracking
host/
  slackfish_host.py Native messaging host
  slackfish_mcp.py  MCP server
  cache.py          Shared cache module
  protocol.py       Native messaging wire protocol
  install.py        Windows registry setup
  slackfish.bat     Native host launcher
  slackfish.json    Native messaging manifest
```
