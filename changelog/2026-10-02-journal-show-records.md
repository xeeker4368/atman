# 2026-10-02 — Journal: `--show-records`, Phase 6 never auto-indexes, specifics check costed

Review rulings of 2026-10-02. Tier 1 (a read-only command) plus records. **The prompt is not tuned.**

## Built
- **`python -m scripts.write_journal --show-records DATE [--with-clause]`**: read-only; calls
  `journal.prepare` (no model call, nothing written, not even the entity's row) and prints the
  records section of the prompt **byte for byte as the entity was given it**, under `# ` heading
  lines giving the counts, `messages_omitted` and `messages_clipped`. Refuses today and future days
  (exit 2); says so for an empty day. The closing text after a write now points at it, and says why:
  the gate checks no counts, names or event claims.
- **Tests (7 new, 52 in `tests/test_journal.py`)**: byte-identical to `prepare(...)`'s records
  section; heading lines only before it; no model call, no row, no entity row; the same records
  under either arm; omissions and clips stated and the omitted message absent; refusal and empty-day
  paths; the closing text. **Seven mutations, each killed:** records trimmed, whole prompt printed,
  a model call added, an entry written, counts blanked, the future-day refusal turned into success,
  and the pointer dropped from the closing text.

## Recorded in J7
- **Phase 6 must never auto-index.** The unsupported-claim rate was 7/24, 6/24 and 4/24 (lower
  bounds) and the identity gate does not check those errors, so the stage-1 reading control stays.
- **A flag-only specifics check (numbers, names, quoted phrases absent from the records) is
  costed, not built.** About 50 lines plus tests, no model call. A throwaway prototype on the 72
  real entries: **it flags none of the 17 known unsupported-claim entries' errors** (they are token
  relations such as who made a correction, or an invented common word such as "brands") and 24 of 72
  entries flagged something, mostly headings read as names. It would catch only an invented count or
  quotation, which the given counts and the entries' behaviour already make rare. Not built. A
  speaker/event attribution check is the larger piece that would address the observed errors; it is
  named, not proposed.
- The stale line in the command's docstring that the clause was "being decided" now says the default
  ships without it.
