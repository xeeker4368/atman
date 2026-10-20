# 2026-10-20: the Brave Search API as web_search's fallback (decisions #38, #39)

**Tier 3** (credential handling; a paid external call). Design: `docs/BRAVE_SEARCH_DESIGN.md`
revision 2. Branch `cc/pre-run-5`.

**What changed**
- `web_search` asks SearXNG first. With `[search] fallback = "brave"` it asks Brave when SearXNG errors,
  times out, or finds nothing while reporting unresponsive engines. Each result says which source
  answered and why the fallback ran (`[Source: …]`).
- `program/tools/brave.py`: the client, the count and the caps. Each call is reserved in `brave_calls`
  (migration 10) by one atomic statement that is also the 980-a-month stop. There are 3 calls a turn
  (a `ContextVar`; the registry now copies the caller's context into the tool's thread), a WARNING from
  900, and Brave's monthly header is used only when its limit is above 0.
- The key is read only from `local.toml` (`config.brave_api_key`), is dropped from the merged config,
  and is checked at startup when the fallback is on.
- `GET /api/me` returns `notices` to the admin role only, shown as a banner. `scripts/brave_usage.py`
  is read-only.
- `search.store_snippets` (off): stored traces keep titles and URLs, not snippets
  (`loop.storable_trace`).
- `web_search`'s timeout goes from 15 s to 38 s (derived). The in-flight grace floor is unchanged at 35
  minutes.

**Tested:** `tests/test_brave.py` (48 tests, fake key, fake clock, no network; `conftest` refuses
`api.search.brave.com`). 11 mutations were run with `PYTHONDONTWRITEBYTECODE=1`, and each was caught.

**Limitations:**
- The new texts were not tried on the model (B3), because a play-kit server was running.
- The kit counts Brave calls in its own store.
- `requests` timeouts bound each read, not the total.

**Follow-up:**
- Lyle's storage-rights check.
- Switching the fallback on.
- The B3 trial.
