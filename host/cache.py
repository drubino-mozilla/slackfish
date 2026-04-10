"""Shared cache for Slackfish data.

Stores channels, users, messages, and threads as JSON files
under a configurable cache directory (default: ~/.slackfish/).
"""

import json
import os
import time

DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".slackfish")
MAX_MESSAGES_PER_CHANNEL = 500
MAX_THREAD_MESSAGES = 200


class SlackfishCache:
    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._ensure_dirs()

    def _ensure_dirs(self):
        for subdir in ["messages", "threads"]:
            os.makedirs(os.path.join(self.cache_dir, subdir), exist_ok=True)

    def _read_json(self, path):
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=None, separators=(",", ":"))

    # --- Channels ---

    def _channels_path(self):
        return os.path.join(self.cache_dir, "channels.json")

    def get_channels(self):
        return self._read_json(self._channels_path())

    def update_channels(self, channels):
        existing = self.get_channels()
        for ch in channels:
            cid = ch["id"]
            if cid in existing:
                existing[cid].update({k: v for k, v in ch.items() if v is not None})
            else:
                existing[cid] = ch
        self._write_json(self._channels_path(), existing)

    # --- Users ---

    def _users_path(self):
        return os.path.join(self.cache_dir, "users.json")

    def get_users(self):
        return self._read_json(self._users_path())

    def get_user(self, user_id):
        users = self.get_users()
        return users.get(user_id)

    def update_users(self, users):
        existing = self.get_users()
        for u in users:
            uid = u.get("id")
            if not uid:
                continue
            if uid in existing:
                existing[uid].update({k: v for k, v in u.items() if v is not None})
            else:
                existing[uid] = u
        self._write_json(self._users_path(), existing)

    # --- Messages ---

    def _messages_path(self, channel_id):
        return os.path.join(self.cache_dir, "messages", f"{channel_id}.json")

    def get_messages(self, channel_id, since=None, limit=None):
        data = self._read_json(self._messages_path(channel_id))
        messages = data.get("messages", [])
        if since:
            messages = [m for m in messages if float(m["ts"]) >= since]
        if limit:
            messages = messages[-limit:]
        return messages

    def update_messages(self, channel_id, messages, is_thread=False):
        if is_thread and messages:
            thread_ts = messages[0].get("thread_ts") or messages[0]["ts"]
            self._update_thread(channel_id, thread_ts, messages)
            return

        path = self._messages_path(channel_id)
        data = self._read_json(path)
        existing = data.get("messages", [])

        existing_by_ts = {m["ts"]: m for m in existing}
        for msg in messages:
            existing_by_ts[msg["ts"]] = msg

        merged = sorted(existing_by_ts.values(), key=lambda m: float(m["ts"]))
        if len(merged) > MAX_MESSAGES_PER_CHANNEL:
            merged = merged[-MAX_MESSAGES_PER_CHANNEL:]

        data["messages"] = merged
        data["channel_id"] = channel_id
        data["updated_at"] = time.time()
        self._write_json(path, data)

    # --- Threads ---

    def _thread_dir(self, channel_id):
        return os.path.join(self.cache_dir, "threads", channel_id)

    def _thread_path(self, channel_id, thread_ts):
        safe_ts = thread_ts.replace(".", "_")
        return os.path.join(self._thread_dir(channel_id), f"{safe_ts}.json")

    def get_thread(self, channel_id, thread_ts):
        data = self._read_json(self._thread_path(channel_id, thread_ts))
        return data.get("messages", [])

    def _update_thread(self, channel_id, thread_ts, messages):
        path = self._thread_path(channel_id, thread_ts)
        data = self._read_json(path)
        existing = data.get("messages", [])

        existing_by_ts = {m["ts"]: m for m in existing}
        for msg in messages:
            existing_by_ts[msg["ts"]] = msg

        merged = sorted(existing_by_ts.values(), key=lambda m: float(m["ts"]))
        if len(merged) > MAX_THREAD_MESSAGES:
            merged = merged[-MAX_THREAD_MESSAGES:]

        data["messages"] = merged
        data["channel_id"] = channel_id
        data["thread_ts"] = thread_ts
        data["updated_at"] = time.time()
        self._write_json(path, data)

    # --- Context ---

    def _context_path(self):
        return os.path.join(self.cache_dir, "context.json")

    def get_context(self):
        return self._read_json(self._context_path())

    def update_context(self, context):
        self._write_json(self._context_path(), context)

    # --- Self (current user) ---

    def _self_path(self):
        return os.path.join(self.cache_dir, "self.json")

    def get_self(self):
        return self._read_json(self._self_path())

    def update_self(self, data):
        self._write_json(self._self_path(), data)

    # --- Search ---

    def search_messages(self, query, limit=50):
        """Full-text search across all cached messages."""
        query_lower = query.lower()
        results = []
        messages_dir = os.path.join(self.cache_dir, "messages")
        if not os.path.isdir(messages_dir):
            return results

        for filename in os.listdir(messages_dir):
            if not filename.endswith(".json"):
                continue
            channel_id = filename[:-5]
            data = self._read_json(os.path.join(messages_dir, filename))
            for msg in data.get("messages", []):
                if query_lower in (msg.get("text") or "").lower():
                    results.append({**msg, "channel_id": channel_id})
                    if len(results) >= limit:
                        return results
        return results

    # --- Stats ---

    def get_stats(self):
        channels = self.get_channels()
        users = self.get_users()
        messages_dir = os.path.join(self.cache_dir, "messages")
        msg_files = []
        if os.path.isdir(messages_dir):
            msg_files = [f for f in os.listdir(messages_dir) if f.endswith(".json")]
        total_messages = 0
        for f in msg_files:
            data = self._read_json(os.path.join(messages_dir, f))
            total_messages += len(data.get("messages", []))
        return {
            "channels_cached": len(channels),
            "users_cached": len(users),
            "channels_with_messages": len(msg_files),
            "total_messages": total_messages,
        }
