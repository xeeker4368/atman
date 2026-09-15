# 2026-09-15 — Governance-file ingestion blocklist

**Tier 1 · Sonnet**, read from `BUILD_PLAN.md` line 191. Design reported and
approved before building. Nothing committed.

## Files changed

Created: `program/artifacts/blocklist.py`, `tests/test_blocklist.py` (34).
Modified: `program/artifacts/ingest.py`, `program/api/routes/upload.py`,
`BUILT.md`.

No schema change, no new dependency. 681 tests pass (was 647); `ruff` clean.

## Content identity, not a path — and why the premise had to move

`ingest.ingest()` takes **bytes**, not a path. Its only caller reads them off an
`UploadFile`, and the sole path-like thing in the request is the client-supplied
filename, which `ingest.py` already refuses to treat as a path. **There is
nothing to resolve at the moment of an upload**, so a pure resolved-path check
cannot block one, and matching the filename would be exactly the string
comparison BUILD_PLAN's note forbids — `soul.md` renamed to `notes.txt` would
sail through.

So the directory rule stays the source of truth and the check derives from it:
resolve each blocked directory, walk it, hash every file, compare an upload's
bytes against that set. Three consequences, all of them the point:

* A governance file added later — Phase 3's `program/integrity/architecture.md`
  is BUILD_PLAN's own example — is covered by the walk with **no code change**.
  That is the failure the generalisation note was written about.
* A blocked file renamed to anything is still refused, because identity is
  content. Tested.
* `is_blocked_path()` remains the rule itself, exercised by the walk and ready
  for Phase 4's path-taking callers — not an unmounted gate.

Measured: 52 files, 50 distinct hashes, **~10 ms** to hash all of them, against
~75 ms to embed a single chunk. Walked fresh per upload rather than cached — a
cache would go stale exactly when it matters, since these files are edited
constantly and `NOW.md` is rewritten every session.

## Scope

| covered | contents |
|---|---|
| `program/integrity/` | `soul.md`, and whatever lands there |
| `docs/` | 7 designs of record |
| `changelog/` | 24 dated records |
| `config/` | `defaults.toml`, and `local.toml` when it exists |
| project root, **non-recursive**, `*.md` | 8 canonical docs |

The root entry is a **rule, not an enumeration** — a doc added next month is
covered without an edit. Non-recursive deliberately: `workspace/` sits under the
root and decision #10 requires creative writing to be *indexed into memory like
everything else*, and `data/artifacts/` holds files already uploaded. A recursive
root rule would block both and contradict a settled decision. Both are asserted
as explicitly **not** blocked.

**A correction to my own scope report.** I said `config/local.toml` exists on
this machine and holds the signing secret, citing `git check-ignore`. That was
wrong: `git check-ignore` echoes any path it *would* ignore, existing or not, and
I read its output as evidence of existence. The file does not exist here; the
secret comes from `ANAM_AUTH_SESSION_SECRET`.

The `config/` inclusion still stands, and the mistake is a decent argument for
it: a directory rule covers `local.toml` from the moment anyone creates it, with
no code change and nobody having to remember. A test asserts exactly that —
the path is blocked *while the file is absent* — and a second test proves
content-refusal against `local.example.toml`, which does exist.

## The response

**HTTP 400**, one fixed body:

> `This file is part of the system's own governance or configuration and cannot
> be ingested as memory.`

* **Not 403** — 403 reads as "you may not", inviting "perhaps someone else may".
  Nobody may; it is not a permission question, so it does not get a permission
  status.
* **Not `metadata_only`** — that means *stored but not read*, and this file was
  recognised and refused with nothing stored. Recording a refusal as a
  successful-but-unread upload would make the `artifacts` table describe
  something that did not happen. `GovernanceFileError` is deliberately **not** an
  `IngestionError` subclass, and a test asserts that so the route cannot collapse
  the two by accident.
* **The response names no path, no filename, no matched rule.** Jodie can upload,
  and a response naming the file would confirm internal structure to a caller who
  should not learn it. A test asserts the body contains none of `soul`,
  `integrity`, `program/`, `docs/`, `config/`, `changelog`, or the project root.
* **The detail is logged at WARNING**, where the operator sees it and an
  unprivileged uploader does not — the same split as `AUTH_DESIGN`'s single 401.

The check runs **before any write**: before the file reaches disk, before the
artifacts row, before the duplicate check. A governance file never exists in the
store even transiently.

## The honest limitation, asserted rather than assumed

Content hashing catches the real file and an exact copy. **Change one byte and it
does not match.** Catching that needs similarity above some threshold, and an
uncalibrated threshold is what this project refuses to ship — the retrieval
floors are unset for the same reason (`RETRIEVAL_DESIGN` D4).

`test_a_near_copy_is_NOT_caught_which_is_the_known_limitation` uploads `soul.md`
plus a single newline and asserts it **succeeds**. That is a test asserting a
gap, deliberately: it makes the limitation a checked property rather than an
unexamined hole, and if near-copy detection is ever added, it fails and points at
its own docstring — the right outcome, not a broken test.

## web_fetch: out of scope, and verified rather than assumed

Nothing serves these files. No `StaticFiles`, no `FileResponse`, no `mount()`
anywhere in `program/`, and the app has exactly four routes — `/api/health`,
`/api/login`, `/api/chat`, `/api/upload` — none of which serves a file.
Independently, `web_fetch`'s SSRF guard refuses `127.0.0.1` before any request,
so it could not reach the app even if the app served them. Closed twice over.

The residual risk is not `web_fetch`'s: if a later admin panel mounts a static
directory, the exposure would be that mount being LAN-reachable, which belongs to
that task and to the loopback gate `ROLE_GATING_DESIGN.md` R2 already specifies.

## Proven to bite

Each guard broken, suite re-run:

| break | result |
|---|---|
| remove `.resolve()` from the path check | 3 symlink/traversal tests fail |
| remove `blocklist.check()` from `ingest()` | 7 tests fail |
| restore both | 681 pass |

## Verified live

Real server, **as Jodie** (the unprivileged user), uploading the real `soul.md`
renamed to `holiday-notes.txt`:

```
HTTP 400
{"detail":"This file is part of the system's own governance or configuration
           and cannot be ingested as memory."}
```

and in the server log, where only the operator sees it:

```
WARNING: refused an upload matching the governance file
         program/integrity/soul.md — it is part of the system's own
         configuration and is not ingestible as memory.
```

An ordinary file uploaded in the same session returned 200 and indexed 1 chunk,
so the block is narrow rather than a general refusal.

## One unrelated fix carried here

`upload.py` still used the deprecated `HTTP_413_REQUEST_ENTITY_TOO_LARGE`. I had
reported that as fixed during task 2.6 and it was not — the reappearing
`StarletteDeprecationWarning` is what showed it. Now `HTTP_413_CONTENT_TOO_LARGE`,
and the warning is gone.
