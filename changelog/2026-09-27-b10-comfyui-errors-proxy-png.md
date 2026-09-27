# B10: ComfyUI client — honest error classes, no proxies, only PNGs

Date: 2026-09-27 · queue item B10 (C1, C4, C5) · Tier 2. Plan approved at review,
with one addition to C4 (the IP-literal host check). No schema change, and no
measurement moves.

All three were confirmed by probe before the plan was written; the probe results are in
the approved plan, not repeated here.

## C1 — an HTTP error is no longer always "rejected the workflow"

**What was wrong**

- `_request` sent every `HTTPError` to `_rejection`, and `_rejection` names every result
  *"ComfyUI rejected the workflow"*.
- That covered `/history`, `/system_stats` and `/view` too. A `/view` 404 means the job
  ran and its image could not be fetched. Calling that a rejection told the entity
  something false about what happened.

**What changed** (`program/media/comfyui.py`)

- A new `ComfyUIResponseError(ComfyUIError)` carries the endpoint path and the status,
  and quotes up to 200 characters of the body.
- `_request` gained an `on_http_error` handler. It defaults to that generic error.
- Only `POST /prompt` passes `_rejection`, and `_rejection` now maps only **HTTP 400**
  to a rejection (still `ComfyUIModelMissing` where the body supports it). A non-400
  from `/prompt` gets the generic error.
- `_fetch_image` turns a `/view` error into `ComfyUIGenerationFailed`: *"ComfyUI finished
  prompt X but its image could not be fetched (HTTP 404 from /view); nothing was
  stored."* That matches the out-of-time error's wording. `_fetch_image` now takes the
  `prompt_id` so it can say which job.

## C4 — proxy environment variables no longer redirect the client

**What was wrong**

- `urllib.request.urlopen` builds its opener from `http_proxy`/`HTTP_PROXY`.
- With one set, a request meant for 127.0.0.1:8188 went to the proxy, and the proxy's
  reply was accepted as ComfyUI's own.
- The loopback check was therefore checking an address the client never connected to.

**What changed**

- A module-level `_OPENER = build_opener(ProxyHandler({}))`. The empty `ProxyHandler`
  replaces the default one, which is the handler that reads the environment.
- `_request` sends every request through `_open()`, which uses that opener. Nothing in
  the module calls `urlopen`.
- `_open()` is also the seam the fake-transport tests patch. About 20 tests moved from
  patching `comfyui.urllib.request.urlopen` to `comfyui._open`, approved as part of C4.
  This changes test setup only, not behaviour.
- **Approved addition:** `config.comfyui_host()` raises `ConfigError` unless the host is
  an IP literal (`http://127.0.0.1:8188`, `http://[::1]:8188`), so `localhost` is
  refused.
  - It runs when the value is read, not at startup. Nothing reads `comfyui.host` at
    startup, and a startup check would either run while image generation is disabled or
    have to repeat the `comfyui.enabled` gating.
  - It closes the remaining gap: a hostname resolved once for the loopback check and
    again by urllib for the connection.
  - **It is a shape check that runs first, not a security check.** The docstring says
    so: `comfyui._check_loopback` is still the boundary, and it is what refuses a
    well-formed LAN literal such as `192.168.0.82`. A test pins that pair.
- No `config/local.toml` exists and no `ANAM_COMFYUI_HOST` is set on this machine, so
  nothing configured here changes.

## C5 — bytes that are not a PNG are not stored as one

**What was wrong**

- `_fetch_image` returned whatever `/view` sent, and took the type from the
  `Content-Type` header (`content_type or "image/png"`).
- A text file and an HTML file served where ComfyUI serves images were both accepted,
  and would have been written to disk as `.png` artifacts.

**What changed**

- `comfyui.PNG_SIGNATURE` (`\x89PNG\r\n\x1a\n`) and `IMAGE_CONTENT_TYPE = "image/png"`.
  The graph's output node is `PreviewImage`, which only writes PNG.
- **Layer 1, when the image is fetched.** `_fetch_image` requires the signature. On a
  mismatch it raises `ComfyUIGenerationFailed`, quoting both the claimed header and the
  body's first 16 bytes. The returned `content_type` is what the bytes are, never what
  the header said.
- **Layer 2, at the storage boundary.** `generated.store()` independently refuses bytes
  without the signature, before any file or row is written. It imports the same
  constant, so the two checks cannot drift apart.
- **The dependency is pinned by one test**
  (`test_the_png_assumption_is_one_decision_in_three_places`). It checks that the
  output node is `PreviewImage`, the signature and content type, that `generated` uses
  the client's constant, and the `.png` filename. If the output format changes, this
  test fails until all of them change together.

## Tests

20 new: 5 for C1, 8 for C4, 3 in `test_comfyui.py` and 4 in `test_image_generate.py`
for C5. The live test is extended to assert the signature and content type.

- **C1:**
  - a `/view` 404 is `ComfyUIGenerationFailed`, with no "reject" in the message and
    with the status, endpoint and prompt id in it;
  - a 400 from `/prompt` is still a rejection;
  - a 500 from `/prompt` is `ComfyUIResponseError` and not a rejection;
  - a `/history` 503 names its endpoint and status;
  - the quoted body is capped at 200 characters.
- **C4:**
  - four non-literal hosts are a `ConfigError`;
  - both literal forms (IPv4 and IPv6) are accepted;
  - a LAN literal passes config and is still refused by `_check_loopback` before any
    request is made;
  - no `urlopen(` appears in the module source;
  - **the proxy test runs in a fresh subprocess.** `urlopen` caches its global opener
    from the environment on first use, so an in-process test gives false negatives once
    anything has called it (the pitfall found while probing). A fake proxy answers
    everything as ComfyUI with version `PROXY!!`, and the client points at a closed
    loopback port.
    - A control in the same process shows the default opener really does reach the
      proxy (`PROXY!!`), so the result means something.
    - The client must report `ComfyUIUnreachable`.
- **C5:**
  - a text body and an HTML body, both sent as `image/png`, are refused, quoting the
    header and the first bytes;
  - a real PNG sent as `application/octet-stream` comes back as `image/png`;
  - `store()` refuses both non-PNG bodies with no file and no row written;
  - the dependency pin above.

**Proven to bite:** each fix was reverted and its tests re-run.

| Reverted | Result |
|---|---|
| C1: every HTTP error back to `_rejection` | 4 fail |
| C4: `_open` back to `urlopen` | 2 fail; the proxy test reads `{'control': 'PROXY!!', 'client': 'PROXY!!'}`, the original bug |
| C4: the IP-literal check | 4 fail |
| C5: the fetch-time check | 3 fail |
| C5: the storage check | 2 fail |

**Full suite:** 1,264 passed, 2 skipped (was 1,244). `ruff check .` is clean.

**Live:** ComfyUI was started without `--listen` (`lsof` showed `127.0.0.1:8188` only).
Both live tests passed: a real generation passes the signature check and reports
`image/png`. ComfyUI was stopped afterwards and the port released.

## Known limitations

- `test_nothing_in_the_client_calls_urlopen` checks spelling. The subprocess test is the
  behavioural proof.
- The signature check proves the bytes *start* like a PNG, not that they are a
  well-formed image. Decoding would need an imaging dependency, and nothing here reads
  the image.
- An IPv4-mapped IPv6 literal (`http://[::ffff:127.0.0.1]:8188`) passes the shape check
  and is unwrapped by `_check_loopback`, as before.
