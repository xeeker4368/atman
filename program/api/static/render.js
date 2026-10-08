// The one renderer for the entity's replies. A pure function: text in, HTML string out.
//
// Order matters and is the whole safety argument. Every character that means something in
// HTML is escaped FIRST; only then are two things converted, from the escaped text:
//   **bold**                         -> <strong>
//   lines starting "- ", "* ", "N. " -> a list
// Everything else stays plain text, with line breaks kept. No links, no images, no other
// markdown. Because escaping runs first, nothing a reply contains can become a tag: the only
// tags in the output are the ones written below.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ESCAPES[ch]);
}

const BULLET = /^\s{0,3}[-*] (.*)$/;
const NUMBERED = /^\s{0,3}(\d{1,9})\. (.*)$/;

function inline(escaped) {
  // Non-greedy and on one line only; an unpaired ** stays as typed.
  return escaped.replace(/\*\*(\S(?:.*?\S)?)\*\*/g, "<strong>$1</strong>");
}

export function renderReply(text) {
  const lines = escapeHtml(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let plain = [];
  let list = null; // { tag, items, start }

  const flushPlain = () => {
    if (plain.length) out.push(`<p>${plain.map(inline).join("<br>")}</p>`);
    plain = [];
  };
  const flushList = () => {
    if (!list) return;
    const start = list.tag === "ol" && list.start !== 1 ? ` start="${list.start}"` : "";
    const items = list.items.map((item) => `<li>${inline(item)}</li>`).join("");
    out.push(`<${list.tag}${start}>${items}</${list.tag}>`);
    list = null;
  };

  for (const line of lines) {
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    if (bullet || numbered) {
      const tag = bullet ? "ul" : "ol";
      flushPlain();
      if (list && list.tag !== tag) flushList();
      if (!list) list = { tag, items: [], start: numbered ? Number(numbered[1]) : 1 };
      list.items.push(bullet ? bullet[1] : numbered[2]);
    } else if (line.trim() === "") {
      flushList();
      flushPlain();
    } else {
      flushList();
      plain.push(line);
    }
  }
  flushList();
  flushPlain();
  return out.join("");
}
