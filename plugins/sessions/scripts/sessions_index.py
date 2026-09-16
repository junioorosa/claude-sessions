"""Index of the sessions in one config dir: one `<config>/projects/*/<uuid>.jsonl`
each (deeper files are subagent transcripts). Files are cached by (mtime, size).
The record format is undocumented and shifts between versions, so only known
record types are read and everything else is ignored.

Standalone: python sessions_index.py [--config-dir DIR] [--rebuild] [--json] [--limit N]
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sessions_common as common  # noqa: E402

INDEX_VERSION = 5
MAX_TITLE = 90
MAX_PROMPT = 300
SKIP_TITLE_PREFIXES = ("<", "/", "!", "This session is being continued", "Caveat:", "[Request interrupted")
COMMAND_RE = re.compile(r"<command-name>(.*?)</command-name>(?:.*?<command-args>(.*?)</command-args>)?", re.S)


def iter_records(path: Path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def text_of(content):
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        joined = "\n".join(p for p in parts if p).strip()
        return joined or None
    return None


def short(text, limit):
    if not text:
        return None
    stripped = text.strip()
    first = stripped.splitlines()[0].strip() if stripped else ""
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def title_candidate(text):
    return bool(text) and not text.lstrip().startswith(SKIP_TITLE_PREFIXES)


def fallback_title(text):
    """Last resort for sessions whose only prompts were slash commands or injected tags."""
    if not text:
        return None
    command = COMMAND_RE.search(text)
    if command:
        return (command.group(1).strip() + " " + (command.group(2) or "").strip()).strip()
    return None if text.lstrip().startswith("<") else text


def empty_record(session_id, path):
    return {
        "id": session_id, "path": str(path), "projectKey": path.parent.name, "project": None,
        "customTitle": None, "aiTitle": None, "agentName": None, "firstPrompt": None, "firstText": None, "lastPrompt": None,
        "gitBranch": None, "version": None, "entrypoint": None, "createdAt": None, "updatedAt": None,
        "userTurns": 0, "assistantTurns": 0, "compacted": 0, "bridged": False, "models": [],
        "costUSD": None, "durationMs": None, "linesAdded": None, "linesRemoved": None, "sizeBytes": 0,
    }


def scan_session(path: Path) -> dict:
    rec = empty_record(path.stem, path)
    models = []
    last_message_id = None
    first_prompt_raw = None
    last_user_text = None
    for o in iter_records(path):
        kind = o.get("type")
        if kind == "custom-title":
            rec["customTitle"] = (o.get("customTitle") or "").strip() or rec["customTitle"]
        elif kind == "ai-title":
            rec["aiTitle"] = (o.get("aiTitle") or "").strip() or rec["aiTitle"]
        elif kind == "agent-name":
            rec["agentName"] = (o.get("agentName") or "").strip() or rec["agentName"]
        elif kind == "last-prompt":
            if o.get("lastPrompt"):
                rec["lastPrompt"] = short(o["lastPrompt"], MAX_PROMPT)
        elif kind == "bridge-session":
            rec["bridged"] = True
        elif kind == "cost-state":
            rec["costUSD"] = o.get("totalCostUSD")
            rec["durationMs"] = o.get("totalDuration")
            rec["linesAdded"] = o.get("totalLinesAdded")
            rec["linesRemoved"] = o.get("totalLinesRemoved")
        elif kind in ("user", "assistant"):
            if o.get("isSidechain"):
                continue
            ts = o.get("timestamp")
            if ts:
                rec["createdAt"] = rec["createdAt"] or ts
                rec["updatedAt"] = ts
            rec["project"] = rec["project"] or o.get("cwd")
            rec["gitBranch"] = o.get("gitBranch") or rec["gitBranch"]
            rec["version"] = o.get("version") or rec["version"]
            rec["entrypoint"] = rec["entrypoint"] or o.get("entrypoint")
            message = o.get("message") or {}
            if kind == "user":
                if o.get("isCompactSummary"):
                    rec["compacted"] += 1
                    continue
                if o.get("isMeta"):
                    continue
                text = text_of(message.get("content"))
                if not text:
                    continue
                rec["userTurns"] += 1
                last_user_text = text
                rec["firstText"] = rec["firstText"] or fallback_title(text)
                if first_prompt_raw is None and title_candidate(text):
                    first_prompt_raw = text
            else:
                message_id = message.get("id")
                if message_id != last_message_id:
                    rec["assistantTurns"] += 1
                    last_message_id = message_id
                model = message.get("model")
                if model and not model.startswith("<") and model not in models:
                    models.append(model)
    rec["models"] = models
    rec["firstPrompt"] = short(first_prompt_raw, MAX_PROMPT)
    if not rec["lastPrompt"]:
        rec["lastPrompt"] = short(last_user_text, MAX_PROMPT)
    return rec


def file_mtime_iso(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_history_titles(root: Path) -> dict:
    titles = {}
    path = root / "history.jsonl"
    if not path.exists():
        return titles
    for o in iter_records(path):
        sid = o.get("sessionId")
        display = (o.get("display") or "").strip()
        if sid and sid not in titles and title_candidate(display):
            titles[sid] = display
    return titles


def load_live_sessions(root: Path) -> dict:
    live = {}
    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        return live
    for path in sessions_dir.glob("*.json"):
        info = common.read_json(path)
        if not isinstance(info, dict):
            continue
        sid = info.get("sessionId")
        if sid and common.claude_process(info.get("pid") or 0):
            live[sid] = {key: info.get(key) for key in ("pid", "status", "name", "nameSource", "cwd", "updatedAt")}
    return live


def resolve_title(rec: dict, live: dict, history: dict) -> None:
    live = live or {}
    live_name = live.get("name") if live.get("nameSource") not in (None, "derived") else None
    for source, value in (
        ("custom", rec.get("customTitle")),
        ("ai", rec.get("aiTitle")),
        ("name", live_name),
        ("history", history.get(rec["id"])),
        ("prompt", rec.get("firstPrompt")),
        ("text", rec.get("firstText")),
    ):
        if value:
            rec["title"] = short(value, MAX_TITLE)
            rec["titleSource"] = source
            return
    rec["title"] = "(untitled)"
    rec["titleSource"] = "none"


def refresh(root: Path, rebuild: bool = False) -> dict:
    cache = {} if rebuild else (common.read_json(common.index_file(root)) or {})
    if cache.get("version") != INDEX_VERSION:
        cache = {}
    files = cache.get("files", {})
    seen = set()
    projects_dir = root / "projects"
    if projects_dir.is_dir():
        for path in projects_dir.glob("*/*.jsonl"):
            if not common.is_uuid(path.stem):
                continue
            key = str(path)
            try:
                st = path.stat()
            except OSError:
                continue
            seen.add(key)
            entry = files.get(key)
            if entry and entry.get("mtime") == st.st_mtime and entry.get("size") == st.st_size:
                continue
            rec = scan_session(path)
            rec["sizeBytes"] = st.st_size
            rec["updatedAt"] = rec["updatedAt"] or file_mtime_iso(path)
            rec["createdAt"] = rec["createdAt"] or rec["updatedAt"]
            files[key] = {"mtime": st.st_mtime, "size": st.st_size, "rec": rec}
    for key in list(files):
        if key not in seen:
            del files[key]
    try:
        common.write_json(common.index_file(root), {"version": INDEX_VERSION, "files": files})
    except OSError:
        pass  # another process is writing the same cache; this refresh still returns fresh data

    live = load_live_sessions(root)
    history = load_history_titles(root)
    # A session with no message records has no cwd; borrow it from a sibling in the same project dir.
    cwd_by_key = {e["rec"]["projectKey"]: e["rec"]["project"] for e in files.values() if e["rec"].get("project")}
    sessions = []
    for entry in files.values():
        rec = dict(entry["rec"])
        rec["project"] = rec["project"] or cwd_by_key.get(rec["projectKey"])
        rec["live"] = live.get(rec["id"])
        rec["empty"] = rec["userTurns"] == 0 and rec["assistantTurns"] == 0
        rec["headless"] = str(rec.get("entrypoint") or "").startswith("sdk")  # sdk-cli = `claude -p`; cli/claude-desktop = interactive
        resolve_title(rec, rec["live"], history)
        sessions.append(rec)
    sessions.sort(key=lambda r: r.get("updatedAt") or "", reverse=True)
    return {
        "configDir": str(root),
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "sessions": sessions,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Index Claude Code sessions of one config dir.")
    parser.add_argument("--config-dir", help="Claude config dir (default: CLAUDE_CONFIG_DIR or ~/.claude)")
    parser.add_argument("--rebuild", action="store_true", help="ignore the cache and rescan every file")
    parser.add_argument("--json", action="store_true", help="print the full index as JSON")
    parser.add_argument("--limit", type=int, default=30, help="rows to print in table mode")
    args = parser.parse_args(argv)
    root = Path(args.config_dir).expanduser().resolve() if args.config_dir else common.config_root()
    index = refresh(root, rebuild=args.rebuild)
    if args.json:
        json.dump(index, sys.stdout, ensure_ascii=False, indent=1)
        return 0
    print(f"config dir: {index['configDir']}  sessions: {len(index['sessions'])}")
    for rec in index["sessions"][: args.limit]:
        live = " *" if rec.get("live") else ""
        cost = f"${rec['costUSD']:.2f}" if rec.get("costUSD") is not None else "-"
        print(f"{rec['id'][:8]} {rec['updatedAt'][:16]:16} {rec['userTurns']:3}/{rec['assistantTurns']:<3} "
              f"{cost:>7} [{rec['titleSource']:7}] {rec['title']}{live}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
