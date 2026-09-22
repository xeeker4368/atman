# 2026-09-21 — B0: protecting `workspace/`, and a correction to my own finding

**Tier 1, blocking A3.** Nothing committed.

## Files

Modified: `program/artifacts/kinds.py`, `program/ops/backup.py`,
`tests/conftest.py`, `tests/test_backup.py` (+3), `tests/test_attribution.py`,
`tests/test_image_generate.py`, `config/defaults.toml`, `BUILT.md`.

**1,096 tests pass** (was 1,095), `ruff` clean. `workspace/` holds exactly its five
tracked `.gitkeep` markers and nothing else.

## First: one of my three findings was wrong

**`workspace/` was already gitignored.** `.gitignore` has carried
`workspace/*/*` plus `!workspace/*/.gitkeep` since Phase 0 — deliberately, so the
directory skeleton stays tracked while its contents do not. I tested
`git check-ignore workspace`, which reports the *directory* as not ignored because it
must be for the markers to be trackable, and read that as the contents being exposed.
Checking a real path (`workspace/04/0415ee…`) shows
`.gitignore:38:workspace/*/*` matching.

I stated it as fact three times — in the published plan, in `BUILT.md`, and in two
changelogs — and each is corrected. **The mistake is instructive in the same way the
0.2-second generation was:** I tested the thing that was easy to test rather than the
thing I was claiming, and the wrong answer looked like the expected one.

*Residual, genuinely uncovered: a file written directly at `workspace/<file>` (depth
one) does not match `workspace/*/*`. Every writer here shards, so nothing produces
that path today.*

## What was actually missing

### 1. Generated images bypassed the intended structure

Phase 0 created `workspace/{generated,uploads,writing,research,journals}/` and tracks
each with a `.gitkeep`; that changelog's own bugfix note was verified against
*"a file dropped into `workspace/generated/`"*. **A2 ignored all of it** — `kinds.py`
sharded flat, so 66 two-hex directories appeared beside the five intended ones.

`ArtifactKind` now carries a `subdirectory`: `generated_image` → `workspace/generated`,
`creative_writing` → `workspace/writing`, `upload` → none, because `artifact_dir()` is
already dedicated. `root_for()` composes it, and the stored `storage_path` stays
relative to the kind's root so no row encodes which subdirectory the root happened to
be.

*Caught by looking at what was already tracked under `workspace/`, which I had not
done when I designed Q1's mapping. Cheap now — only test output existed. Expensive
after B4 and a few hundred real files.*

### 2. `backup.py` did not cover it

`_copy_workspace()` added, mirroring `_copy_artifacts()` and recorded in the manifest
alongside it, with both directory paths now in `source` — they resolve from their own
config keys, so a manifest naming only the databases would not tell a restore where to
put them back.

The note says what it is: **the least rebuildable artifact in the set.** An uploaded
file still exists on whoever's machine it came from, and vectors regenerate from
`chunks`. **Nothing the entity wrote exists anywhere else.** Still `best-effort`, not
`transactional`, and honest about why: a directory copy outside the databases' read
lock, so something written mid-backup may be absent — a missing file beside its row
rather than a corrupt one, and the row carries a sha256.

**Proven to bite:** removing the two lines that append it fails 2 tests.

### 3. The isolation guard did not cover it — and it had already leaked

`tests/conftest.py` captured the data, Chroma, backup and artifact directories.
**`workspace/` was the fifth, and the gap was not theoretical: 65 real PNGs from
`tests/test_image_generate.py` were sitting in the repository's own `workspace/`.**
Same trap as the backup directory at task 1.14 and the artifact directory at 2.6, for
the third time — it resolves from its own config key, so repointing `ANAM_DATA_DIR`
leaves it alone.

**It needed a different check from the other four.** They ask *"was this directory
created during the run?"*, which can never fire for `workspace/` because it is part of
the tracked skeleton and always exists. The guard now **snapshots the file set at
import and reports anything new**, naming the offending paths and what to do:

```
1 file(s) were written into the real workspace (…/Atman/workspace):
…/workspace/generated/6f/6fef8a6f…. A test that writes creative work or a
generated image must take `isolated_data_dir`, which repoints ANAM_WORKSPACE_DIR.
```

**Proven by writing a deliberate leak**, not by reading: a throwaway test that
un-sets `ANAM_WORKSPACE_DIR` and stores an image raises
`StoreIsolationViolation` with that message. `isolated_data_dir` now repoints
`ANAM_WORKSPACE_DIR`, and the 65 strays are removed.

## The documented consequence you asked for

`config/defaults.toml`, directly under `agent.tool_budget_seconds`, now states that
**a turn that generates an image is structurally a single-tool turn** — 83.8–88.2 s
measured against a 120 s aggregate budget is 70–73%, so whatever follows gets the
remainder and a second call needing more than a few seconds is returned `SKIPPED`. It
says this is hardware rather than choice, points at the measurements, and records that
raising the budget was reviewed and declined because it moves
`IN_FLIGHT_GRACE_FLOOR_MINUTES`. Nobody has to re-derive it from three timeout values
in two files.

## Known limitations

- **Four tests changed expectations** because the subdirectory landed. Each now
  asserts the *stronger* property — that the subdirectory is used — rather than being
  relaxed, and one adds a loop asserting every workspace-rooted kind declares one, so
  a future kind cannot shard flat by omission.
- **Nothing prunes `workspace/`.** Backup copies it whole, and generated images are
  ~2 MB each, so both grow without bound. That is go-live tooling's problem and is
  not built.
- **The backup has still never been restored** — unchanged, and now with one more
  directory in it.
- **A depth-one file in `workspace/` is not gitignored** (above). No writer produces
  one.

## Next

A3 (chunk/embedding indexing for generated images) and B4 (the creative-writing tool),
as originally sequenced. B4 inherits the `writing/` subdirectory and the isolation
guard, so it starts on protected ground.
