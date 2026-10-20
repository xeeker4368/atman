# Brave Search as web_search's fallback (revision 2, BUILT, for review)

**Basis:** decision #38 (2026-10-19), with its order reversed by decision #39 (2026-10-20): **SearXNG is
the primary source and the Brave Search API is the fallback.** Revision 1 (Brave primary) was reviewed
on 2026-10-20; its decisions 1 and 3 were accepted, decision 2 (storage rights) is open, and decision 4
is replaced by the fallback rule below.

**Tier 3:** credential handling, and the paid calls are an external effect (a cost).

**What stays:**
- `web_search`'s name, parameters, description and header.
- The renderer, its caps and the 4,000-character loop cap.
- `NO_RESULTS` as the start of every empty result, so CO17 still detects one.
- c63b9ec's SearXNG notes.

## 1. The fallback rule and what the entity reads

**Config** (`config/defaults.toml` `[search]`, every key bootstrap-only):

| key | default | why |
|---|---|---|
| `fallback` | `"none"` | `"none"`: SearXNG alone, and the key is never read. `"brave"`: Brave is the fallback. `"none"` in the public defaults, so a clone without a key starts; Lyle sets `"brave"` in `local.toml` |
| `brave_timeout_seconds` | 5.0 | about 2× the worst latency in Lyle's run (2.66 s) |
| `brave_credit_usd`, `brave_price_per_1000_usd` | 5.0, 5.0 | the inputs to the stop |
| `brave_stop_monthly` | 980 | §3 |
| `brave_warn_monthly` | 900 | Lyle's figure |
| `brave_max_per_turn` | 3 | §3 |
| `store_snippets` | false | §5 |

Revision 1's `provider` key is renamed `fallback`, since Brave is no longer a provider choice
(decision 1 below).

**Brave is called** (`web_search.search_web`) only when `fallback = "brave"` and SearXNG:
- **errors**: it cannot be reached, answers non-200, returns a body that is not JSON or not an object, or
  returns no `results` key;
- **times out** (`searxng.timeout_seconds`, 10 s);
- **returns zero results while reporting unresponsive engines.**

SearXNG returning zero results with no engine reporting a failure is a real "nothing found", and Brave
is not called. A Brave answer with zero results is a real answer too, and nothing is retried.

**One consequence to know:** DuckDuckGo has refused SearXNG with a CAPTCHA on every query since
2026-10-19. So today every zero-result SearXNG search reports an unresponsive engine, and every one goes
to Brave.

**The request:** `GET https://api.search.brave.com/res/v1/web/search` (a module constant, so a config
typo cannot send the key elsewhere). `q` and `count = searxng.max_results` (6) go in the parameters;
`Accept` and `X-Subscription-Token` go in the headers. `web.results[]` maps to `title`, `url` and
`description` (with Brave's `<strong>` tags and entities removed), in Brave's order, through the same
renderer.

**The texts** (prompt-facing):

| case | what the model reads |
|---|---|
| SearXNG answered | header, `[Source: SearXNG.]`, results, then c63b9ec's note if engines were down |
| SearXNG found nothing, no engine down | `NO_RESULTS` + `[Source: SearXNG. Every engine it asked answered, and none had a match.]` |
| Brave answered | header, `[Source: Brave Search, the fallback. It ran because the primary search (SearXNG) {why}.]`, results |
| Brave answered with nothing | `NO_RESULTS` + `[Source: Brave Search, the fallback, which answered with no results. It ran because the primary search (SearXNG) {why}.]` |
| SearXNG found nothing with engines down, Brave unused or failed | `NO_RESULTS` + c63b9ec's "No search engine returned anything…" + `[The fallback search (Brave) {reason}.]` |
| SearXNG errored, Brave unused or failed | `tool_error`: `No search ran: the primary search (SearXNG) {why}, and the fallback search (Brave) {reason}.` |

- `{why}` is either `failed: it did not respond within 10s` (or `could not be reached`, `answered HTTP
  502`, `returned an answer that was not JSON`, `returned no result set`), or `returned nothing, and 1
  of its engines did not answer (duckduckgo)`. The engine names are clipped at 80 characters, because a
  worst-case render measured 4,018 characters before the clip.
- `{reason}` is one of:
  - `was not used: the paid search is used up for this month, until 1 November 2026 (UTC)`;
  - `was not used: this turn has already made 3 paid searches, the most one turn may make`;
  - `was not used: its key could not be read`;
  - `was not used: its usage count could not be recorded`;
  - `failed: it did not respond within 5s`, `could not be reached`, `answered HTTP 429`, or `returned
    an answer that was not usable`.

## 2. The key

- **Read only from `config/local.toml` `[search] brave_api_key`**, by `config.brave_api_key()`, which
  reads that file itself, at the moment of each call.
  - `config._load` drops the key from the merged configuration, so `config.get` and any dump of the
    config never hold it.
  - It is not in `_ENV_MAP`, `_FALLBACK` or `defaults.toml`, and tests fail if it appears there.
- **Startup refuses** (`lifespan`, right after the session secret, before anything touches disk) when
  `fallback = "brave"` and the key is:
  - missing;
  - not 20 to 128 characters of `[A-Za-z0-9_-]`;
  - a placeholder (`your`, `placeholder`, `changeme`, `example`, `replace` or `insert`).

  The message names the file and the key, never the value. Lyle's key passes (checked 2026-10-20,
  printing only the verdict).
- **Never logged, traced or put in a message.**
  - The key travels only in the header.
  - A failure is described by its kind and HTTP status, never by an exception's text (which carries
    the URL and query) and never by Brave's response body.
  - Strings still pass through a scrub.
  - `brave_calls` holds no key and no query.
- **Tests** use `BSA` plus 28 zeros in a temporary `local.toml`, and a fake `requests.get`.
  - `conftest` refuses any request to `api.search.brave.com`, live-marked or not, with a
    `BaseException`.
  - The leak test runs 9 paths with logging at DEBUG, including Brave errors whose own text and body
    contain the key. It finds the key in no log record, exception, trace entry, stored trace or table
    row.

## 3. Caps, count, warning and stop

**The arithmetic.** Brave's published pricing (https://brave.com/search/api/, read 2026-10-19) is "$5
per 1,000 requests" and "$5 in free credits every month".
- $5 ÷ $5 per 1,000 gives **1,000 calls a month** inside the credit.
- **The stop is 1,000 − 20 = 980.** The 20 are calls this store never sees: Lyle's tests, a comparison
  run, or **the play kit's own store**, which counts in its own `working.db`.
- The warning is at 900.
- 980 a month is about 32 a day; at 3 a turn, that is at least 10 turns a day that fall back.
- `tests/test_brave.py` recomputes the stop from the two dollar figures, and startup refuses a stop
  above the credit's calls.

**The count:** `working.db` `brave_calls` (migration 10).
- **The columns:** `id`, `month` (`YYYY-MM`, UTC), `called_at`, `outcome` (`sent`/`ok`/`failed`),
  `http_status`, `latency_seconds`, `month_limit`, `month_remaining`.
- **A call is reserved before it is sent**, by one `INSERT … SELECT … WHERE count < 980 AND NOT
  (Brave's monthly limit > 0 AND remaining ≤ 20)`.
  - That reservation is also the stop check, and it is the only database step before the call.
  - Two threads reserving at 979 let exactly one through (tested).
  - A sent call counts whatever happens to it.
  - It goes through `db.transaction()`. **`db.py` is unchanged.**

**Brave's headers.** Lyle's run recorded these:

| header | value |
|---|---|
| `x-ratelimit-limit` | `"50, 0"` |
| `x-ratelimit-policy` | `"50;w=1, 0;w=2678400"` |
| `x-ratelimit-remaining` | `"49, 0"` |
| `x-ratelimit-reset` | `"1, 1046159"` |

- Both figures are stored. The monthly figure is an earlier stop **only when its monthly limit is above
  0**. On this credit-billed plan it reads 0, so the local count is the only monthly stop.
- Both shapes are tested: limit 0 never stops, and a limit of 2,000 with 20 remaining does.
- The reset was 1,046,159 s on the run's last call, about 21:24 UTC on 19 October (the files' time).
  That lands within seconds of 00:00 UTC on 1 November, so Brave's window looks like the UTC calendar
  month. This is descriptive, from one reading.

**Per turn:** a `ContextVar` holds the turn's count.
- `loop.run_turn` opens it around the whole turn (`brave.turn_scope`).
- `registry._run_in_thread` now runs the handler in a copy of the caller's context, so the tool's
  thread sees the turn's count.
- It is not an argument, so the model cannot set it.
- Outside a turn (a script), only the monthly stop applies.

**At 900:**
- **The server log** gets a WARNING on the call that reaches 900 and on every tenth call after it.
- **`GET /api/me`** returns `notices` for the admin role only. The field is absent for any other role,
  decided on the server, so Jodie's browser never receives it.
- **`app.js`** shows the notice in a banner, set with `textContent` once at login; there is no polling.
- **`scripts/brave_usage.py`**, read-only (`mode=ro`), prints:
  - the month;
  - calls by outcome;
  - the warning and the stop;
  - calls left;
  - credit left (`$5 − used × $0.005`);
  - Brave's monthly header and whether it is used;
  - the fallback setting.

  It never reads the key.

**At 980:**
- No call is made, and the entity reads the "used up … until 1 November 2026 (UTC)" reason.
- The log gets one WARNING per month, per process.
- The admin notice says the paid search is used up.
- At 00:00 UTC on the 1st the month changes and calls resume. This is tested with a fake clock,
  including December into January.

## 4. The tool timeout and the in-flight grace floor

`web_search`'s timeout rises from 15 s to **38 s**. Its worst path is SearXNG failing slowly, then
Brave:

| term | seconds | from |
|---|---|---|
| SearXNG request | 10 | `searxng.timeout_seconds` |
| reserve the Brave call (one write) | 10 | `database.busy_timeout_seconds` |
| Brave request | 5 | `search.brave_timeout_seconds` |
| record the outcome (one write) | 10 | `database.busy_timeout_seconds` |
| margin | 3 | judgment, as before |
| **total** | **38** | `web_search.timeout_terms()` |

- The writes are terms because they run inside the tool's wait. Neither retries, so each waits at most
  one busy timeout for its lock.
- Both requests use `requests` timeouts, which bound the connect and each read, not the total. The
  tool timeout is the outer bound.

**The floor does not move.** Its tool term is the turn's aggregate `agent.tool_budget_seconds` (120 s),
and each dispatch is given `min(the tool's timeout, what is left of the budget)`. Re-run with the new
value:

| term | seconds |
|---|---|
| persist the user message | 40 |
| embedding | 300 |
| 5 model calls | 1,500 |
| gate classifier | 45 |
| tools, aggregate | 120 |
| persist the reply | 40 |
| **total** | **2,045** |

2,045 s is 34.1 minutes, rounded up to **35**. `tests/test_idle.py::test_the_floor_holds_for_every_tools_own_timeout`
checks three things:
- every catalogue tool's timeout is at most the budget;
- `web_search`'s timeout equals its terms;
- dispatch is cut to the budget left.

It then recomputes the floor. **What changes:** two slow searches and a `memory_search` (38 + 38 + 45 =
121 s) no longer all fit in one turn. The budget cuts the last one short, and that is recorded.

## 5. `search.store_snippets` (default off)

- **Off:** the handler returns a `StoredText`. The model reads the full results. `loop.storable_trace`,
  called by `turn.py` at the save, writes the stored form to `messages.tool_trace` (in `working.db` and
  `archive.db`):
  - each result's title and URL, without its snippet;
  - `[The snippets of these results were not stored (search.store_snippets is off). …]`.

  It applies to both sources. Empty results are stored unchanged, so CO17 reads them as before.
- **On:** the trace is stored as the model saw it, as today.

**What the gate loses with it off:**
- **The gate as it runs: nothing.** It checks the reply before the save, against the in-memory trace,
  which holds the full results (tested through `POST /api/chat`). It also never reads a result's text:
  - its structural rules read outcomes and ids;
  - the classifier's trace block lists each tool, its outcome and its error (`gate._render_trace`).
- **Anything that later re-reads a stored trace** sees titles and URLs only:
  - a re-check or diagnosis of a past reply;
  - a frozen case built from a run;
  - a future stage 2 rule that compares a reply's "the page says X" with what the search returned.

  None of these can see what the snippets said, so such a claim cannot be checked against the record
  afterwards.
- **The admin trace panel** shows no snippets for a reopened conversation. The live response still
  does.

`web_fetch` is unchanged: it fetches pages directly, not Brave data.

## 6. What Brave sees (for the decision record)

**Only fallback searches are sent**, and only while `fallback = "brave"`. While DuckDuckGo refuses
SearXNG, that is every search SearXNG cannot answer: it fails, times out, or finds nothing.

**What Brave receives:**
- the query exactly as the entity wrote it, which can carry a name, a place or a health topic, from
  any conversation, Jodie's included, and later from research topics;
- the account's key;
- this machine's IP address.

**What it does not receive:** the conversation, who is speaking, or which search was a fallback.

**Retention.** Brave's API privacy notice (https://api-dashboard.search.brave.com/privacy-policy, read
2026-10-19) says query records are kept "for a maximum of 90 days", with "IP address, authentication
token". Zero retention is an Enterprise option only.

SearXNG, the primary, sends every query to Google CSE, Brave and DuckDuckGo from the same IP with no
account.

## 7. Checked

- **Real config, on a scratch store** (`scratch_env`, 2026-10-20). `[auth]`, `[moltbook]` and
  `[search]` are present, and the app started in-process with `lifespan` both with `fallback = "none"`
  and with `"brave"` set in memory. `/api/health` returned 200 both times, and the store migrated to
  version 10. `data/` was unchanged (11 files, size and mtime).
- **The real `local.toml` has no `fallback` line.** So Brave stays off until Lyle adds
  `fallback = "brave"`.
- **The kit** loads the same config through the same `lifespan` and was not launched. One is running
  now, on main's code.
- **Not done: the B3 trial of the new texts on the model.** A play-kit server was running, and model
  calls beside it would break the one-model-process rule. See the report.

## Decisions for review

1. **`provider` renamed `fallback`** (`"none"`/`"brave"`). *Recommended:* yes. Lyle adds
   `fallback = "brave"` to `local.toml` to switch it on.
2. **Storage rights** stay open, with `store_snippets = false` until Lyle has read his terms.
3. **The play kit counts in its own store.** With `fallback = "brave"` in the shared `local.toml`, kit
  runs spend the same credit unseen by the real store's count, and the 20-call margin is small for a
  run. *Recommended:* run the kit with the fallback off, or accept the margin and watch Brave's
  dashboard.
4. **Every zero-result search goes to Brave while DuckDuckGo refuses SearXNG.** *Recommended:* accept
   it. That is the rule as decided, and the stop bounds the cost.
