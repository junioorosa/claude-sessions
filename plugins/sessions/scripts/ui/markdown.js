/* Small, HTML-escaping markdown subset: fences, headings, lists, quotes, tables, inline code/bold/italic/links. */
import { esc } from "./util.js";

export function inline(text) {
  const codes = [];
  let out = esc(text).replace(/`([^`\n]+)`/g, (m, code) => { codes.push(code); return "\u0000" + (codes.length - 1) + "\u0000"; });
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  out = out.replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  return out.replace(/\u0000(\d+)\u0000/g, (m, i) => "<code>" + codes[Number(i)] + "</code>");
}

export function markdown(text) {
  const lines = (text || "").replace(/\r\n?/g, "\n").split("\n");
  const html = [];
  const paragraph = [];
  const flush = () => { if (paragraph.length) { html.push("<p>" + paragraph.map(inline).join("<br>") + "</p>"); paragraph.length = 0; } };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*(```|~~~)\s*(\S*)/);
    if (fence) {
      flush();
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) buf.push(lines[i++]);
      i++;
      html.push('<pre><code class="lang-' + esc(fence[2]) + '">' + esc(buf.join("\n")) + "</code></pre>");
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)/);
    if (heading) { flush(); html.push("<h" + heading[1].length + ">" + inline(heading[2]) + "</h" + heading[1].length + ">"); i++; continue; }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { flush(); html.push("<hr>"); i++; continue; }
    if (/^\s*>/.test(line)) {
      flush();
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      html.push("<blockquote>" + markdown(buf.join("\n")) + "</blockquote>");
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flush();
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      let table = "<table><thead><tr>" + cells(rows[0]).map((c) => "<th>" + c + "</th>").join("") + "</tr></thead><tbody>";
      for (const r of rows.slice(2)) table += "<tr>" + cells(r).map((c) => "<td>" + c + "</td>").join("") + "</tr>";
      html.push(table + "</tbody></table>");
      continue;
    }
    const list = line.match(/^\s*([-*+]|\d+[.)])\s+/);
    if (list) {
      flush();
      const ordered = /\d/.test(list[1]);
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*([-*+]|\d+[.)])\s+(.*)/);
        if (m) items.push(m[2]);
        else if (items.length && /^\s{2,}\S/.test(lines[i])) items[items.length - 1] += "\n" + lines[i].trim();
        else break;
        i++;
      }
      html.push((ordered ? "<ol>" : "<ul>") + items.map((it) => "<li>" + it.split("\n").map(inline).join("<br>") + "</li>").join("") + (ordered ? "</ol>" : "</ul>"));
      continue;
    }
    if (!line.trim()) { flush(); i++; continue; }
    paragraph.push(line);
    i++;
  }
  flush();
  return html.join("\n");
}
