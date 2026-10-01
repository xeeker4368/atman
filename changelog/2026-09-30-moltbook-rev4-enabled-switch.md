# 2026-09-30 — Moltbook revision 4: its own `enabled` switch, 401/403, keyless limits, live run

Review follow-ups to the Moltbook read tools (`docs/MOLTBOOK_READ_DESIGN.md`
revision 4). Tier 1.

## What changed

- **`moltbook.enabled` replaces "a key is configured"** as the read tools'
  predicate (`config.moltbook_enabled()`). It is bootstrap, with
  `ANAM_MOLTBOOK_ENABLED` as the env override, and **defaults to `false`**.
  `moltbook_configured()` is removed. Reading and Phase 8 posting stay on
  separate axes.
  - **Action for Lyle:** add `enabled = true` under `[moltbook]` in
    `config/local.toml` to have the tools offered. I did not edit that file,
    because it holds the key.
- **A 401 or 403 now gets its own message:** Moltbook refused a read that
  needs no key, possibly because it now requires authentication. Never retried.
- **Conftest** turns Moltbook off through the switch, and still blanks the key.
- `config/defaults.toml` and `config/local.example.toml` document the switch.
- Design doc revision 4, `BUILT.md`.

## Measured

- **Keyless rate-limit headers**, one GET per endpoint, no key sent: `/posts`
  200, single post and comments 500, search and profile 60, each in a ~60 s
  window. These are identical to the keyed buckets and none is under 60/min, so
  M4's "no client-side limiter" stands.
- **The two opt-in live tests pass** (`ANAM_MOLTBOOK_LIVE=1`, no key sent). One
  real `browse` render was read by eye: allowlisted fields only.

## Tests

- The switch and the key are varied independently in 4 cases.
- The default is pinned `false` in both `defaults.toml` and the fallback.
- 401 and 403 each get the specific message.
- **Proven to bite:**
  - reverting to key presence fails 3 tests;
  - removing the 401/403 branch fails 2;
  - defaulting to on fails 1.
- **Full suite:** 1,411 passed, 4 skipped. `ruff` is clean.

## Known limitations

- The Phase 5 gate call (a real turn through the model) is still not made.
- Anonymous buckets are probably per IP, shared with this connection;
  undocumented.
