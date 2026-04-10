"""Slackfish native messaging host.

Receives structured Slack data from the Firefox extension via native
messaging and writes it to the local cache.
"""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol import read_message, write_message
from cache import SlackfishCache

LOG_PATH = os.path.join(os.path.expanduser("~"), ".slackfish", "host.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("slackfish")

cache = SlackfishCache()


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
            cache.update_messages(channel_id, messages, is_thread=is_thread)
            log.info("Cached %d messages for %s (thread=%s)", len(messages), channel_id, is_thread)

    elif msg_type == "channels":
        channels = msg.get("channels", [])
        if channels:
            cache.update_channels(channels)
            log.info("Cached %d channels", len(channels))

    elif msg_type == "users":
        users = msg.get("users", [])
        if users:
            cache.update_users(users)
            log.info("Cached %d users", len(users))

    elif msg_type == "context":
        cache.update_context({
            "channel_id": msg.get("channelId"),
            "url": msg.get("url"),
            "timestamp": msg.get("timestamp"),
        })
        log.info("Context updated: %s", msg.get("channelId"))

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
