"""`/sessions` handler.

Hook mode (no argv): reads the UserPromptSubmit payload on stdin; for
`/sessions [stop|reindex|status]` it acts and answers {"decision": "block"},
so the prompt never reaches the model. Other prompts: exit 0, no output.
CLI mode (argv): same actions, plain text output.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sessions_common as common  # noqa: E402

SLASH_RE = re.compile(r"^/sessions(?::[\w-]+)?(?:\s+(.*))?$", re.S)
START_TIMEOUT = 8.0


def ping(port: int, root: Path, timeout=0.6):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=timeout) as resp:
            info = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not info.get("ok") or Path(info.get("configDir", "")).resolve() != root:
        return None
    return info


def running_server(root: Path, replace_stale=True):
    info = common.read_json(common.server_file(root)) or {}
    port = info.get("port")
    if not (port and common.pid_alive(info.get("pid") or 0) and ping(port, root)):
        # No usable server file: a server may still answer on the derived port (file lost or overwritten).
        info = ping(common.default_port(root), root) or {}
        port = info.get("port")
        if not port:
            return None
    if info.get("codeStamp") == common.code_stamp() or not replace_stale:
        return port
    stop_server(port, info.get("pid"))  # plugin updated: drop the old process, caller spawns a fresh one
    return None


def stop_server(port, pid):
    try:
        post("http://127.0.0.1:%d/api/shutdown" % port)
    except Exception:
        return
    deadline = time.time() + 3
    while pid and common.pid_alive(pid) and time.time() < deadline:
        time.sleep(0.1)


def spawn_server(root: Path) -> int:
    """Start the server, unless another hook is already starting it (two /sessions at once)."""
    lock = common.data_dir() / ("spawn-%s.lock" % common.root_key(root))
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime < START_TIMEOUT + 2:
            return wait_for_server(root)
        lock.unlink(missing_ok=True)  # stale lock from a crashed hook
        return spawn_server(root)
    os.close(fd)
    try:
        return launch_server(root)
    finally:
        lock.unlink(missing_ok=True)


def wait_for_server(root: Path) -> int:
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.2)
        info = ping(common.default_port(root), root)
        if info:
            return info["port"]
        stored = common.read_json(common.server_file(root)) or {}
        if stored.get("port") and ping(stored["port"], root):
            return stored["port"]
    raise RuntimeError("another /sessions is starting the server but it did not come up")


def launch_server(root: Path) -> int:
    script = Path(__file__).resolve().parent / "sessions_server.py"
    argv = [sys.executable, str(script), "--config-dir", str(root)]
    env = common.child_env({"SESSIONS_CLAUDE_BIN": common.claude_binary(), "PYTHONIOENCODING": "utf-8"})
    log = open(common.log_file(root), "a", encoding="utf-8")
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log, "env": env, "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(argv, **kwargs)
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.15)
        info = common.read_json(common.server_file(root)) or {}
        if info.get("pid") == process.pid and info.get("port") and ping(info["port"], root):
            return info["port"]
        if process.poll() is not None:
            break
    raise RuntimeError("server did not start; see %s" % common.log_file(root))


def post(url: str):
    request = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"X-Sessions-Client": "1", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run(action: str, root: Path) -> str:
    if action == "status":
        port = running_server(root, replace_stale=False)
        if not port:
            return "no sessions viewer running for %s" % root
        stale = "" if (ping(port, root) or {}).get("codeStamp") == common.code_stamp() else " (outdated, restarts on next /sessions)"
        return "sessions viewer running at http://127.0.0.1:%d/ (config dir %s)%s" % (port, root, stale)
    if action == "stop":
        info = common.read_json(common.server_file(root)) or {}
        port = info.get("port")
        if not (port and common.pid_alive(info.get("pid") or 0) and ping(port, root)):
            info = ping(common.default_port(root), root) or {}
            port = info.get("port")
        if not port:
            return "no sessions viewer running for %s" % root
        stop_server(port, info.get("pid"))
        return "sessions viewer stopped (port %d)" % port
    port = running_server(root) or spawn_server(root)
    url = "http://127.0.0.1:%d/" % port
    if action == "reindex":
        post(url + "api/reindex")
    webbrowser.open(url)
    return "Sessions viewer: %s\nconfig dir: %s" % (url, root)


def parse_action(arg: str) -> str:
    arg = (arg or "").strip().lower().lstrip("-")
    return arg if arg in ("stop", "reindex", "status") else "open"


def hook_mode() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    match = SLASH_RE.match((payload.get("prompt") or "").strip())
    if not match:
        return 0
    try:
        reason = run(parse_action(match.group(1)), common.config_root())
    except Exception as exc:
        reason = "sessions: %s" % exc
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    sys.stdout.flush()
    return 0


def cli_mode(argv) -> int:
    action = parse_action(argv[1] if argv[0] == "open" and len(argv) > 1 else argv[0])
    try:
        print(run(action, common.config_root()))
        return 0
    except Exception as exc:
        print("sessions: %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(cli_mode(sys.argv[1:]) if len(sys.argv) > 1 else hook_mode())
