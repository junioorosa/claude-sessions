import { $, el, fmtDuration, fmtTokens } from "./util.js";
import { markdown } from "./markdown.js";

const RESULT_LIMIT = 20000;

export function renderTranscript(data, box) {
  box.innerHTML = "";
  const inner = el("div", { class: "transcript-inner" });
  // One assistant turn per API call; a run between two prompts reads as one reply.
  const turns = data.turns;
  for (let i = 0; i < turns.length; i++) {
    if (turns[i].kind !== "assistant") { inner.appendChild(renderTurn(turns[i])); continue; }
    let last = i;
    while (last + 1 < turns.length && turns[last + 1].kind === "assistant") last++;
    inner.appendChild(renderAssistantRun(turns.slice(i, last + 1)));
    i = last;
  }
  box.appendChild(inner);
  applyToggles();
  const stats = [turns.filter((t) => t.kind === "user").length + " prompts"];
  if (data.abandoned) stats.push(data.abandoned + " messages on abandoned branches");
  $("#transcript-stats").textContent = stats.join(" · ");
}

export function applyToggles() {
  const show = { tool: $("#show-tools").checked, thinking: $("#show-thinking").checked, context: $("#show-context").checked };
  for (const node of document.querySelectorAll("#transcript [data-kind]")) node.hidden = !show[node.getAttribute("data-kind")];
}

function who(label, turn, extra) {
  return el("div", { class: "who" }, [label, el("span", { class: "ts", text: turn.ts ? new Date(turn.ts).toLocaleTimeString() : "" })].concat(extra || []));
}

function details(cls, summaryText, body, mode) {
  const node = el("details", { class: cls }, [el("summary", {}, [el("span", { class: "name", text: summaryText })])]);
  const inner = el("div", { class: "inner" });
  if (mode === "pre") inner.appendChild(el("pre", { text: body }));
  else inner.textContent = body;
  node.appendChild(inner);
  return node;
}

function renderTurn(turn) {
  switch (turn.kind) {
    case "user":
      return el("div", { class: "turn user" }, [
        who("You", turn, turn.images ? [el("span", { class: "chip", text: turn.images + " image" + (turn.images > 1 ? "s" : "") })] : []),
        el("div", { class: "bubble", text: turn.text }),
      ]);
    case "assistant":
      return renderAssistantRun([turn]);
    case "command":
      return el("div", { class: "turn command" }, [who("Command", turn), el("span", { class: "bubble", text: turn.text })]);
    case "stdout":
      return el("div", { class: "turn command" }, [details("block context", "output", turn.text, "pre")]);
    case "context":
      return el("div", { class: "turn context", "data-kind": "context" }, [details("block context", turn.name || "context", turn.text, "text")]);
    case "compact":
      return el("div", { class: "turn compact" }, [details("block compact", "Context summary (compacted)", turn.text, "text")]);
    case "boundary":
      return el("div", { class: "turn boundary", text: "context compacted" + (turn.preTokens ? ": " + fmtTokens(turn.preTokens) + " → " + fmtTokens(turn.postTokens) + " tokens" : "") + (turn.trigger ? " (" + turn.trigger + ")" : "") });
    default:
      return el("div", { class: "turn", text: JSON.stringify(turn) });
  }
}

function renderAssistantRun(run) {
  const models = [...new Set(run.map((t) => t.model).filter(Boolean))];
  const sum = (pick) => run.reduce((acc, t) => acc + (pick(t) || 0), 0);
  const fresh = sum((t) => t.usage && (t.usage.input + (t.usage.cacheWrite || 0)));
  const cached = sum((t) => t.usage && t.usage.cacheRead);
  const output = sum((t) => t.usage && t.usage.output);
  const duration = sum((t) => t.durationMs);
  const chips = models.map((m) => el("span", { class: "chip", text: m }));
  if (run.length > 1) chips.push(el("span", { class: "chip", text: run.length + " steps" }));
  if (duration) chips.push(el("span", { class: "chip", text: fmtDuration(duration) }));
  if (fresh || output) {
    chips.push(el("span", { class: "chip", title: "new input + cached input → output tokens", text: fmtTokens(fresh) + " + " + fmtTokens(cached) + " cached → " + fmtTokens(output) }));
  }
  const node = el("div", { class: "turn assistant" }, [who("Claude", run[0], chips)]);
  for (const turn of run) {
    for (const block of turn.blocks) {
      if (block.type === "text") node.appendChild(el("div", { class: "md", html: markdown(block.text) }));
      else if (block.type === "thinking") node.appendChild(el("div", { "data-kind": "thinking" }, [details("block thinking", "thinking", block.text, "text")]));
      else if (block.type === "tool_use") node.appendChild(renderTool(block));
    }
  }
  return node;
}

function toolHint(block) {
  const input = block.input || {};
  switch (block.name) {
    case "Bash": case "PowerShell": return input.description || input.command || "";
    case "Read": return (input.file_path || "") + (input.offset ? " @" + input.offset : "");
    case "Edit": case "Write": case "NotebookEdit": return input.file_path || input.notebook_path || "";
    case "Grep": return input.pattern + (input.path ? "  in " + input.path : "");
    case "Glob": return input.pattern || "";
    case "WebFetch": return input.url || "";
    case "WebSearch": return input.query || "";
    case "Agent": return input.description || "";
    case "Skill": return input.skill || "";
    default: {
      const text = JSON.stringify(input);
      return text.length > 120 ? text.slice(0, 119) + "…" : text;
    }
  }
}

function renderTool(block) {
  const summary = el("summary", {}, [
    el("span", { class: "name", text: block.name }),
    el("span", { class: "hint", text: toolHint(block) }),
    block.result && block.result.isError ? el("span", { class: "err", text: "error" }) : null,
  ]);
  const inner = el("div", { class: "inner" });
  const input = block.input || {};
  if (block.name === "Edit") {
    inner.append(el("div", { class: "sub", text: "old" }), el("pre", { text: input.old_string || "" }),
      el("div", { class: "sub", text: "new" }), el("pre", { text: input.new_string || "" }));
  } else if (block.name === "Bash" || block.name === "PowerShell") {
    inner.appendChild(el("pre", { text: input.command || "" }));
  } else if (block.name === "Write") {
    inner.appendChild(el("pre", { text: input.content || "" }));
  } else {
    inner.appendChild(el("pre", { text: JSON.stringify(input, null, 2) }));
  }
  if (block.result) {
    const text = block.result.text || "";
    inner.appendChild(el("div", { class: "sub", text: "result" + (text.length > RESULT_LIMIT ? " (first " + RESULT_LIMIT + " chars of " + text.length + ")" : "") }));
    inner.appendChild(el("pre", { text: text.slice(0, RESULT_LIMIT) || "(empty)" }));
  } else {
    inner.appendChild(el("div", { class: "sub", text: "no result recorded" }));
  }
  return el("div", { "data-kind": "tool" }, [el("details", { class: "block tool" }, [summary, inner])]);
}
