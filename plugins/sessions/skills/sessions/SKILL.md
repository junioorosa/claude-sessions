---
name: sessions
description: Open the local Claude Code sessions viewer (sidebar with pinned sessions, groups, search, transcripts and one-click resume). Use when the user runs /sessions.
disable-model-invocation: true
allowed-tools: Bash
argument-hint: "[stop | reindex | status]"
---

# /sessions

The plugin's `UserPromptSubmit` hook normally handles `/sessions` before the model sees it (zero tokens): it starts the local viewer server if needed, opens the browser and blocks the prompt.

If this skill is running, the hook did not fire (hooks disabled, or the plugin was loaded without `/reload-plugins`). Do the same thing by hand — run exactly this and report the URL it prints:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/hook.sh" open $ARGUMENTS
```

Arguments: none opens the viewer; `stop` shuts the server down; `reindex` rebuilds the index from scratch; `status` prints whether a server is running for the current config dir.
