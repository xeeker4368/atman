# B13: web_fetch layer 3 reads the peer of a close-delimited response

Date: 2026-09-24 · queue item B13 (found during Group A item 13) · Tier 3 (a change to
an SSRF security check). Plan submitted with the Group A report and approved.

## What was wrong

- Layer 3 checks the address the socket actually connected to, before any body is read.
  This is what defeats DNS rebinding.
- `peer_address()` found that socket through `response.raw._connection.sock` only.
- `http.client` detaches the socket from the connection when the body is delimited by
  the connection closing (`Connection: close`) or has no length at all. Measured:
  `_connection.sock` is `None` in both cases.
- So `peer_address()` returned `None`, and `_check_peer` refused every such page with
  "the address actually connected to could not be determined".
- That failed closed, so it was safe, but it rejected legitimate servers that end a
  response by closing the connection.

## What changed

`program/tools/web_fetch.py` `peer_address()`:

- It now gets the socket from `_live_socket(response)`, the lookup Group A added for the
  body-read watchdog:
  - the connection's socket if present;
  - otherwise the socket the response's file object still holds.
- On that file-object socket `getpeername()` returns the real peer, measured on both
  shapes.
- If neither socket can be found, it still returns `None`, and `_check_peer` still
  refuses.
- The docstring records what changed and why.

## Tested

Four new tests in `tests/test_web_fetch.py`, against real local servers answering once
with `Connection: close` and with no length at all:

- **The peer is read** on both shapes. Each first asserts `_connection.sock is None`, so
  the old path really is empty.
- **Layer 3 still refuses loopback on that path**, and with its address reason ("landed
  on …"), not "could not be determined". So the check genuinely runs on the recovered
  address; the fix isn't just "stopped refusing".

Other checks:

- **Proof it bites:** restoring the connection-only lookup fails all 4.
- **Unchanged and passing:** `test_an_unreadable_peer_address_fails_closed` (no socket
  anywhere), the rebinding tests, and the rest of the SSRF suite. 67 in the file.
- **Live:** `test_a_live_fetch_of_a_real_page` passes against a real public page.
- **Full suite:** 1216 passed, 2 skipped (the two ComfyUI live tests; ComfyUI isn't
  running). `ruff check .` clean.

## Known limitations

- **Not observed against a public server that closes to end its response.** The local
  servers reproduce the exact `http.client` shape that caused the refusal, but no
  real-world close-delimited page was fetched.
- **Both lookups are private API** (`_connection`, `_fp.fp.raw._sock`). A future
  `urllib3`/`http.client` change that moves them makes `peer_address()` return `None`,
  which fails closed. That is the behaviour this module was built to have.
