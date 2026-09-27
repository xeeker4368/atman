# Group A batch: five independent Tier 0/1 fixes

Date: 2026-09-24 · merged-queue items 13 (web_fetch half), 15, 18, 19, 22a · approved as
one batch with no per-item stop

None of the five share a function. The only shared files are `BUILT.md` and this
changelog. `tests/test_ingestion.py` also carries the chunking fix's tests (item 2); the
new test here is a separate one.

## 15 — Upload size limit applied before the body is in memory

`program/api/routes/upload.py`: new `_read_capped()` in `upload()`.

- **Before:** `await file.read()` pulled the whole body into memory, and only `ingest()`
  checked the size.
- **Now:** the route reads in 1 MB pieces and returns 413 once the total passes
  `ingestion.max_upload_bytes`. It holds at most one piece beyond the limit.
- `ingest()` keeps its own check for callers that don't come through this route.
- **Tests (3), in `tests/test_upload_route.py`:**
  - an oversize body stops reading within one piece of the limit;
  - a body exactly at the limit is read whole;
  - an oversize upload never reaches `ingest()`.
- **Limitation:** Starlette spools the multipart body to a temp file before the handler
  runs. Memory is bounded now; disk still takes the full upload.

## 13 (web_fetch half) — The fetch budget covers the body read

`program/tools/web_fetch.py`: `_read_capped()` gained a `remaining` argument and a watchdog.
There is a new `_live_socket()`, and `fetch()` passes the time left.

- **Before:** the 20 s total budget was checked only at each redirect hop. The read
  timeout bounds each socket read, so a slow-drip server held the read, and the orphaned
  handler thread, indefinitely.
- **Measured before fixing:** a check between chunks would not have worked.
  `iter_content(16384)` returned no chunk in 25 s against a 20-byte/s local server,
  because it blocks until 16 KB have arrived.
- **Fix:** a `threading.Timer` shuts the socket down at the deadline. That wakes the
  blocked read (measured: 2.01 s against a 2 s timer).
- **The timer's flag decides whether the read timed out, not the exception.** Without a
  `Content-Length`, a shutdown looks like a clean end of body, and would otherwise
  return a partial page as though it were complete. That case raises
  `WebFetchError` instead.
- **Found during testing:** for close-delimited bodies the socket is not on
  `raw._connection.sock`. The watchdog had nothing to cut, and the first version hung
  (confirmed by a faulthandler stack dump). `_live_socket()` falls back to the socket
  held by the response's file object.
- If neither path finds a socket, the read is refused (fails closed), the same stance
  layer 3 takes.
- **Tests (4), in `tests/test_web_fetch.py`, against real local servers:**
  - a slow body with a length is cut at the budget;
  - a slow body without a length is cut at the budget;
  - a fast body is returned whole and its timer is joined, not left armed;
  - with no socket found, the read is refused.
- **Proof it bites:** the pre-fix behaviour was measured directly (no chunk in 25 s),
  not by reverting. A reverted test hangs instead of failing, which a suite run can't
  report cleanly.
- **Not covered:** DNS resolution (`resolve_target`) still sits outside the budget.
- **New finding, not fixed. Layer 3 refuses every close-delimited response:**
  - `peer_address()` reads `raw._connection.sock`, which is `None` for those bodies
    (measured).
  - So `_check_peer` refuses them as "could not be determined".
  - It fails closed, so it is safe, but it rejects legitimate servers.
  - The fix would reuse `_live_socket()`, but it changes layer-3 security semantics, so
    it goes up for review separately. It is recorded in BUILT.md.

## 18 — ComfyUI image fetch gets the remaining time

`program/media/comfyui.py`: `generate()` computes the time remaining and passes it to
`_fetch_image()`, whose parameter changed from `deadline` to `remaining`.

- **Before:** a generation finishing at 108 s could be followed by a 60 s fetch, about
  168 s against the client's 110 s bound.
- **Now:** with no time left, `generate()` raises `ComfyUITimeout`, saying the image was
  produced but not retrieved.
- **Tests (2), in `tests/test_comfyui.py`:**
  - finishing at 100 s of 110 s gives the fetch 10 s;
  - finishing at 110 s raises and never calls `/view`.
- **Proof it bites:** both fail against the previous `comfyui.py`.
- **Limitation:** urllib's timeout applies per socket operation, not in total. That is
  the same class of problem as item 13, accepted here because the peer is loopback.
- **Shared-function note:** C2 touches `generate()` and C5 touches `_fetch_image()`.
  Both are in Group B, so neither was done in this pass.

## 19 — Backup manifest counts come from the captured copies

`program/ops/backup.py`: `_row_counts(destination)` reads the backup files read-only, and
`create_backup()` calls it after `_snapshot_databases()`.

- **Before:** the counts were read from the live source before the snapshot lock was
  held, so they could describe a different moment than the one captured.
- **Now:** they describe the captured copy.
- **No lock duration or locking behaviour changed.** Counting inside the lock was the
  other option. It was not taken, because it would have been a `db.py` checkpoint
  question.
- **Test (1), in `tests/test_backup.py`:** a message is written between the old counting
  point and the snapshot, and the manifest counts must match the copy.
- **Proof it bites:** it fails against the previous `backup.py`.

## 22a — `ingest(artifact_type=...)` indexes under that type

`program/artifacts/ingest.py`: `_index_text()` takes `artifact_type` and the call site
passes it.

- **Before:** the storage path and the row used the argument, while indexing hardcoded
  `upload`.
- No production caller passes another type today.
- **Test (1), in `tests/test_ingestion.py`:** chunks carry the provenance of the type the
  row records.
- **Proof it bites:** it fails against the previous `ingest.py`.

## Totals

Full suite **1186 passed, 2 skipped** (was 1175/2 after item 2). The two skips are the
live ComfyUI tests, and ComfyUI is not running. `ruff check .` clean.
