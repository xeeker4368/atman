# 2026-09-21 — A2: the `image_generate` tool, and a derived constant that was wrong

**Tier 1.** The tool, `enabled` gating, storage, and Q13's return shape. Nothing
committed. **Stops here for review.**

## Files

Created: `program/tools/image_generate.py`, `program/artifacts/generated.py`,
`tests/test_image_generate.py` (26). Modified: `program/tools/catalog.py`,
`program/tools/registry.py` (`Tool.enabled`), `program/config.py`,
`config/defaults.toml`, `tests/test_tools.py`, `tests/test_attribution.py`,
`BUILT.md`.

**1,095 tests pass** (was 1,067), `ruff` clean.

## One scope decision, stated because it moves a boundary

**A2 takes the file write and the artifacts row; A3 keeps chunk/embedding indexing.**

The plan put "storage + metadata indexing" in A3, but Q13 requires the tool to return
a **path and an id**, and a tool that generates and discards has neither. Every
intermediate split leaves a half-state: a discarding tool is the placeholder
`catalog.py` refuses (*"would read as built while being nothing"*), and a file written
with no row is the orphan `backup.py` warns about from the other side. Storing bytes
and row together leaves neither, and it is `ingest.py`'s own internal seam — it stores,
then calls `_index_text()`, and those were always separable.

So after A2 an image is on disk and in `artifacts`, findable by SQL. After A3 it is
findable by retrieval. A test asserts `get_artifact_chunks()` is still empty, so when
A3 lands it fails and points at the change.

## The tool

One parameter, `prompt`. **No `negative_prompt`** (Q15): at cfg 1.0 the sampler skips
the unconditional branch, so one would be accepted and silently ignored — worse than
absent, because the model could not notice its instruction had no effect. A test
asserts both its absence *and* that cfg is still 1.0, so the trigger is checkable
rather than remembered.

**The result says nothing has seen the image**, and that is not a disclaimer:

> Nothing in this system has seen this image — there is no vision capability here.
> Describing what it looks like would be fabrication; the file itself is the record.

The entity has no vision. A result reading "here is your image" invites the model to
describe something it has not seen, which is exactly what the integrity gate exists to
catch. Cheaper to make the tool's own output truthful than to catch the consequence
downstream.

**`enabled` is decision #12's first axis and behaves like it.** A disabled capability
is **not offered to the model** — the schema never enters the prompt — rather than
offered and refused, which would burn a turn on the discovery. `Tool.enabled` is a
**call-time predicate**, so `default_registry()` filters on the live config value;
`catalog.TOOLS` still lists the tool unconditionally, so the full set stays greppable.
No `approval_required` (Q4), and a test asserts the key does not exist.

*Known limit, recorded beside the flag: `default_registry()` caches, so flipping
`enabled` needs a restart or `reset_default_registry()`. That collides with decision
#8's "no setting requires a restart" and will need attention when the admin panel
makes capability flags live-editable. It is config-file-only today, so nothing can
flip it at runtime yet.*

**Attribution's first real consumer.** The handler declares `takes_attribution`, so
P0's plumbing carries the person present into `artifacts.user_id`. A test asserts the
model cannot supply it and that it never appears in the recorded arguments.

## Storage

`workspace/` via `kinds.root_for()` (Q1) — the module names no directory. Sharded path
from the generated id, never from the prompt: a test stores a prompt of
`"../../etc/passwd"` and asserts the path is unaffected. `extraction_status =
metadata_only` (Q14), on task 2.6's scanned-PDF precedent — `extracted` would claim
the opposite of the truth for a PNG. The **prompt goes in `extracted_text`**, because
it is the image's indexable content and the column A3 will read; the generation
settings go in `extraction_note` as JSON, so a later reader can tell a 4-step
Lightning image from a 20-step one without a migration per sampler parameter.

Bytes first, then the row — `ingest.py`'s order, and the asymmetry is the point: a
file with no row wastes space and is detectable by walking the directory, while a row
with no file is a record pointing at nothing.

## The finding: a derived constant that was wrong, caught by live use

`comfyui.timeout_seconds` was **90**, derived at A1c from an *estimated* worst case of
70–75 s ("cold **or** contended"). The real worst case is cold **and** contended.

Measured, all 2026-09-21:

| condition | seconds |
|---|---|
| warm, nothing else resident | 12.8 |
| cold, alone | 30.5 |
| warm, `gemma4:26b` resident | 51–58 |
| warm, `gemma4:26b` **and** `nomic-embed-text` resident | 60.8 |
| inside a real turn (both resident, retrieval ran) | **83.8** |
| **cold, inside a real turn / cold with both resident** | **87.0** |

So one live turn **passed at 83.8 s** and another, cold, **failed past 90 s** with
`ComfyUITimeout`. **The constant was sitting inside its own measurement's variance
band**, which converts a slow generation into a lost one non-deterministically —
the worst kind of wrong, because it works in testing.

**Re-derived: client 90 → 110, tool 100 → 115.** The chain holds —
client 110 < tool 115 < `agent.tool_budget_seconds` 120 — so an overrun still
surfaces as `ComfyUITimeout` ("the outcome is unknown") rather than the loop's opaque
one. **`agent.tool_budget_seconds` did not move, so `IN_FLIGHT_GRACE_FLOOR_MINUTES`
stays 35** and option (b) is still avoided.

**Verified by re-running the exact case that failed**: cold ComfyUI, live turn →
**88.2 s, `ok`**, image on disk, 21.8 s of margin. And pinned by a test that asserts
the client timeout clears the measured 87 s worst case **by 20%**, so lowering it back
fails with the reason attached — the test the first derivation needed and did not
have.

**Consequence, stated rather than left to be discovered: an image turn is an
image-only turn.** 88 s of a 120 s aggregate budget leaves ~5 s, so a second tool call
in the same turn will be `SKIPPED`. That was already true at 90; it is now explicit in
the config comment.

**The failure mode was graceful, which is worth recording separately.** When it did
time out, dispatch returned `TOOL_ERROR`, the loop fed it back, and the entity told
the person plainly: *"The image generation failed because the process timed out. I am
unable to provide the picture of the brass doorknob at this time."* No fabrication, no
crash, no invented image. The architecture behaved correctly while the constant was
wrong.

## Verified live, end to end

A real turn, real model, real weights: the model wrote its own richer prompt (*"A
polished copper kettle sitting on a dark, textured slate worktop…"*), the tool ran in
83.8 s, and a 1,880,297-byte PNG landed in `workspace/28/28ee33f0…` with
`metadata_only`, the prompt in `extracted_text`, the settings in `extraction_note`,
and **0 chunks** — A3's half, correctly absent.

## Known limitations

- **Four tests changed expectations rather than behaviour**, because a real tool
  landed: the catalogue's exact tuple (twice, one inside a subprocess), and the two
  attribution tests that asserted *no* tool takes attribution. Those two were
  **re-scoped to the three Phase 2 tools by name** rather than widened — the property
  worth protecting is "these three are unchanged", not "nothing takes attribution",
  which stopped being true the moment this landed. A third test now asserts the
  positive direction, so the pair cannot both pass by nothing declaring it.
- **No duplicate detection.** `ingest.py` refuses a re-upload by sha256; two identical
  prompts here produce two artifacts, because the seed differs and so do the bytes.
  Deliberate — an image is not a re-upload of anything — but worth naming.
- **Nothing cleans `workspace/`**, and images are ~2 MB each. *(CORRECTED at B0: I
  also claimed here that `workspace/` was not gitignored. It was — since Phase 0. See
  `changelog/2026-09-21-b0-workspace-protection.md`.)* `workspace/` is still
  not backed up and not isolated in tests — that is **B0**, and it now
  has real files in it on this machine.
- **The tool cannot be disabled at runtime** (registry cache, above).
- **Quality remains a casual-look judgment** only.

## Next

A3: chunk and embed the prompt so a generated image is findable by retrieval, and
decide how a retrieved image chunk renders — it has no text to show, so
`prompt.render_retrieved` will need to say what it is rather than print its content.
