# Moltbook fixtures

Captured from the live API on 2026-09-30 by read-only GETs, then **scrubbed
before being committed**. This repository is public. Design of record:
`docs/MOLTBOOK_READ_DESIGN.md` M10.

| file | captured from | the hazard it exists for |
|---|---|---|
| `posts_list.json` | `GET /posts?sort=hot` | `is_spam`/`is_deleted` flags; author objects carrying their own description |
| `feed_with_tip.json` | `GET /feed?sort=new` | a server-written `tip` field; stands in for any field Moltbook adds later. `/feed` itself is never called |
| `search_mixed.json` | `GET /search` (type all) | agent, comment and post results mixed; an agent result with `post: null` |
| `post.json` | `GET /posts/{id}` | a single post |
| `comments_empty.json` | `GET /posts/{id}/comments` | an empty reply list |
| `profile.json` | `GET /agents/profile` | a profile with `recentPosts` |
| `error_404.json` | `GET /agents/profile` (unknown name) | the observed error shape, which is not the published one |

## What the scrub did

- **Removed:** every `owner` and `claimed_by` field. They identify the real
  people behind an account. The read-agent test puts sentinels back in a copy
  to prove the renderer would not show them.
- **Replaced with synthetic values of the same shape:**
  - every id → `00000000-0000-4000-8000-NNNNNNNNNNNN`, mapped consistently, so
    cross-references still line up;
  - every agent name and display name → `agent-NN`;
  - every submolt name → `submolt-NN`;
  - every description, title, post, comment and preview text → filler of the
    **same length**, so the rendering caps still bite;
  - every `/u/<name>` and `/post/<id>` URL, following the maps above;
  - external links, avatars, cursors and the echoed search query.
- **The profile is the shape of the account the API key belongs to, and
  nothing else of it.** Its timestamps and counts were replaced as well,
  because together they would identify the account.
- **Kept:** keys, types, flags, other timestamps, the `tip` text (Moltbook's
  own wording, the hazard itself), and the 404's message.

## How the scrub was checked

- **At build time (not committed):** every string in every raw capture was
  compared against the fixtures. **0 of 193** identifier/text strings survived.
  **0 of 14** strings from the key's own account profile survived, timestamps
  included. The check needs the raw identifiers, so it cannot live here.
- **Committed:** `tests/test_moltbook.py` asserts that no fixture has an
  `owner` or `claimed_by` key at any depth, and that every agent name matches
  `agent-NN`.

## Synthetic, not captured

A 429 and a 3xx are built inside the tests from the published and observed
shapes. Provoking a real 429 means draining a live rate-limit bucket, which is
not a pure read. Non-empty replies are also built in the tests, since the
captured post had none.
