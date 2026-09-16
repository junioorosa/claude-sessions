"""Turns of one session, for the viewer.

The file is a DAG: a rewind/fork appends records whose parent is an earlier
node, leaving the abandoned branch in place. Only the chain from the active
leaf back to the root is rendered. Compaction inserts a `compact_boundary`
record with no parent but a `logicalParentUuid`; the walk follows it.

Standalone: python sessions_transcript.py <file.jsonl> [--json]
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sessions_index import iter_records  # noqa: E402

MESSAGE_TYPES = ("user", "assistant")
CONTEXT_ATTACHMENTS = ("hook_success", "hook_additional_context")
COMMAND_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
STDOUT_RE = re.compile(r"<local-command-stdout>(.*?)</local-command-stdout>", re.S)


def flatten(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "image":
                parts.append("[image]")
        return "\n".join(parts)
    return ""


def load_nodes(path: Path):
    ordered = []
    by_uuid = {}
    for o in iter_records(path):
        uuid = o.get("uuid")
        if not uuid:
            continue
        ordered.append(o)
        by_uuid[uuid] = o
    return ordered, by_uuid


def find_leaf(ordered):
    for o in reversed(ordered):
        if o.get("type") in MESSAGE_TYPES and not o.get("isSidechain"):
            return o
    return ordered[-1] if ordered else None


def active_chain(ordered, by_uuid):
    chain = []
    visited = set()
    node = find_leaf(ordered)
    while node is not None and node["uuid"] not in visited:
        visited.add(node["uuid"])
        chain.append(node)
        parent = node.get("parentUuid") or node.get("logicalParentUuid")
        node = by_uuid.get(parent) if parent else None
    chain.reverse()
    return chain, visited


def user_turn(o):
    content = (o.get("message") or {}).get("content")
    text = ""
    images = 0
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text += ("\n" if text else "") + block.get("text", "")
            elif block.get("type") == "image":
                images += 1
    text = text.strip()
    ts = o.get("timestamp")
    if o.get("isCompactSummary"):
        return {"kind": "compact", "ts": ts, "text": text}
    if text.startswith("<command-name>"):
        command = COMMAND_RE.search(text)
        args = ARGS_RE.search(text)
        name = command.group(1).strip() if command else ""
        return {"kind": "command", "ts": ts, "text": (name + " " + (args.group(1).strip() if args else "")).strip()}
    if text.startswith("<local-command-stdout>"):
        stdout = STDOUT_RE.search(text)
        return {"kind": "stdout", "ts": ts, "text": stdout.group(1).strip() if stdout else text}
    if o.get("isMeta"):
        return {"kind": "context", "ts": ts, "name": "injected context", "text": text}
    if not text and not images:
        return None
    return {"kind": "user", "ts": ts, "text": text, "images": images}


def tool_results(o):
    content = (o.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [
        (block.get("tool_use_id"), {"text": flatten(block.get("content")), "isError": bool(block.get("is_error"))})
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]


def attachment_turn(o):
    att = o.get("attachment") or {}
    if att.get("type") not in CONTEXT_ATTACHMENTS:
        return None
    text = att.get("content") or att.get("text") or ""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    if not text.strip():
        return None
    return {"kind": "context", "ts": o.get("timestamp"), "name": att.get("hookName") or att.get("type"), "text": text}


def assistant_block(block, tool_uses):
    btype = block.get("type")
    if btype == "text":
        return {"type": "text", "text": block.get("text", "")}
    if btype == "thinking":
        return {"type": "thinking", "text": block.get("thinking", "")}
    if btype == "tool_use":
        entry = {"type": "tool_use", "id": block.get("id"), "name": block.get("name"), "input": block.get("input"), "result": None}
        tool_uses[block.get("id")] = entry
        return entry
    return None


def build_turns(chain):
    turns = []
    tool_uses = {}
    current = None  # assistant records come one per content block, sharing message.id
    for o in chain:
        kind = o.get("type")
        if kind == "assistant":
            message = o.get("message") or {}
            if current is None or current["_id"] != message.get("id"):
                current = {"kind": "assistant", "_id": message.get("id"), "ts": o.get("timestamp"),
                           "model": message.get("model"), "blocks": [], "usage": None, "durationMs": None}
                turns.append(current)
            usage = message.get("usage") or {}
            if usage:
                current["usage"] = {"input": usage.get("input_tokens"), "output": usage.get("output_tokens"),
                                    "cacheRead": usage.get("cache_read_input_tokens"),
                                    "cacheWrite": usage.get("cache_creation_input_tokens")}
            for block in message.get("content") or []:
                entry = assistant_block(block, tool_uses) if isinstance(block, dict) else None
                if entry:
                    current["blocks"].append(entry)
            continue
        current = None
        if kind == "user":
            pairs = tool_results(o)
            for tool_id, result in pairs:
                if tool_id in tool_uses:
                    tool_uses[tool_id]["result"] = result
            turn = None if pairs else user_turn(o)
        elif kind == "attachment":
            turn = attachment_turn(o)
        elif kind == "system" and o.get("subtype") == "compact_boundary":
            meta = o.get("compactMetadata") or {}
            turn = {"kind": "boundary", "ts": o.get("timestamp"), "preTokens": meta.get("preTokens"),
                    "postTokens": meta.get("postTokens"), "trigger": meta.get("trigger")}
        elif kind == "system" and o.get("subtype") == "turn_duration":
            turn = None
            for previous in reversed(turns):
                if previous["kind"] == "assistant":
                    previous["durationMs"] = o.get("durationMs")
                    break
        else:
            turn = None
        if turn:
            turns.append(turn)
    for turn in turns:
        turn.pop("_id", None)
    return turns


def load_transcript(path: Path) -> dict:
    ordered, by_uuid = load_nodes(path)
    chain, visited = active_chain(ordered, by_uuid)
    abandoned = sum(1 for o in ordered
                    if o.get("type") in MESSAGE_TYPES and not o.get("isSidechain") and o["uuid"] not in visited)
    return {"turns": build_turns(chain), "abandoned": abandoned, "records": len(ordered)}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        print(__doc__)
        return 2
    result = load_transcript(Path(files[0]))
    if "--json" in argv:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
        return 0
    print(f"records={result['records']} turns={len(result['turns'])} abandoned={result['abandoned']}")
    for turn in result["turns"]:
        if turn["kind"] == "assistant":
            summary = ", ".join((b["name"] if b["type"] == "tool_use" else b["type"]) for b in turn["blocks"])
            print(f"  assistant [{turn.get('model')}] {summary}")
        else:
            preview = (turn.get("text") or "").replace("\n", " ")[:100]
            print(f"  {turn['kind']:9} {turn.get('name', '')} {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
