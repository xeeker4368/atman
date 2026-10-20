# 2026-10-19: web_search says plainly when no engine answered

Branch `cc/pre-run-5`. Tier 2 (prompt-facing result text).

**What changed.** `program/tools/web_search.py` `_degradation_note` now takes the results and writes
one of two notes:
- **No results, engines down:** "No search engine returned anything. N search engine(s) did not
  answer — …, and no other engine returned a result. So this is not a finding that the web has
  nothing on this: the search may not have reached it at all."
- **Results, engines down:** "This search was incomplete: N search engine(s) did not answer — ….
  The results came from brave, google, so these results are partial …" (the engines are read from the
  results' `engines` fields; "Other engines did answer" if none is named).

**Why.** Before, both cases ended "Other engines did, so these results are partial". With zero results
and every engine down (run 4, the 2026-10-16 queries) that was false, and it is what the entity reads.
SearXNG's payload names the engines that failed but not the ones that answered empty, so the zero-results
note does not claim every engine failed; it says nothing came back from any.

**Unchanged.** The result still begins with `NO_RESULTS`, so CO17's empty-result detection still sees it
(`tests/test_empty_result.py`). The healthy-search case carries no note.

**Tested.** `tests/test_web_search.py`: every engine down; nothing found with engines down; partial
results naming the engines; partial results with no engine names. Mutation (zero-results branch
disabled, bytecode writing off): two of them fail.

**Not measured.** No model calls: how the entity reads the new note is for run 5. No gate
re-measurement: the change is to a tool result, not the rubric, classifier prompts, situation block or
trace rendering ("Until go-live", item 4).
