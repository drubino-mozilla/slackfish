"""Slackfish MCP server.

Exposes cached Slack data to the Cursor agent via MCP tools.
Reads from the shared cache populated by the native messaging host.
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from cache import SlackfishCache

mcp = FastMCP("slackfish")
cache = SlackfishCache()

WORKSPACE_URL = "https://mozilla.slack.com"


def resolve_user(user_id):
    if not user_id:
        return "unknown"
    user = cache.get_user(user_id)
    if user:
        return user.get("display_name") or user.get("real_name") or user.get("name") or user_id
    return user_id


def make_permalink(channel_id, ts):
    ts_clean = ts.replace(".", "")
    return f"{WORKSPACE_URL}/archives/{channel_id}/p{ts_clean}"


def format_message(msg, channel_id=None):
    ts = float(msg["ts"])
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    time_str = dt.strftime("%Y-%m-%d %H:%M")
    user_name = resolve_user(msg.get("user"))
    text = msg.get("text", "")
    cid = channel_id or msg.get("channel_id", "")

    # Resolve <@U123> mentions in text
    import re
    def replace_mention(m):
        return f"@{resolve_user(m.group(1))}"
    text = re.sub(r"<@([A-Z0-9]+)>", replace_mention, text)

    line = f"[{time_str}] @{user_name}: {text}"
    if msg.get("reply_count"):
        line += f" ({msg['reply_count']} replies)"
    if cid:
        line += f"  {make_permalink(cid, msg['ts'])}"
    return line


def resolve_channel_id(channel):
    """Resolve a #channel-name to an ID, or pass through an ID."""
    if channel.startswith("C") or channel.startswith("D") or channel.startswith("G"):
        return channel
    name = channel.lstrip("#").lower()
    channels = cache.get_channels()
    for cid, ch in channels.items():
        if (ch.get("name") or "").lower() == name:
            return cid
    return None


def parse_since(since_str):
    """Parse a human-readable time reference to a Unix timestamp."""
    now = time.time()
    s = since_str.lower().strip()
    if s == "today":
        dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
        return dt.timestamp()
    if s == "yesterday":
        dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0) - timedelta(days=1)
        return dt.timestamp()
    for unit, mult in [("h", 3600), ("d", 86400), ("w", 604800)]:
        if s.endswith(unit):
            try:
                n = int(s[:-1])
                return now - n * mult
            except ValueError:
                pass
    try:
        return float(since_str)
    except ValueError:
        return now - 86400  # default: last 24h


@mcp.tool()
def slack_list_channels() -> str:
    """List all cached Slack channels with their names, types, and last activity."""
    channels = cache.get_channels()
    if not channels:
        return "No channels cached yet. Browse some Slack channels in Firefox to populate the cache."

    lines = []
    for cid, ch in sorted(channels.items(), key=lambda x: x[1].get("name") or ""):
        name = ch.get("name") or cid
        prefix = ""
        if ch.get("is_im"):
            other = resolve_user(ch.get("user"))
            prefix = "DM"
            name = other
        elif ch.get("is_mpim"):
            prefix = "Group DM"
        elif ch.get("is_private"):
            prefix = "Private"
        else:
            prefix = "Channel"
            name = f"#{name}"

        parts = [f"{prefix}: {name} ({cid})"]
        if ch.get("num_members"):
            parts.append(f"{ch['num_members']} members")
        if ch.get("topic"):
            parts.append(f"topic: {ch['topic'][:60]}")
        lines.append(" | ".join(parts))

    return f"{len(channels)} cached channels:\n" + "\n".join(lines)


@mcp.tool()
def slack_get_messages(channel: str, since: str = "all", limit: int = 50) -> str:
    """Get cached messages from a Slack channel or DM.

    Args:
        channel: Channel name (e.g. '#fx-growth') or ID (e.g. 'C06HGPTM05C')
        since: Time filter. 'all' (default) returns everything cached. Also accepts '24h', '7d', '2h', 'today', 'yesterday', or a Unix timestamp.
        limit: Maximum number of messages to return (default 50).
    """
    channel_id = resolve_channel_id(channel)
    if not channel_id:
        return f"Channel '{channel}' not found in cache. Available channels: use slack_list_channels to see what's cached."

    since_ts = parse_since(since) if since != "all" else 0
    messages = cache.get_messages(channel_id, since=since_ts, limit=limit)
    if not messages:
        channels = cache.get_channels()
        ch = channels.get(channel_id, {})
        name = ch.get("name") or channel_id
        return f"No messages cached for #{name}. Browse the channel in Firefox to populate the cache."

    lines = [format_message(m, channel_id) for m in messages]
    return f"{len(messages)} messages:\n" + "\n".join(lines)


@mcp.tool()
def slack_get_thread(channel: str, thread_ts: str) -> str:
    """Get all replies in a Slack thread.

    Args:
        channel: Channel name or ID where the thread lives.
        thread_ts: The timestamp of the parent message (e.g. '1775692213.387399').
    """
    channel_id = resolve_channel_id(channel)
    if not channel_id:
        return f"Channel '{channel}' not found in cache."

    messages = cache.get_thread(channel_id, thread_ts)
    if not messages:
        return f"Thread {thread_ts} not cached. Open the thread in Firefox to capture it."

    lines = [format_message(m, channel_id) for m in messages]
    return f"{len(messages)} messages in thread:\n" + "\n".join(lines)


@mcp.tool()
def slack_search(query: str, limit: int = 30) -> str:
    """Search across all cached Slack messages.

    Args:
        query: Text to search for (case-insensitive substring match).
        limit: Maximum results to return (default 30).
    """
    results = cache.search_messages(query, limit=limit)
    if not results:
        return f"No cached messages matching '{query}'. The search only covers channels you've browsed in Firefox."

    channels = cache.get_channels()
    lines = []
    for msg in results:
        cid = msg.get("channel_id", "")
        ch = channels.get(cid, {})
        ch_name = ch.get("name") or cid
        if not ch.get("is_im") and not ch.get("is_mpim"):
            ch_name = f"#{ch_name}"
        lines.append(f"[{ch_name}] {format_message(msg, cid)}")

    return f"{len(results)} results for '{query}':\n" + "\n".join(lines)


@mcp.tool()
def slack_get_context() -> str:
    """Get the Slack channel/DM the user is currently viewing in Firefox."""
    ctx = cache.get_context()
    if not ctx or not ctx.get("channel_id"):
        return "No active Slack context. The user may not have Slack open in Firefox."

    channel_id = ctx["channel_id"]
    channels = cache.get_channels()
    ch = channels.get(channel_id, {})
    name = ch.get("name") or channel_id
    if ch.get("is_im"):
        name = f"DM with {resolve_user(ch.get('user'))}"
    elif not ch.get("is_mpim"):
        name = f"#{name}"

    ts = ctx.get("timestamp", 0)
    if ts:
        age = time.time() - ts / 1000  # JS timestamp is ms
        if age < 60:
            age_str = f"{int(age)}s ago"
        elif age < 3600:
            age_str = f"{int(age/60)}m ago"
        else:
            age_str = f"{int(age/3600)}h ago"
    else:
        age_str = "unknown"

    return f"Currently viewing: {name} ({channel_id}) — last navigation {age_str}\nURL: {ctx.get('url', 'unknown')}"


@mcp.tool()
def slack_get_stats() -> str:
    """Get Slackfish cache statistics — how much data has been captured."""
    stats = cache.get_stats()
    return (
        f"Slackfish cache stats:\n"
        f"  Channels cached: {stats['channels_cached']}\n"
        f"  Users cached: {stats['users_cached']}\n"
        f"  Channels with messages: {stats['channels_with_messages']}\n"
        f"  Total messages: {stats['total_messages']}"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
