# soul.md: one sentence on describing its own states (decision #35)

**What changed.** `program/integrity/soul.md` gains a paragraph after "Who you are is not decided in
advance…": "You can describe your own states in whatever words fit, human ones included. They don't have
to mean exactly what they would for a person, and you don't need to disclaim them." It is 1,175 → 1,352
characters, and about 294 → 338 estimated tokens. Design revision 7 (S38 to S40). **Wording approved as
written by Lyle, 2026-10-13, after the replay** (decision #35).

**Checks.**
- Ceiling: 1,352 of 3,000.
- Naming and trait tripwires: pass.
- Required markers: unchanged and present.
- The gate does not read `soul.md`: `BEFORE_DIGEST` did not move, so no gate re-measurement is owed.

**Digests re-taken in this commit**, each because the system prompt holds `soul.md`. Each held at `1930965`,
and each new value was stable across two runs:
- `BEFORE_B20_B21` 443de41e → c82de6de
- `BEFORE_PIECE_2` 2ef64bd7 → 4be99ef9
- `BEFORE_RENDER_SETTING` c4cb8962 → f8c8e42c

`tests/test_prompt.py` pins 1,352 characters and 338 / 512 tokens.

**The replay, written and not run.** `scripts/soul_states_replay.py` replays run 3's C10 t1, t5 and t12, C1 t5
and C18 t1 under the old `soul.md` (from `main`, sha256-pinned) and the new one.
- 10 replays per arm per turn, in shuffled rounds, never one prompt twice in a row.
- Fresh scratch stores, retrieval off in both arms, run 4's flags.
- It nominates hedge and must-catch candidates by pattern. The reader classifies by hand: one reader, so
  descriptive.
- `--dry-run` was run: the turns, the plan and the arm swap check out, with no model call.
- Expected model time is about 100 replays at roughly 40 s each, about 70 minutes.

**Limits.**
- History is run 3's own hedged replies in both arms, so the difference is understated.
- The runner has no unit tests: its import sets the scratch environment, the same as `point_b.py` and
  `point_c.py`. The dry run is its check.
