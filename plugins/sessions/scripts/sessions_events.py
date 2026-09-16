"""Live updates: a file watcher that notices transcript/session changes and a
server-sent-events stream that pushes them to open pages."""

import json
import queue
import time
from pathlib import Path

import sessions_common as common

WATCH_INTERVAL = 1.5
KEEPALIVE = 15
NL = b"\n"


class EventHub:
    def __init__(self):
        self.listeners = set()

    def subscribe(self):
        listener = queue.Queue()
        self.listeners.add(listener)
        return listener

    def unsubscribe(self, listener):
        self.listeners.discard(listener)

    def broadcast(self, event):
        for listener in list(self.listeners):
            listener.put(event)

    @property
    def active(self):
        return bool(self.listeners)


def stream(handler, hub: EventHub):
    """Serve one SSE connection until the client goes away; a comment every KEEPALIVE seconds keeps it open."""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()
    listener = hub.subscribe()
    try:
        handler.wfile.write(b"retry: 3000" + NL + b"data: " + json.dumps({"type": "hello"}).encode("utf-8") + NL + NL)
        handler.wfile.flush()
        while True:
            try:
                event = listener.get(timeout=KEEPALIVE)
                payload = b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + NL + NL
            except queue.Empty:
                payload = b": ping" + NL + NL
            handler.wfile.write(payload)
            handler.wfile.flush()
    except (OSError, ValueError):
        pass
    finally:
        hub.unsubscribe(listener)


def file_signature(root: Path):
    """(session id -> (mtime, size)) for transcripts plus the sessions/ dir state; cheap enough to run every second."""
    files = {}
    for path in (root / "projects").glob("*/*.jsonl"):
        if common.is_uuid(path.stem):
            try:
                st = path.stat()
                files[path.stem] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
    live = []
    for path in (root / "sessions").glob("*.json"):
        try:
            live.append((path.name, path.stat().st_mtime_ns))
        except OSError:
            pass
    return files, tuple(sorted(live))


def watch_files(server, hub: EventHub, interval=WATCH_INTERVAL):
    """Push a `changed` event when a transcript grows, a session starts/stops, or a live process dies."""
    files, live = file_signature(server.root)
    known_pids = {}
    while True:
        time.sleep(interval)
        if not hub.active:
            files, live = file_signature(server.root)  # keep the baseline current while nobody listens
            continue
        new_files, new_live = file_signature(server.root)
        changed = [sid for sid, sig in new_files.items() if files.get(sid) != sig] + [sid for sid in files if sid not in new_files]
        died = [sid for sid, pid in known_pids.items() if not common.pid_alive(pid)]
        files, live_changed, live = new_files, new_live != live, new_live
        if not (changed or died or live_changed):
            continue
        index = server.refresh_index()
        known_pids = {rec["id"]: rec["live"]["pid"] for rec in index["sessions"] if rec.get("live")}
        hub.broadcast({"type": "changed", "sessions": (changed + died)[:50], "live": live_changed or bool(died)})
