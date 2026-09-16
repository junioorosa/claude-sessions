#!/usr/bin/env bash
# Hook mode (no args): UserPromptSubmit payload on stdin. CLI mode: open|stop|reindex|status.
# Never fails the prompt: without a Python 3 it exits 0 silently.

set -u

[ -f "${SESSIONS_HOOKS_DISABLED_FILE:-$HOME/.claude/sessions-disabled}" ] && exit 0
[ "${SESSIONS_HOOKS_DISABLED:-0}" = "1" ] && exit 0

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/sessions_intercept.py"
[ -f "$SCRIPT" ] || exit 0

PY="${SESSIONS_PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    PY=python3
  elif command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    PY=python
  elif command -v py >/dev/null 2>&1; then
    PY="py -3"
  else
    exit 0
  fi
fi

# Git Bash: the interpreter is a native binary and wants a native path.
if command -v cygpath >/dev/null 2>&1; then
  SCRIPT="$(cygpath -w "$SCRIPT")"
fi

exec $PY "$SCRIPT" "$@"
