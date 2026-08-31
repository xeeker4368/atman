# Database schema

Two SQLite databases under `data/`. This document is the narrative; the schema
itself is in `anam/memory/schema/archive.sql` and `working.sql`, and those files
are authoritative if the two ever disagree.

---

## The split, and why

**`archive.db` — the durable record.** Append-only, two tables, shape frozen,
never migrated. It answers one question: what actually happened, and who said
it. Nothing derived lives here.

**`working.db` — the operational store.** Everything else: conversation state,
chunks, provenance, settings, artifacts, correction links. All of it is either a
mirror of the archive or derived from it, and all of it is rebuildable.

The split buys one thing: the irreplaceable data lives in a small, frozen,
append-only store that no feature has a reason to touch, while everything that
does need to change is free to change. Memory forms slowly and cannot be
regenerated; schemas churn. Keeping them apart means schema churn cannot reach
the memory.

### Writes are atomic across both

A message is written to both stores inside one transaction, over a connection
that has the archive `ATTACH`ed. Either both rows land or neither does.

The failure this prevents is silent and asymmetric: a message in the archive but
not in working is a memory retrieval cannot see; a message in working but not the
archive is a memory the durable record does not have. Neither raises anything at
the time.

### DELETE journaling, not WAL

SQLite guarantees atomicity across attached databases **only when no
participating database is in WAL mode**. In WAL, a multi-database commit is
atomic within each database separately — precisely the half-written state above.

WAL would be faster. It is deliberately not used. Both `main` and `archive` are
set to `DELETE`, and `tests/test_db.py::test_journal_mode_is_delete_on_both`
pins it. Do not change this without re-reading SQLite's documentation on atomic
commit across attached databases.

### Foreign keys do not cross the boundary

SQLite does not support foreign keys between attached databases. The two stores
share ids by convention, not by constraint. Within `working.db`, foreign keys
are enforced (`PRAGMA foreign_keys = ON`).

---

## archive.db

Frozen. If a later phase needs a new field, it belongs in `working.db`.

### `users`
| column | type | notes |
|---|---|---|
| `id` | TEXT PK | |
| `name` | TEXT NOT NULL | |
| `created_at` | TEXT NOT NULL | ISO-8601 UTC |

### `messages`
| column | type | notes |
|---|---|---|
| `id` | TEXT PK | |
| `conversation_id` | TEXT NOT NULL | no FK — conversations live in working |
| `user_id` | TEXT NOT NULL | attribution |
| `role` | TEXT NOT NULL | `user` \| `assistant`, CHECK-constrained |
| `content` | TEXT NOT NULL | |
| `tool_trace` | TEXT | JSON, or NULL when no tools were called |
| `timestamp` | TEXT NOT NULL | ISO-8601 UTC |

`tool_trace` is here rather than only in `working.db` because the trace is part
of what happened. It is the ground truth the fabrication gate checks claims
against, and a claim about a tool call is only checkable for as long as the
record of that call survives.

### Considered and deliberately excluded

- **A `conversations` table.** Start and end times are derivable from message
  timestamps grouped by `conversation_id`. Storing them here would duplicate a
  derived fact into the frozen store.
- **A `channel` column.** This build has one channel. iMessage is deferred
  entirely, and `user_id` already resolves to a person. *Flagged for review:
  this is the one field that is cheap now and expensive later, given the table
  is frozen.*

---

## working.db

### `schema_version`
Migration bookkeeping. Version 1 is the initial schema, recorded when the
database is created.

### `users`
`id`, `name` (UNIQUE), `role` (`admin` \| `user`, CHECK-constrained),
`password_hash`, `created_at`, `last_seen_at`.

Web credentials live here rather than in a general channel-identifier table.
One channel, so the generic version would be speculative structure.

### `conversations`
`id`, `user_id` → users, `started_at`, `ended_at`, `message_count`, `chunked`.

`chunked` is set only by the final re-chunk at conversation close. Checkpointing
a live tail does not set it: a conversation is chunked when it has been chunked
in full, not when part of it happens to be retrievable.

### `messages`
Mirrors `archive.messages`, plus foreign keys to `conversations` and `users`.

`user_id` is carried explicitly rather than joined through the conversation. A
conversation has exactly one user, so the column is redundant — but attribution
has to survive onto chunks, and a join that can be forgotten is worse than a
column that cannot.

### `chunks` — the canonical retrievable unit

| column | notes |
|---|---|
| `id` | TEXT PK |
| `conversation_id` | NULL for non-conversation chunks (files, creative writing) |
| `user_id` | attribution, carried through from the message |
| `text` | NOT NULL |
| `source_type` | **NOT NULL** — provenance |
| `source_trust` | **NOT NULL** — provenance |
| `chunk_index` | ordinal within its conversation; NULL otherwise |
| `first_message_id`, `last_message_id` | traces a chunk back to its raw turns |
| `text_sha256` | detects text changing underneath a derived index |
| `created_at`, `updated_at` | |

**ChromaDB and the FTS5 index are both derived from this table and rebuildable
from it.** The reference build had no canonical chunk row — chunks existed only
inside Chroma and FTS — which is what made orphaned index entries possible there
and required a dedicated purge tool. A canonical row turns that class of bug
into a foreign-key violation instead of a maintenance task.

Provenance is `NOT NULL` because task 1.7 requires that no chunk can be written
without it, and a constraint holds where a convention does not. The permitted
*values* are a vocabulary owned by `anam/memory/provenance.py`, deliberately not
a CHECK constraint — adding a source type should not require a migration.

A partial unique index keeps `(conversation_id, chunk_index)` unique where both
are present, while letting many non-conversation chunks share a NULL index.

### `chunks_fts`
FTS5 over `chunks.text` as an **external-content** table: the text is not stored
twice, and three triggers (insert/delete/update) make desync from `chunks`
impossible rather than merely unlikely.

The reference build created its FTS table in a separate `execute()` with a
comment saying `executescript` could not reliably mix virtual-table DDL with
regular DDL. That does not reproduce on SQLite 3.53.1 — verified directly — so
it is declared inline here.

### `supersedes` — corrections (decision #2)

`superseding_chunk_id` and `superseded_chunk_id`, both FK to `chunks`, plus
`classifier_model`, `confidence`, `rationale`, `created_at`.

A correction never edits or deletes what it corrects. It links to it, and
retrieval resolves the link (task 3.5). A chain of corrections resolves by
following links forward. Two constraints matter:

- `UNIQUE (superseding, superseded)` — no duplicate links.
- `CHECK (superseding <> superseded)` — a self-link would make forward
  resolution non-terminating.

Recording which model judged the link and how confident it was is what makes a
bad link auditable after the fact.

#### The cycle guard

`UNIQUE (superseding, superseded)` treats `(A,B)` and `(B,A)` as different
tuples, so the unique index and the self-link CHECK together still permit a
loop: "A supersedes B" and "B supersedes A" are two individually valid rows.
Longer loops (A→B→C→A) are equally representable. Since resolution follows links
forward, a loop of any length means resolution never terminates.

Two `BEFORE` triggers — one on INSERT, one on UPDATE of either link column —
walk forward from the proposed superseding chunk with a recursive CTE and abort
if the proposed superseded chunk is already reachable. That condition is exactly
"this link closes a loop". `UNION` rather than `UNION ALL` keeps the walk itself
terminating even on data that is somehow already cyclic.

This lives in the schema rather than in the classifier because the writer is not
always the classifier: a restored older store, a hand-edited row, or a future
bug can all produce a link. A guarantee that only holds when one particular
caller is careful is not a guarantee.

**The trigger rejects a link, never a chunk.** No raw experience is touched by
it, which keeps it consistent with provenance being sacred.

#### Requirement this places on task 3.5

The write-time guard keeps cycles out of the table. It does **not** relieve
retrieval of terminating safely:

> Task 3.5's forward resolution must carry a visited set and stop when it
> revisits a chunk, rather than relying on the data being acyclic.

The two solve different problems and neither replaces the other. The trigger
preserves *correctness* — without it, a wrong classifier judgment quietly makes
the store self-contradictory. The visited set preserves *termination* — a cycle
arriving from a restore or a future bug must not hang or blow the stack in the
middle of a live conversation, which is the worst possible place to discover it.
If only one could exist, the trigger is the more valuable, but retrieval hanging
mid-turn is severe enough that the read-side check is not optional.

### `settings` (decision #8)
`key` PK, `value`, `value_type` (CHECK-constrained), `updated_at`, `updated_by`.

Authoritative at runtime for any key it holds a row for. TOML and env provide
the bootstrap default until a row exists. Nothing reads both at request time.

### `artifacts`
Metadata about files that live on disk under `workspace/`. Carries provenance
(`origin`, `source_role`, `source_conversation_id`, `source_message_id`,
`source_tool_name`), `revision_of` for lineage, `checksum`, and `metadata_json`.

### `research_candidates` (decisions #3, #4)
Proposals only — a row is inert until a human approves it. Self-flagged and
mined candidates land here identically, distinguished by `source` rather than by
living in separate tables.

*Flagged for review: this is the one table with no consumer until Phase 5.*

---

## Changing the schema

Two routes, and picking the wrong one is how a database ends up in a state no
version number describes:

- **New objects** — a new table, index, trigger or virtual table — can be added
  to `working.sql` directly. Every statement there is `CREATE ... IF NOT
  EXISTS`, and `init_databases()` re-runs the whole file on every startup, so a
  new object appears on existing databases without a migration.
- **Anything that changes or backfills existing data** — `ALTER TABLE`, a column
  type change, a data rewrite — needs a `Migration` in
  `anam/memory/migrations.py`. `IF NOT EXISTS` cannot express those, and they
  must run exactly once.

Never edit or renumber an applied migration: a database that already ran version
N will not run it again, so changing it produces two different schemas both
claiming to be version N.

---

## Go-live wipe (Phase 10, decision #16)

**Confirmed procedure, recorded here so Phase 10 does not have to rediscover it.**

Delete both `archive.db` and `working.db` outright, then recreate the schema
fresh and empty via `init_databases()`. That is the whole operation.

- **No special handling for the archive.** Append-only and frozen describe how
  it behaves in normal operation, not a protection against deliberate deletion.
  The go-live wipe deletes it like any other file.
- **No partial preservation, and no carve-out for "genuine" history.** This
  build's data is disposable test data throughout. Do not build a
  preserve-some-rows exception into the wipe tooling — that was the prior
  project's more cautious stance and it is explicitly not this one's
  (`PROJECT.md`, decision #16).
- **Nothing else needs clearing for the databases' sake.** ChromaDB and the FTS5
  index are both derived from `chunks`, so a wipe of the SQLite stores plus a
  fresh Chroma collection leaves no orphaned state. That is a property of the
  canonical-`chunks` design above; it would not hold if chunks lived only in the
  indexes.

The wipe is Tier 3 and the point of no return for this build's test data —
confirm explicitly with Lyle before executing it.

---

## Deliberately absent

Each of these is a decision, not an oversight, and each is asserted by a test
(`test_deferred_features_have_no_tables`):

- **`review_items`** — the review queue is out of this build (decision #14).
- **Anything self-modification-shaped** — no seam anywhere, not even a nullable
  column (decision #15).
- **`summaries`** — nothing is ever summarised. History windowing drops turns
  from the prompt, never from the record (decision #6).
- **`excluded_chunks`** — the fabrication gate refuses to persist a bad turn
  rather than storing it and filtering it later, and a full wipe precedes
  go-live (decisions #1, #16). The reference build needed this; this one should
  not.
- **`channel_identifiers`** — one channel in this build.
