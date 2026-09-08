# 2026-09-08 — Task 2.5: the `web_fetch` tool

**Tier 1 · Sonnet · not gated.** The SSRF guard is the substance; everything else
is ordinary work around it.

## Files changed

Created: `program/tools/web_fetch.py`, `tests/test_web_fetch.py` (59).
Modified: `program/tools/catalog.py`, `program/config.py`,
`config/defaults.toml`, `tests/test_tools.py` (catalogue guards), `BUILT.md`.

No schema change, **no new dependency** — HTML extraction uses the standard
library's `HTMLParser`, matching the precedent set when `scrypt` was chosen over
a password library. 576 tests pass (was 517); `ruff check` clean.

## The SSRF guard

The threat is concrete rather than theoretical. The **model** chooses the URL,
and this build's own `web_search` puts attacker-writable text into the prompt on
every search — so the realistic attacker is *content*, and the target is
everything this host can reach that the LAN cannot: Ollama on `127.0.0.1:11434`,
SearXNG on `127.0.0.1:8080`, the Anam API itself, `169.254.169.254`, and every
device on `192.168.0.0/24`.

Five layers. Each closes a hole the previous one leaves:

**0. `session.trust_env = False`.** Not housekeeping, and the one I would most
easily have missed. With the default, an `HTTP_PROXY` environment variable makes
`requests` connect to the *proxy* rather than to the address we validated, and
every other layer becomes decoration. Verified by running it: with `HTTP_PROXY`
pointed at a dead port, a default session raises `ProxyError` — it went to the
proxy — while a `trust_env=False` session connects normally.

**1. Scheme allowlist**, on the parsed URL, before anything else. `file://`,
`ftp://`, `gopher://`, `data:`, `javascript:`, `ws://` refused **by name**.
"The library probably won't do that" is not a control.

**2. Resolve, then validate every address.** A hostname is not what connects to a
socket. `getaddrinfo()` is called explicitly and **all** returned addresses are
checked — a name with one public and one private A record would otherwise pass on
whichever the resolver happened to order first. IPv4-mapped IPv6
(`::ffff:127.0.0.1`) is unwrapped before classification.

Classification was **verified against Python's own `ipaddress`** rather than
taken from the docs: `is_private` does cover every range BUILD_PLAN enumerates
(RFC 1918, `127.0.0.0/8`, `169.254.0.0/16`, `::1`, `fe80::/10`, `fc00::/7`), and
`172.32.0.1` is correctly *outside* the /12. Multicast is **not** covered by
`is_private` and gets its own check. An explicit `BLOCKED_NETWORKS` tuple is kept
alongside — redundant today, and there so the intent stays greppable and a change
in stdlib classification cannot silently open a hole.

**3. Verify the address actually connected to, before any body is read.** Layers
1–2 are a check, and a check has a gap: DNS can answer differently between our
lookup and the client's connect. That is DNS rebinding, and it defeats
resolve-then-hope entirely. So the response is streamed, the real peer address is
read off the socket (`response.raw._connection.sock.getpeername()`, confirmed
available for both HTTP and HTTPS before the body), and re-validated **before a
byte of body is read** — the body being what would reach the model.

This reaches into private API, so it **fails closed**: if the peer cannot be
determined, the fetch is refused. A future break in that internal shape stops the
tool loudly instead of quietly deleting the layer.

**4. Redirects followed by hand.** `allow_redirects=False`; each `Location` is
resolved against the current URL and put through layers 1–3 again. `requests`'
own redirect handling would follow a hop to `http://169.254.169.254/` without
asking anyone.

### The redirect decision

**Followed, up to 3 hops, every hop fully re-validated.** Refusing redirects
outright would fail on a large share of ordinary URLs — `http`→`https` and
apex→`www` are ubiquitous — for no security gain, because a validated hop is
exactly as safe as a validated first request. Three covers those two plus one
link shortener; beyond that is rare, and each hop spends the fetch's shared
deadline, so the limit bounds time as well as indirection.

### One gap the tests found, fixed in the code

A test patched the resolver and watched `http://169.254.169.254/` sail through:
an **IP literal in the URL** was being validated via `getaddrinfo` rather than as
itself. In production the resolver returns the literal unchanged, so the
behaviour was right by luck. It now validates a literal directly and never
consults the resolver for one — the point of the layer is that the target is not
taken on trust.

### Proved, not asserted

Refusal tests assert the URL **never reached the wire**: the HTTP layer is
replaced with something that raises, so a passing test means the guard refused
rather than the network failing.

`test_a_running_local_service_is_refused_while_being_reachable` first confirms
SearXNG really is answering on `127.0.0.1:8080`, *then* asserts refusal — so it
cannot be "nothing was listening" wearing a guard's clothes. Public addresses are
asserted to be **allowed**, because a guard that refuses everything would pass
every refusal test.

Each layer was then broken and the suite re-run:

| Break | Result |
|---|---|
| layer 3 removed | rebinding and fail-closed tests fail |
| only the first resolved address checked | multi-record test fails |
| `allow_redirects=True` | redirect test fails |

## Content handling

* **HTML** — text extracted with a stdlib `HTMLParser` that drops whole
  `script`/`style`/`nav`/`footer`/`head` subtrees rather than stripping tags. A
  raw body with tags removed still carries an entire minified script.
* **`text/*`, JSON, XML** — used as-is.
* **PDF** — **metadata only**, saying plainly that this tool does not read PDFs.
  Decision #11's PDF extraction belongs to file ingestion, which owns that
  dependency; claiming to have read a PDF and returning nothing would be worse
  than saying so.
* **images, video, binary, unknown** — described (type and size), never dumped.
* Malformed markup falls back to a tag strip rather than failing the fetch.

## Judgment values

**`timeout_seconds = 25`** — same rule as the previous two tools: above the
longest bound the code inside enforces, so a real failure surfaces as its own
error rather than as `TIMEOUT`, which means *outcome unknown*. Inside is
`web_fetch.total_timeout_seconds = 20`, a ceiling across **all** hops (each hop
gets what remains, so redirects cannot multiply the wait).

Unlike `memory_search` (SQLite's `busy_timeout`) and `web_search` (SearXNG's 3 s
per-engine ceiling), **nothing inside this tool bounds itself** — the remote
server decides. So the ceiling comes from what a turn can afford:
`agent.tool_budget_seconds` is 120, and a `web_search` (15) plus three fetches is
90, leaving room for a retry. Measured real fetches — sqlite.org 210 kB in
0.29 s, python.org 55 kB in 0.11 s, example.com in 0.04 s — so 20 s is ~70x the
observed case and the margin is entirely for a slow remote.

**Render budget: measured, not fixed.** `web_fetch.max_text_chars = 3500` is the
preferred body cap, but the header carries the URL — printed **twice** when a
redirect is reported — plus a page title, and a long URL with a long title runs
to a thousand characters on its own. A fixed body cap plus an unbounded header
can exceed `agent.max_tool_result_chars` and be cut by the loop mid-sentence. So
the header is built and measured first and the body takes the remainder, the
order `prompt.assemble_turn()` already uses for the same reason. A pathological
case — 400-character URL printed twice, 300-character title, truncated download —
renders at **3,996 against the 4,000 cap**, bounded by construction.

`max_download_bytes = 2,000,000` — ~10x the largest real page measured (211 kB).

**One parameter, `url`.** No extraction hint, no selector, no character
override. A hint would imply targeted extraction this tool does not do — the
"reads as built and isn't" failure this project keeps naming — and a character
override would let the model ask for more than the loop will carry and get a
*truncated* result back. Consistent with `memory_search` and `web_search`.

## Verified live, and one honest limitation

The model fetched `https://www.sqlite.org/fts5.html` in **0.28 s of the 25 s
allowed**, 210,915 bytes reduced to 3,751 rendered characters, and answered
correctly.

**But the turn took 4 iterations, and the reason is worth recording rather than
smoothing over.** The section it needed was past the 3,500-character cap, so it
**re-fetched the identical URL**, got identical text, then fell back to
`web_search` and answered from that. Two things are visible:

* **There is no way to request a later part of a page.** That is a direct
  consequence of the one-parameter decision above, and it is evidence against it
  — an `offset` parameter would be a real capability rather than a tuning knob.
  Flagged for the reviewer rather than decided here.
* **The loop has no duplicate-call detection.** A known non-feature from task
  2.2, now with a concrete example: an identical call burned an iteration.
