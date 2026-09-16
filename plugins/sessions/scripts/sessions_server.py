"""Loopback HTTP server behind the viewer, one per config dir. Stdlib only.
Exits on its own after `--idle-minutes` without a request.

    python sessions_server.py [--config-dir DIR] [--port N] [--idle-minutes N] [--open]
"""

import argparse
import atexit
import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sessions_common as common  # noqa: E402
import sessions_events  # noqa: E402
import sessions_index  # noqa: E402
import sessions_launch  # noqa: E402
from sessions_transcript import load_transcript  # noqa: E402

UI_DIR = Path(__file__).resolve().parent / "ui"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
DEFAULT_STATE = {"pinned": [], "groups": [], "titles": {}, "hidden": []}
SEARCH_MAX_SESSIONS = 60
SEARCH_MAX_SNIPPETS = 3
# Custom header on mutating routes: browsers only send it after a CORS
# preflight, which is never answered, so other origins cannot POST here.
CLIENT_HEADER = "X-Sessions-Client"
RESUME_COOLDOWN = 5  # seconds during which a second launch of the same session is refused


class SessionsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # on Windows True would bind a port already in use

    def __init__(self, address, handler, root: Path, idle_seconds: int):
        super().__init__(address, handler)
        self.root = root
        self.idle_seconds = idle_seconds
        self.last_request = time.time()
        self.lock = threading.Lock()
        self.by_id = {}
        self.index = None
        self.code_stamp = common.code_stamp()
        self.hub = sessions_events.EventHub()
        self.launches = {}  # session id -> time of the last terminal launch

    def refresh_index(self, rebuild=False):
        with self.lock:
            self.index = sessions_index.refresh(self.root, rebuild=rebuild)
            self.by_id = {rec["id"]: rec for rec in self.index["sessions"]}
            return self.index

    def session(self, session_id):
        rec = self.by_id.get(session_id)
        if rec is None:
            self.refresh_index()
            rec = self.by_id.get(session_id)
        return rec


class Handler(BaseHTTPRequestHandler):
    server_version = "sessions/" + common.VERSION

    def log_message(self, fmt, *args):
        if self.command != "GET":
            sys.stderr.write("%s %s\n" % (dt.datetime.now().strftime("%H:%M:%S"), fmt % args))

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, path):
        name = "index.html" if path == "/" else Path(path).name
        file = UI_DIR / name
        if file.suffix not in CONTENT_TYPES or not file.is_file():
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        body = file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", CONTENT_TYPES[file.suffix])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except ValueError:
            return {}

    def guard(self, mutating):
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        if host not in ("127.0.0.1:%d" % port, "localhost:%d" % port):
            self.send_json({"error": "bad host"}, HTTPStatus.FORBIDDEN)
            return False
        if mutating and self.headers.get(CLIENT_HEADER) != "1":
            self.send_json({"error": "missing %s header" % CLIENT_HEADER}, HTTPStatus.FORBIDDEN)
            return False
        self.server.last_request = time.time()
        return True

    def do_GET(self):
        if not self.guard(mutating=False):
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        path = url.path
        if not path.startswith("/api/"):
            return self.send_static(path)
        if path == "/api/ping":
            return self.send_json(self.ping())
        if path == "/api/sessions":
            return self.send_json(self.server.refresh_index(rebuild=query.get("rebuild", ["0"])[0] == "1"))
        if path.startswith("/api/transcript/"):
            return self.transcript(path.rsplit("/", 1)[1])
        if path == "/api/search":
            return self.send_json(self.search(query.get("q", [""])[0]))
        if path == "/api/state":
            return self.send_json(load_state(self.server.root))
        if path == "/api/events":
            return sessions_events.stream(self, self.server.hub)
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self):
        if not self.guard(mutating=True):
            return
        if urlsplit(self.path).path == "/api/state":
            state = normalize_state(self.read_body())
            common.write_json(common.state_file(self.server.root), state)
            return self.send_json(state)
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self.guard(mutating=True):
            return
        path = urlsplit(self.path).path
        if path.startswith("/api/resume/"):
            return self.resume(path.rsplit("/", 1)[1], self.read_body())
        if path == "/api/reindex":
            return self.send_json(self.server.refresh_index(rebuild=True))
        if path == "/api/shutdown":
            self.send_json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def ping(self):
        return {"ok": True, "version": common.VERSION, "codeStamp": self.server.code_stamp, "pid": os.getpid(),
                "configDir": str(self.server.root), "port": self.server.server_address[1],
                "platform": sys.platform, "claude": common.claude_binary()}

    def transcript(self, session_id):
        if not common.is_uuid(session_id):
            return self.send_json({"error": "bad id"}, HTTPStatus.BAD_REQUEST)
        rec = self.server.session(session_id)
        if rec is None:
            return self.send_json({"error": "unknown session"}, HTTPStatus.NOT_FOUND)
        result = load_transcript(Path(rec["path"]))
        result["id"] = session_id
        self.send_json(result)

    def search(self, needle):
        needle = needle.strip()
        if len(needle) < 2:
            return {"q": needle, "hits": []}
        index = self.server.index or self.server.refresh_index()
        hits = []
        for rec in index["sessions"]:
            snippets = search_file(Path(rec["path"]), needle)
            if snippets:
                hits.append({"id": rec["id"], "snippets": snippets})
                if len(hits) >= SEARCH_MAX_SESSIONS:
                    break
        return {"q": needle, "hits": hits}

    def resume(self, session_id, body):
        if not common.is_uuid(session_id):
            return self.send_json({"error": "bad id"}, HTTPStatus.BAD_REQUEST)
        rec = self.server.session(session_id)
        if rec is None:
            return self.send_json({"error": "unknown session"}, HTTPStatus.NOT_FOUND)
        now = time.time()
        if now - self.server.launches.get(session_id, 0) < RESUME_COOLDOWN:
            return self.send_json({"ok": False, "busy": True, "detail": "already opening"})
        self.server.launches[session_id] = now
        cwd = rec.get("project") or str(Path.home())
        if not Path(cwd).is_dir():
            cwd = str(Path.home())
        argv = [common.claude_binary(), "--resume", session_id]
        if body.get("fork"):
            argv.append("--fork-session")
        ok, detail = sessions_launch.spawn_terminal(cwd, argv)
        self.send_json({"ok": ok, "detail": detail, "cwd": cwd, "command": sessions_launch.shell_line(argv)})


def normalize_state(raw):
    raw = raw if isinstance(raw, dict) else {}
    groups = [
        {"id": str(g.get("id") or g["name"]), "name": g["name"], "sessions": [s for s in g.get("sessions", []) if isinstance(s, str)]}
        for g in raw.get("groups", []) if isinstance(g, dict) and isinstance(g.get("name"), str)
    ]
    return {
        "pinned": [s for s in raw.get("pinned", []) if isinstance(s, str)],
        "groups": groups,
        "titles": {k: v for k, v in (raw.get("titles") or {}).items() if isinstance(v, str)},
        "hidden": [s for s in raw.get("hidden", []) if isinstance(s, str)],
    }


def load_state(root):
    return normalize_state(common.read_json(common.state_file(root)) or DEFAULT_STATE)


def text_fields(record):
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                yield block.get("text", "")
            elif kind == "tool_use":
                yield json.dumps(block.get("input"), ensure_ascii=False)
            elif kind == "tool_result":
                yield sessions_index.text_of(block.get("content")) or ""


def search_file(path: Path, needle: str):
    lowered = needle.lower()
    snippets = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if lowered not in line.lower():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("type") not in ("user", "assistant") or record.get("isSidechain"):
                    continue
                for text in text_fields(record):
                    pos = text.lower().find(lowered)
                    if pos < 0:
                        continue
                    start = max(0, pos - 80)
                    end = min(len(text), pos + len(needle) + 80)
                    snippets.append({"role": record["type"], "text": text[start:end].replace("\n", " ")})
                    if len(snippets) >= SEARCH_MAX_SNIPPETS:
                        return snippets
    except OSError:
        pass
    return snippets


def already_serving(port: int, root: Path) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=0.6) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        return bool(info.get("ok")) and Path(info.get("configDir", "")).resolve() == root
    except Exception:
        return False


def bind(root: Path, port: int, idle_seconds: int) -> SessionsServer:
    last_error = None
    for candidate in range(port, port + 20):
        try:
            return SessionsServer(("127.0.0.1", candidate), Handler, root, idle_seconds)
        except OSError as exc:
            last_error = exc
    raise SystemExit("could not bind a port: %s" % last_error)


def idle_watchdog(server: SessionsServer):
    while True:
        time.sleep(30)
        if server.hub.active:
            continue  # an open tab is a user; only exit once every page is closed
        if time.time() - server.last_request > server.idle_seconds:
            sys.stderr.write("idle for %ds, exiting\n" % server.idle_seconds)
            server.shutdown()
            return


def write_server_file(root: Path, port: int):
    path = common.server_file(root)
    common.write_json(path, {"port": port, "pid": os.getpid(), "configDir": str(root), "version": common.VERSION,
                             "codeStamp": common.code_stamp(),
                             "startedAt": dt.datetime.now(dt.timezone.utc).isoformat()})

    def cleanup():
        if (common.read_json(path) or {}).get("pid") == os.getpid():
            try:
                path.unlink()
            except OSError:
                pass
    atexit.register(cleanup)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sessions viewer server (loopback only).")
    parser.add_argument("--config-dir", help="Claude config dir (default: CLAUDE_CONFIG_DIR or ~/.claude)")
    parser.add_argument("--port", type=int, help="port to bind (default derived from the config dir)")
    parser.add_argument("--idle-minutes", type=int, default=int(os.environ.get("SESSIONS_IDLE_MINUTES", "30")))
    parser.add_argument("--open", action="store_true", help="open the browser once the server is up")
    args = parser.parse_args(argv)
    root = Path(args.config_dir).expanduser().resolve() if args.config_dir else common.config_root()
    port = args.port or common.default_port(root)
    if not args.port and already_serving(port, root):
        sys.stderr.write("a sessions viewer for %s already answers on port %d\n" % (root, port))
        return 0
    server = bind(root, port, max(1, args.idle_minutes) * 60)
    port = server.server_address[1]
    write_server_file(root, port)
    server.refresh_index()
    threading.Thread(target=idle_watchdog, args=(server,), daemon=True).start()
    threading.Thread(target=sessions_events.watch_files, args=(server, server.hub), daemon=True).start()
    url = "http://127.0.0.1:%d/" % port
    sys.stderr.write("sessions viewer %s for %s (pid %d)\n" % (url, root, os.getpid()))
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
