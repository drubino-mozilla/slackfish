"""Slackfish native messaging host.

Receives structured Slack data from the Firefox extension via native
messaging and writes it to the local cache.
"""

import json
import sys
import os
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol import read_message, write_message
from cache import SlackfishCache

SLACKFISH_DIR = os.path.join(os.path.expanduser("~"), ".slackfish")
LOG_PATH = os.path.join(SLACKFISH_DIR, "host.log")
EVENTS_PATH = os.path.join(SLACKFISH_DIR, "events.jsonl")
EVENTS_MAX_LINES = 4000
EVENTS_KEEP_LINES = 2000

os.makedirs(SLACKFISH_DIR, exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("slackfish")

cache = SlackfishCache()


def emit_event(event):
    """Append a single event to events.jsonl, rotating if needed."""
    event["t"] = time.time()
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(EVENTS_PATH, "a", encoding="utf-8") as f:
        f.write(line)


def rotate_events():
    """If events.jsonl exceeds EVENTS_MAX_LINES, truncate to the last EVENTS_KEEP_LINES."""
    if not os.path.exists(EVENTS_PATH):
        return
    try:
        with open(EVENTS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > EVENTS_MAX_LINES:
            with open(EVENTS_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines[-EVENTS_KEEP_LINES:])
            log.info("Rotated events.jsonl: %d -> %d lines", len(lines), EVENTS_KEEP_LINES)
    except OSError:
        pass


def handle_message(msg):
    msg_type = msg.get("type")
    if not msg_type:
        log.warning("Message without type: %s", msg)
        return

    if msg_type == "messages":
        channel_id = msg.get("channel_id")
        messages = msg.get("messages", [])
        is_thread = msg.get("thread_ts") is not None
        if channel_id and messages:
            for m in messages:
                bot_id = m.get("bot_id")
                bot_name = m.get("username")
                if bot_id and bot_name and not cache.get_user(bot_id):
                    cache.update_users([{
                        "id": bot_id,
                        "name": bot_name,
                        "real_name": bot_name,
                        "display_name": bot_name,
                        "is_bot": True,
                    }])
            new_msgs, dup_count = cache.update_messages(channel_id, messages, is_thread=is_thread)
            for m in new_msgs:
                emit_event({
                    "type": "new",
                    "channel_id": channel_id,
                    "ts": m["ts"],
                    "user": m.get("user"),
                    "text": m.get("text", ""),
                    "thread_ts": m.get("thread_ts"),
                    "reply_count": m.get("reply_count", 0),
                })
            if dup_count:
                emit_event({"type": "dup", "channel_id": channel_id, "count": dup_count})
            log.info("Cached %d messages for %s (new=%d, dup=%d, thread=%s)",
                     len(messages), channel_id, len(new_msgs), dup_count, is_thread)

    elif msg_type == "channels":
        channels = msg.get("channels", [])
        if channels:
            cache.update_channels(channels)
            emit_event({"type": "channels", "count": len(channels)})
            log.info("Cached %d channels", len(channels))

    elif msg_type == "users":
        users = msg.get("users", [])
        if users:
            new_count = cache.update_users(users)
            if new_count:
                emit_event({"type": "users", "count": new_count})
            log.info("Cached %d users (%d new)", len(users), new_count)

    elif msg_type == "context":
        channel_id = msg.get("channelId")
        cache.update_context({
            "channel_id": channel_id,
            "url": msg.get("url"),
            "timestamp": msg.get("timestamp"),
        })
        if channel_id:
            emit_event({"type": "context", "channel_id": channel_id})
        log.info("Context updated: %s", channel_id)

    elif msg_type == "self":
        cache.update_self(msg)
        log.info("Self updated: %s", msg.get("user_name"))

    elif msg_type == "log":
        level = msg.get("level", "info").lower()
        message = msg.get("message", "")
        getattr(log, level if level in ("debug", "info", "warning", "error") else "info")(
            "[EXT] %s", message
        )

    else:
        log.warning("Unknown message type: %s", msg_type)


def main():
    log.info("Native host started")
    rotate_events()
    write_message({"status": "ready"})

    while True:
        msg = read_message()
        if msg is None:
            log.info("Stdin closed, exiting")
            break
        try:
            handle_message(msg)
        except Exception as e:
            log.exception("Error handling message: %s", e)

    log.info("Native host exiting")


if __name__ == "__main__":
    main()
