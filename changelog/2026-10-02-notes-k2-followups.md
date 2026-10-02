# 2026-10-02 — Notes set K2: two follow-ups to pieces 4 and 5, and piece 5's BUILT entry

Requested at review of piece 5. Tier 2.

- **The review output's closing line follows the proposal's status** (`scripts/note.py`,
  `OUTCOME_LINES`). Piece 4 printed *"A proposal is pending. Nothing is saved until you approve it."*
  under every entity reply, including for a proposal already decided. It now reads the row's own
  status: pending, approved, edited, rejected, applied_without_review, and never says "pending" for a
  decided one or "saved" for a pending or rejected one. A test per status; three mutations killed.
- **`note_propose` does nothing fallible after the insert.** The `ToolOutput` and `Record` (and the
  log line) are built first, so the insert is the last statement before the return. Otherwise an error
  after the commit would be a `tool_error` with a row present, which piece 5's receipt reads as *"Not
  proposed. Nothing was recorded."* about a record that exists. A test makes the build fail and asserts
  no row; an AST test pins the insert as the last statement. Both fail when the order is reversed.
- **`BUILT.md`:** piece 5's entry.

Full suite and `ruff`: see the piece 6 report.
