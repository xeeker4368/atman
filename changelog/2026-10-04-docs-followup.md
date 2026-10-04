# 2026-10-04: docs follow-up

Documentation only. Nothing committed or staged, no server, no model call. `ARCHITECTURE.md`, `BUILT.md`, `config/defaults.toml`, `program/engine/ollama.py` and `start.sh` were not touched.
`changelog/2026-10-04-docs-restructure.md` is already committed (`18c7d56`), so this is a new file.

## Changes
1. **`AGENTS.md` rule 3** restored to "No keys, no account names, no real conversation text, and no raw entity replies from real conversations." Only the draft's Part C pointer and the sentence about replies from scratch runs on invented data stay out.
2. **`NOW.md` and `docs/archive/NOW-closed-backlog.md`.**
   - **Moved after reading the diffs (each closes the whole item):**
     - *B6b, the entity's `users` row* (`9a616cc`). The item's gap was that `scripts/set_password.py` could set a hash on `__entity__`, after which every route behind `require_actor` would accept it, and that closing it needs an auth-side guard on credential operations and token issue. The diff adds all four guards the item lists: `db.set_password_hash` raises "refusing to set a password on the entity's reserved row"; `scripts/set_password.py` refuses the name at line 43, before `getpass` at line 58; `auth.login` refuses `db.ENTITY_USER_NAME` through the dummy verification; `auth.actor_for_header` returns `None` for a token whose actor is the entity. Tests added in the same commit: `test_a_password_cannot_be_set_on_the_entity_row`, `test_the_entity_row_cannot_log_in_even_if_it_somehow_had_a_password`, `test_a_token_for_the_entity_row_is_refused`, `test_the_set_password_script_refuses_the_entity_row_before_prompting`.
     - *CO10.3, how the entity should describe supersession* (`d63206e`). The item's open question was whether anything should teach the entity the accurate framing. The diff adds a seven-line clause to `program/integrity/soul.md` ("your earlier statement is not overwritten anywhere … recorded as a link marking it superseded … Do not say you 'updated the record' … Do not say the link has been made"), still present at HEAD (`soul.md` lines 39 to 41), with `tests/test_prompt.py` and `docs/SOUL_AND_PROMPT_DESIGN.md` revision 4. The follow-up item about D3's "superseded" wording is separate, stays open, and is named in the index line.
   - **Scratch-helper item** (`fb25f6e`) kept in place; its status line now reads "Built 2026-10-03 (`fb25f6e`)" and the "Not done" bullet is untouched.
   - **Stale statements corrected (status wording only; no decision changed):**
     - "The journal is not built yet" (the corrections-do-not-reach-journal-chunks item) now says the journal is built (`2cde154`, 2026-10-01) and entries enter memory only on `--index`, so the gap applies from the first indexed entry. **Correction to my earlier report:** only that item said "not built"; I had named the `messages.integrity_check` item as well. That one said "The journal gets a reader…", which I changed to "has a reader … built in `2cde154`".
     - "Overwritten each session" replaced (git history holds earlier versions).
     - The decision-log preamble no longer says all decisions were made before code existed: entries 1 to 19 were (`e46ae7a` has 19), entries 20 to 23 were added during the build (`8e27aab`, 2026-09-02, has 20).
3. **Go-live checklist** gains three lines attributed "Lyle, 2026-10-04": rotate the Moltbook API key before Phase 8; cap Docker memory for the SearXNG container; B22 closed before go-live. The pre-go-live soak test line was already there (Phase 10 has one). The tested-restore line stays as "also open, not stated as a go-live requirement".
4. **`BUILD_PLAN.md`.** Only the Phase 2 agent-loop row carried the 30/20 numbers (the Phase 1 idle-close row names none). They are kept and marked superseded: part (a) was done 2026-09-08 (`changelog/2026-09-08-idle-close-re-derivation.md`), and the values are now `in_flight_grace_minutes = 41` (`config/defaults.toml`) and `IN_FLIGHT_GRACE_FLOOR_MINUTES = 35` (`program/config.py`).
5. **`ARCHITECTURE.md` size, unchanged:** 313 lines, 80,795 characters, 573 citations verified, 0 unresolved (`/tmp/arch/check.py`). The 15 longest lines are 819, 783, 780, 765, 717, 707, 705, 702, 694, 665, 647, 634, 622, 592 and 581 characters (lines 200, 240, 86, 212, 161, 185, 100, 201, 279, 287, 90, 152, 157, 199, 188).

## Commit sets (this follow-up's files only; each in exactly one set)
All four are files already in the earlier uncommitted sets B, C and D, so they fold into those if you have not committed them yet.
- **B** `AGENTS.md`
- **C** `NOW.md`, `docs/archive/NOW-closed-backlog.md`
- **D** `BUILD_PLAN.md`
- **F** `changelog/2026-10-04-docs-followup.md`
