"""Shared plumbing: config-dir resolution, per-config-dir file names, port
derivation, process helpers. Everything is keyed by the resolved config dir so
two accounts never share an index, a state file or a port."""

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

VERSION = "0.1.0"
PORT_BASE = 47900
PORT_SPAN = 100
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# A child `claude` that sees CLAUDECODE=1 refuses to start (nested-session
# guard); CLAUDE_CONFIG_DIR is kept so the resumed session lands in the same account.
_KEEP_ENV = {"CLAUDE_CONFIG_DIR"}
_DROP_ENV_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_PLUGIN_")


def config_root() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    root = Path(env).expanduser() if env else Path.home() / ".claude"
    try:
        return root.resolve()
    except OSError:
        return root.absolute()


def root_key(root: Path) -> str:
    return hashlib.sha1(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:10]


def default_port(root: Path) -> int:
    return PORT_BASE + zlib.crc32(os.path.normcase(str(root)).encode("utf-8")) % PORT_SPAN


def data_dir() -> Path:
    # Not CLAUDE_PLUGIN_DATA: Claude Code sets it only for hook processes, so the
    # CLI/skill fallback would use another dir and spawn a second server.
    env = os.environ.get("SESSIONS_DATA_DIR", "").strip()
    path = Path(env).expanduser() if env else config_root() / "plugins" / "data" / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def server_file(root: Path) -> Path:
    return data_dir() / f"server-{root_key(root)}.json"


def index_file(root: Path) -> Path:
    return data_dir() / f"index-{root_key(root)}.json"


def log_file(root: Path) -> Path:
    return data_dir() / f"server-{root_key(root)}.log"


def state_file(root: Path) -> Path:
    return data_dir() / f"ui-state-{root_key(root)}.json"


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path: Path, payload) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) terminates the process on Windows; query a handle instead.
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def process_name(pid: int):
    """Executable name of a live pid, or None when it cannot be read."""
    try:
        if sys.platform == "win32":
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return None
            try:
                size = ctypes.c_ulong(1024)
                buf = ctypes.create_unicode_buffer(size.value)
                if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    return None
                return Path(buf.value).name.lower()
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        if sys.platform.startswith("linux"):
            return Path("/proc/%d/comm" % pid).read_text().strip().lower()
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=2).stdout
        return Path(out.strip()).name.lower() or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def claude_process(pid: int) -> bool:
    """Alive and still a Claude Code process (pids get reused after a crash)."""
    if not pid_alive(pid):
        return False
    name = process_name(pid)
    return name is None or any(tag in name for tag in ("claude", "node", "bun"))


def child_env(extra=None) -> dict:
    env = {k: v for k, v in os.environ.items() if k in _KEEP_ENV or not k.upper().startswith(_DROP_ENV_PREFIXES)}
    if extra:
        env.update(extra)
    return env


def claude_binary() -> str:
    for key in ("SESSIONS_CLAUDE_BIN", "CLAUDE_CODE_EXECPATH"):
        value = os.environ.get(key, "").strip()
        if value and Path(value).exists():
            return value
    return shutil.which("claude") or "claude"


def code_stamp() -> str:
    """Changes whenever a server-side script changes; a running server with another stamp is stale."""
    here = Path(__file__).resolve().parent
    return str(max(int(p.stat().st_mtime) for p in here.glob("*.py")))


def is_uuid(value: str) -> bool:
    return bool(value) and bool(UUID_RE.match(value))
