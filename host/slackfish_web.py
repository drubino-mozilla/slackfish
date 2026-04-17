"""Slackfish web UI server.

Serves a local web dashboard for browsing cached Slack data.
Uses watchdog to detect cache file changes and pushes updates
to connected browsers via Server-Sent Events.
"""

import asyncio
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cache import SlackfishCache

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".slackfish")
EVENTS_PATH = os.path.join(CACHE_DIR, "events.jsonl")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PORT = 7890
WORKSPACE_URL = "https://mozilla.slack.com"
HEARTBEAT_TIMEOUT = 60  # seconds with no heartbeat before auto-exit

cache = SlackfishCache()
sse_clients: list[asyncio.Queue] = []
last_heartbeat: float = time.time()


class CacheEventHandler(FileSystemEventHandler):
    """Translates filesystem events on ~/.slackfish/ into SSE notifications."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def _broadcast(self, event_data: dict):
        for q in list(sse_clients):
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, event_data)
            except asyncio.QueueFull:
                pass

    def on_modified(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        name = path.name
        parent = path.parent.name

        if parent == "messages" and name.endswith(".json"):
            channel_id = name[:-5]
            self._broadcast({"event": "channel_update", "channel_id": channel_id})
        elif name == "channels.json":
            self._broadcast({"event": "channels_update"})
        elif name == "users.json":
            self._broadcast({"event": "users_update"})
        elif name == "context.json":
            self._broadcast({"event": "context_update"})
        elif name == "events.jsonl":
            self._broadcast({"event": "activity_update"})

    on_created = on_modified


observer = Observer()


async def heartbeat_watchdog():
    """Background task that exits the process if no tab has pinged recently."""
    while True:
        await asyncio.sleep(15)
        if time.time() - last_heartbeat > HEARTBEAT_TIMEOUT:
            print("No heartbeat for 60s -- shutting down.", flush=True)
            os._exit(0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    handler = CacheEventHandler(loop)
    observer.schedule(handler, CACHE_DIR, recursive=True)
    observer.start()
    asyncio.create_task(heartbeat_watchdog())
    yield
    observer.stop()
    observer.join()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def resolve_user(user_id: str) -> str:
    if not user_id:
        return "unknown"
    user = cache.get_user(user_id)
    if user:
        return user.get("display_name") or user.get("real_name") or user.get("name") or user_id
    return user_id


def make_permalink(channel_id: str, ts: str) -> str:
    return f"{WORKSPACE_URL}/archives/{channel_id}/p{ts.replace('.', '')}"


def enrich_message(msg: dict, channel_id: str = "") -> dict:
    """Add resolved usernames, permalink, and formatted time to a message."""
    ts = float(msg["ts"])
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    user_name = resolve_user(msg.get("user"))

    text = msg.get("text", "")
    text = re.sub(r"<@([A-Z0-9]+)>", lambda m: f"@{resolve_user(m.group(1))}", text)

    cid = channel_id or msg.get("channel_id", "")
    return {
        **msg,
        "user_name": user_name,
        "text_resolved": text,
        "time_str": dt.strftime("%Y-%m-%d %H:%M"),
        "time_short": dt.strftime("%H:%M"),
        "permalink": make_permalink(cid, msg["ts"]) if cid else None,
        "channel_id": cid,
    }


def resolve_mpdm_name(raw_name: str, username_map: dict) -> str:
    """Parse 'mpdm-alice--bob--charlie-1' into 'Alice A, Bob B, Charlie C'."""
    if not raw_name or not raw_name.startswith("mpdm-"):
        return raw_name or ""
    stripped = re.sub(r'^mpdm-', '', raw_name)
    stripped = re.sub(r'-\d+$', '', stripped)
    usernames = [u for u in stripped.split("--") if u]
    return ", ".join(username_map.get(u, u) for u in usernames)


def is_dm(cid: str, ch: dict) -> bool:
    return ch.get("is_im") or (cid.startswith("D") and not ch.get("is_mpim"))


def is_group_dm(cid: str, ch: dict) -> bool:
    return ch.get("is_mpim", False)


def _get_latest_ts_map():
    """Return {channel_id: latest_message_ts} by scanning message cache files."""
    messages_dir = os.path.join(CACHE_DIR, "messages")
    result = {}
    if not os.path.isdir(messages_dir):
        return result
    for fname in os.listdir(messages_dir):
        if not fname.endswith(".json"):
            continue
        cid = fname[:-5]
        try:
            with open(os.path.join(messages_dir, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            msgs = data.get("messages", [])
            if msgs:
                result[cid] = float(msgs[-1]["ts"])
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            pass
    return result


# --- API endpoints ---

@app.get("/api/stats")
def api_stats():
    return cache.get_stats()


@app.get("/api/channels")
def api_channels():
    raw = cache.get_channels()
    latest_ts = _get_latest_ts_map()
    users = cache.get_users()
    username_map = {}
    for u in users.values():
        uname = u.get("name")
        if uname:
            username_map[uname] = u.get("display_name") or u.get("real_name") or uname
    channels = []
    for cid, ch in raw.items():
        entry = {**ch, "id": cid}
        if is_dm(cid, ch):
            entry["is_im"] = True
            entry["display_name"] = resolve_user(ch.get("user")) if ch.get("user") else (ch.get("name") or cid)
        elif is_group_dm(cid, ch):
            name = ch.get("name") or cid
            entry["display_name"] = resolve_mpdm_name(name, username_map) if name.startswith("mpdm-") else name
        else:
            entry["display_name"] = f"#{ch.get('name') or cid}"
        entry["latest_ts"] = latest_ts.get(cid, 0)
        channels.append(entry)
    channels.sort(key=lambda c: (-c["latest_ts"], (c.get("display_name") or "").lower()))
    return channels


@app.get("/api/activity")
def api_activity(limit: int = 200):
    """Read events from events.jsonl, return newest first."""
    if not os.path.exists(EVENTS_PATH):
        return []
    events = []
    try:
        with open(EVENTS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        channels = cache.get_channels()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = ev.get("channel_id", "")
            if cid:
                ch = channels.get(cid, {})
                name = ch.get("name") or cid
                if is_dm(cid, ch):
                    ev["channel_name"] = resolve_user(ch.get("user")) if ch.get("user") else name
                elif is_group_dm(cid, ch):
                    ev["channel_name"] = name
                else:
                    ev["channel_name"] = f"#{name}"
            if ev.get("type") == "new" and ev.get("user"):
                ev["user_name"] = resolve_user(ev["user"])
            events.append(ev)
            if len(events) >= limit:
                break
    except OSError:
        pass
    return events


@app.get("/api/channels/{channel_id}/messages")
def api_messages(channel_id: str, since: float = 0, limit: int = 200):
    messages = cache.get_messages(channel_id, since=since if since else None, limit=limit)
    return [enrich_message(m, channel_id) for m in messages]


@app.get("/api/threads/{channel_id}/{thread_ts}")
def api_thread(channel_id: str, thread_ts: str):
    messages = cache.get_thread(channel_id, thread_ts)
    return [enrich_message(m, channel_id) for m in messages]


@app.get("/api/search")
def api_search(q: str = Query(""), limit: int = 50):
    if not q.strip():
        return []
    results = cache.search_messages(q, limit=limit)
    channels = cache.get_channels()
    enriched = []
    for msg in results:
        cid = msg.get("channel_id", "")
        ch = channels.get(cid, {})
        m = enrich_message(msg, cid)
        m["channel_name"] = ch.get("name") or cid
        m["is_im"] = ch.get("is_im", False)
        enriched.append(m)
    return enriched


@app.get("/api/context")
def api_context():
    ctx = cache.get_context()
    if not ctx or not ctx.get("channel_id"):
        return {"active": False}
    channel_id = ctx["channel_id"]
    channels = cache.get_channels()
    ch = channels.get(channel_id, {})
    name = ch.get("name") or channel_id
    if ch.get("is_im"):
        name = f"DM with {resolve_user(ch.get('user'))}"
    elif not ch.get("is_mpim"):
        name = f"#{name}"
    return {
        "active": True,
        "channel_id": channel_id,
        "channel_name": name,
        "url": ctx.get("url"),
        "timestamp": ctx.get("timestamp"),
    }


@app.get("/api/users")
def api_users():
    raw = cache.get_users()
    users = []
    for uid, u in raw.items():
        users.append({**u, "id": uid})
    users.sort(key=lambda u: (u.get("real_name") or u.get("name") or "").lower())
    return users


# --- Heartbeat ---

@app.post("/api/heartbeat")
def api_heartbeat():
    global last_heartbeat
    last_heartbeat = time.time()
    return {"ok": True}


# --- SSE endpoint ---

@app.get("/events")
async def sse_endpoint():
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    sse_clients.append(q)

    async def event_stream():
        try:
            while True:
                data = await q.get()
                yield f"data: {json.dumps(data)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse_clients.remove(q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Static files + index ---

ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "icons")
app.mount("/icons", StaticFiles(directory=ICONS_DIR), name="icons")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


if __name__ == "__main__":
    # pythonw.exe sets stdout/stderr to None -- redirect to devnull so
    # print() and uvicorn logging don't crash.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")
    print(f"Slackfish Web UI: http://localhost:{PORT}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
