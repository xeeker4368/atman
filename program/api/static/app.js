// The chat page (docs/DECISIONS.md #31): plain JavaScript, no framework, no build step.
//
// One state object, changed only by the functions below, and the page redrawn from it; no
// polling. Rules this file keeps:
//   - Every server string (replies, the person's own messages, previews, receipts, the trace)
//     is set with textContent. The one innerHTML is the output of renderReply(), which escapes
//     before it converts anything (render.js).
//   - The session token lives in sessionStorage only: per tab, so two people can be logged in
//     in two tabs. Any 401 clears it and returns to the login form. Tokens are stateless, so
//     logging out forgets the token here; it does not revoke it, and it stays valid until it
//     expires.
//   - No streaming. POST /api/chat answers once, after the integrity check has run, so the
//     page waits and then shows the whole reply.

import { renderReply } from "./render.js";

const TOKEN_KEY = "anam.session";

const state = {
  me: null, // { name, role }
  conversations: [],
  current: null, // conversation id, or null for a new one
  busy: false,
};

const $ = (id) => document.getElementById(id);

// --- token -------------------------------------------------------------------

function readToken() {
  try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}
function writeToken(token) {
  try { sessionStorage.setItem(TOKEN_KEY, token); } catch { /* the page still works this tab */ }
}
function clearToken() {
  try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* nothing stored */ }
}

// --- talking to the server ---------------------------------------------------

class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

function describeDetail(detail, status) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI's 422: a list of {loc, msg}.
    return detail.map((d) => (d && d.msg ? `${(d.loc || []).join(".")}: ${d.msg}` : String(d))).join("\n");
  }
  return `The server answered ${status}.`;
}

async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  const token = readToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "The server could not be reached. Is the backend running?");
  }
  let data = null;
  try { data = await response.json(); } catch { /* not JSON */ }

  if (response.status === 401) {
    showLogin();
    throw new ApiError(401, "Logged out.");
  }
  if (!response.ok) throw new ApiError(response.status, describeDetail(data && data.detail, response.status));
  return data;
}

// --- small DOM helpers -------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function localTime(iso) {
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? String(iso) : when.toLocaleString();
}

function show(node, text) {
  node.textContent = text;
  node.hidden = !text;
}

// --- views -------------------------------------------------------------------

function showLogin(message) {
  clearToken();
  state.me = null;
  state.conversations = [];
  state.current = null;
  $("chat-view").hidden = true;
  $("login-view").hidden = false;
  $("messages").replaceChildren();
  show($("login-error"), message || "");
  $("login-password").value = "";
  $("login-name").focus();
}

async function showChat() {
  $("login-view").hidden = true;
  $("chat-view").hidden = false;
  state.me = await api("/api/me");
  $("who-label").textContent = `Logged in as ${state.me.name}`;
  await refreshConversations();
  startNew();
}

function startNew() {
  state.current = null;
  $("messages").replaceChildren();
  show($("notice"), "");
  show($("send-error"), "");
  drawConversationList();
  $("message-input").focus();
}

async function refreshConversations() {
  state.conversations = await api("/api/conversations");
  drawConversationList();
}

function drawConversationList() {
  const list = $("conversation-list");
  list.replaceChildren();
  for (const c of state.conversations) {
    const button = el("button");
    button.type = "button";
    button.setAttribute("aria-current", String(c.id === state.current));
    const when = `${localTime(c.started_at)}${c.ended_at ? " · closed" : ""}`;
    button.append(el("span", "when", when));
    const preview = c.preview === null ? "(no message)" : c.preview + (c.preview_cut ? "…" : "");
    button.append(el("span", "preview", preview));
    button.addEventListener("click", () => openConversation(c.id));
    const item = el("li");
    item.append(button);
    list.append(item);
  }
}

async function openConversation(id) {
  if (state.busy) return;
  show($("notice"), "");
  show($("send-error"), "");
  try {
    const detail = await api(`/api/conversations/${encodeURIComponent(id)}/messages`);
    state.current = detail.id;
    const box = $("messages");
    box.replaceChildren();
    for (const m of detail.messages) {
      box.append(m.role === "assistant" ? replyNode(m.content, m.timestamp, m.receipts, m.trace) : userNode(m.content, m.timestamp));
    }
    if (detail.ended_at) {
      show($("notice"), "This conversation is closed. Sending a message starts a new one.");
    }
    drawConversationList();
    box.scrollTop = box.scrollHeight;
  } catch (err) {
    if (err.status !== 401) show($("send-error"), err.message);
  }
}

// --- messages ----------------------------------------------------------------

function userNode(content, timestamp) {
  const node = el("article", "turn user");
  node.append(el("div", "meta", `You · ${localTime(timestamp)}`));
  node.append(el("div", "body", content));
  return node;
}

const ARTIFACT_OUTCOMES = {
  saved: "saved",
  not_saved: "not saved; nothing was recorded",
  unknown: "unknown; whether it was saved could not be confirmed",
};

function receiptLine(r) {
  if ("artifact_id" in r) {
    const words = ARTIFACT_OUTCOMES[r.outcome] || r.outcome;
    const parts = [`${r.tool}: ${words}`];
    if (r.artifact_id) parts.push(`${r.artifact_type || "record"} ${String(r.artifact_id).slice(0, 8)}`);
    if (r.created_at) parts.push(localTime(r.created_at));
    return parts.join(" · ");
  }
  return `${r.tool}: ${r.text || r.outcome}`;
}

function receiptsNode(receipts) {
  // null on a stored reply means its tool record could not be read: say so, never "nothing".
  if (Array.isArray(receipts) && receipts.length === 0) return null;
  const box = el("div", "receipts");
  box.append(el("span", "label", "System record (not part of the reply)"));
  if (!Array.isArray(receipts)) {
    box.append(el("div", null, "This turn's tool record could not be read, so whether anything was saved is unknown."));
    return box;
  }
  const list = el("ul");
  for (const r of receipts) list.append(el("li", null, receiptLine(r)));
  box.append(list);
  return box;
}

// Shown for admin as a display choice; it protects nothing. The entity's own writing is
// already replaced by its length in the API response (program/api/trace_view.py).
function traceNode(trace) {
  if (!state.me || state.me.role !== "admin" || !Array.isArray(trace) || trace.length === 0) return null;
  const details = el("details", "trace");
  details.append(el("summary", null, `Trace (${trace.length} ${trace.length === 1 ? "entry" : "entries"})`));
  details.append(el("pre", null, JSON.stringify(trace, null, 2)));
  return details;
}

function replyNode(content, timestamp, receipts, trace) {
  const node = el("article", "turn assistant");
  node.append(el("div", "meta", `Reply · ${localTime(timestamp)}`));
  const body = el("div", "body");
  body.innerHTML = renderReply(content); // escaped before anything is converted (render.js)
  node.append(body);
  const r = receiptsNode(receipts);
  if (r) node.append(r);
  const t = traceNode(trace);
  if (t) node.append(t);
  return node;
}

// --- sending -----------------------------------------------------------------

function setBusy(busy) {
  state.busy = busy;
  $("send-button").disabled = busy;
  $("new-button").disabled = busy;
  $("message-input").readOnly = busy;
  $("waiting").hidden = !busy;
}

async function send(text) {
  const box = $("messages");
  const sent = userNode(text, new Date().toISOString());
  box.append(sent);
  box.scrollTop = box.scrollHeight;
  show($("send-error"), "");
  setBusy(true);
  const asked = state.current;
  try {
    const reply = await api("/api/chat", { method: "POST", body: { message: text, conversation_id: asked } });
    if (reply.new_conversation && asked !== null) {
      show($("notice"), "That conversation had closed, so this message started a new one.");
    } else if (reply.new_conversation) {
      show($("notice"), "Started a new conversation.");
    }
    state.current = reply.conversation_id;
    box.append(replyNode(reply.content, new Date().toISOString(), reply.receipts, reply.trace));
    box.scrollTop = box.scrollHeight;
    $("message-input").value = "";
  } catch (err) {
    if (err.status === 401) return;
    sent.classList.add("unanswered");
    sent.querySelector(".meta").textContent += " · not answered";
    show($("send-error"), err.message);
  } finally {
    setBusy(false);
  }
  try { await refreshConversations(); } catch { /* the list refreshes on the next action */ }
}

// --- wiring ------------------------------------------------------------------

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("login-button");
  button.disabled = true;
  show($("login-error"), "");
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ name: $("login-name").value, password: $("login-password").value }),
    });
    if (!response.ok) {
      show($("login-error"), response.status === 401 ? "That name and password did not work." : `The server answered ${response.status}.`);
      return;
    }
    const data = await response.json();
    writeToken(data.token);
    $("login-password").value = "";
    await showChat();
  } catch (err) {
    if (err.status !== 401) show($("login-error"), err.message || "The server could not be reached.");
  } finally {
    button.disabled = false;
  }
});

$("logout-button").addEventListener("click", () => showLogin());
$("new-button").addEventListener("click", () => { if (!state.busy) startNew(); });

$("send-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("message-input").value.trim();
  if (!text || state.busy) return;
  send(text);
});

$("message-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("send-form").requestSubmit();
  }
});

if (readToken()) {
  showChat().catch((err) => { if (err.status !== 401) showLogin(err.message); });
} else {
  showLogin();
}
