# Notes v2: pass timing, and no pass from the post-turn sweep (2026-10-27)

Branch `cc/notes-v2`. Tier 2. Decisions on the timing report (Lyle, 2026-10-27).

**Measured** (one model process, kit stopped, scratch stores; descriptive, single runs; Ollama 0.35.0
with `OLLAMA_NUM_PARALLEL` 1):
- Run 4's longest conversation (24,110 characters, one piece): 6,459 prompt tokens, 126 output, 23.2 s.
- A synthetic maximal piece (88,393 characters, the notes list at its cap): 26,571 prompt tokens in
  103.5 s, 107 output in 4.3 s, 108 s.
- A turn sent during a pass waited about 19 s for it at the model. A 5 s-timeout call queued behind a
  pass timed out: **Ollama's queue time counts against a waiting call's own timeout.** The gate
  classifier's is 45 s, so a pass piece over about 41 s can leave a turn saved unchecked (`unavailable`).

**Changed:**
- `routes/chat.py` `_sweep` no longer calls `note_pass.drain`. Passes run from `close_idle_conversations
  --passes N` and `scripts.note pass`, both refusing while a server holds the store, and from the play
  kit's `/kit/end`. A conversation the sweep closes reads `not_run` until one of those runs.
- `notes.pass_timeout_seconds` stays 300; its comment carries the derivation (largest piece about 181 s).
- `BUILD_PLAN.md` Phase 6: the scheduler must keep background model work off a turn's path, with the open
  question of whether Ollama stops a call whose client timed out.
- `docs/BACKLOG.md`: `note_search`'s framing id, to drop at the next rendering change (NS1b to NS4).

**Tests:** `test_the_post_turn_sweep_never_runs_a_pass` (replaces the drain test) and
`test_both_pass_scripts_refuse_while_a_server_holds_the_store`; each caught its mutation (the drain put
back in the sweep; the store lock removed from `scripts.note pass`).
