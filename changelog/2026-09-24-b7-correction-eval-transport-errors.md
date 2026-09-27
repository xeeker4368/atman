# B7: the correction eval no longer scores a server error as a judgment

Date: 2026-09-24 · merged-queue item 20 (diagnostic finding #16) · plan B7 · Tier 2
(measurement harness). Plan approved before implementation. The plan kept the fix out
of `classifier.py`, which is the fabrication gate's shared framework (O18).

## What was wrong

- `correction_eval.sample_once` drew the line between "the classifier answered unusably"
  (scored, as production behaves) and "no judgment was made" (`unavailable`, excluded)
  by exception type. It scored `ollama.OllamaResponseError` as unusable.
- `corrections._parse` raised that class for an unusable reply.
- `ollama.py` raises the same class for an HTTP error status, a non-model 404, and a
  body that isn't JSON. In those cases nothing was judged.
- So a run against a server returning 500s would have scored every run as an unusable
  reply: misses on link cases, passes on no-link cases. That is a report of clean
  false-link rates with nothing classified.
- The report shows `unusable_replies` beside the rates, which is why the diagnostic
  pass could check its own run. Nothing enforced the distinction.

## What changed

- **New `corrections.UnusableReplyError(ValueError)`.** `_parse` raises it in all three
  places where it raised `OllamaResponseError`.
  - It is deliberately **not** a subclass of `OllamaResponseError`, because sharing a
    type was the defect.
  - The docstring says why.
- **`correction_eval.sample_once`:**
  - `UnusableReplyError` → a scored unusable reply, as before;
  - any other exception → `unavailable`;
  - the docstring now says where the line is drawn.
- **Unused `ollama` imports removed** from both modules.
- **Untouched:** `classifier.py`, and production behaviour. `turn._record_corrections`
  catches `Exception`, so both kinds still mean "no link".

## Tested

- **`test_an_http_error_from_the_model_server_is_unavailable_not_a_scored_miss`.** An
  `OllamaResponseError` from the server gives 3/3 `UNAVAILABLE`, 0 unusable, and 0 link
  denominator.
- **`test_an_unusable_reply_is_still_scored_as_production_behaves`.** A reply with no
  verdict gives 2/2 `MISSED`, 2 unusable, and 2 link denominator. This is the other side
  of the line.
- **`test_a_transport_error_is_not_reported_as_an_unusable_reply`** (in
  `tests/test_corrections.py`). A server error passes through `classify()` as itself, and
  `UnusableReplyError` is not an `OllamaResponseError`.
- **Two existing `_parse` tests** now expect `UnusableReplyError`.
- **Proof it bites:** making `sample_once` score `OllamaResponseError` as unusable again
  fails the HTTP-error test.
- **End-to-end harness check against the real model.** This is not a new measurement of
  record.
  - 5 decorrelated passes over the frozen 17 cases, fingerprint `39ce8e41…`, on
    `gemma4:26b` at 0.35.
  - False links 0/50, missed 5/35 (all `C7`, the recorded failure), wrong target 0/35,
    wrong state 0/35.
  - **0 unavailable, 0 unusable.** Every case was unanimous.
  - This matches the measurement of record cell for cell at its own scale.
- **Full suite:** 1234 passed, 2 skipped (run after the eval, so the live tests didn't
  interleave with its sampling). `ruff check .` clean.

## Known limitations

- **The recorded 340-call measurement's one unusable reply can't be re-attributed.** No
  raw report was kept. It landed on a no-link case, so if it was a server error, that
  run's false-link denominator would go from 200 to 199. No rate changes.
- **Production logs don't distinguish the two.** `turn._record_corrections` logs "could
  not run" for both. It's a missed link either way, and changing the log wording was out
  of scope.
