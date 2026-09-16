/* Server-sent events from /api/events. Events burst while Claude answers, so
   they are coalesced and delivered as one Set of changed session ids. */

const COALESCE_MS = 300;

export function connectEvents(onChanged) {
  if (typeof EventSource === "undefined") return null;  // the 30s poll still works
  const source = new EventSource("/api/events");
  let timer = null;
  let pending = new Set();
  source.onmessage = (e) => {
    let event;
    try { event = JSON.parse(e.data); } catch (err) { return; }
    if (event.type !== "changed") return;
    for (const id of event.sessions || []) pending.add(id);
    clearTimeout(timer);
    timer = setTimeout(() => {
      const ids = pending;
      pending = new Set();
      onChanged(ids);
    }, COALESCE_MS);
  };
  return source;
}
