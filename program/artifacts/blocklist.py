"""Governance files cannot be ingested as ordinary memory.

`BUILD_PLAN.md`'s Phase 2 row: *"soul.md, project docs can't be ingested as
normal memory"*, under the generalisation note that says to **match by resolved
directory, not an enumerated filename list** — because a filename list silently
fails to cover whatever governance file a later phase adds.

Why this is content-identity and not a path check
=================================================
``ingest.ingest()`` takes **bytes**, not a path. The upload route reads them off
an ``UploadFile``; the only path-like thing in the request is the client-supplied
filename, which ``ingest.py`` already refuses to treat as a path. So there is
nothing to resolve at the moment of an upload, and matching the filename would be
exactly the string comparison the note forbids — ``soul.md`` renamed to
``notes.txt`` would sail through.

So the directory rule stays the **source of truth** and the check is derived from
it: resolve each blocked directory, walk it, hash every file, and compare an
upload's bytes against that set. Three things follow, all of them the point:

* A governance file added later — ``program/integrity/architecture.md`` in
  Phase 3, say — is covered by the walk with no code change here. That is the
  failure the note was written about.
* A blocked file renamed to anything at all is still refused, because identity
  is its content.
* :func:`is_blocked_path` remains the rule itself, exercised by the walk, and
  ready for the path-taking callers Phase 4 brings. It is not an unmounted gate.

What this does not catch, stated rather than hidden
====================================================
**A near-copy.** Change one byte of ``soul.md`` and the hash no longer matches.
Catching that needs similarity above some threshold, and an uncalibrated
threshold is precisely what this project refuses to ship — the retrieval floors
are unset for the same reason (``RETRIEVAL_DESIGN`` D4). This stops the accident
and the straightforward copy; it does not stop a determined edit.
``tests/test_blocklist.py`` asserts that gap explicitly, so it is a known
property rather than an assumption nobody checked.

Not configurable, deliberately
===============================
The blocked set is derived from the source tree, not from a setting. A
settings-backed blocklist is one an admin panel could turn off, and this is not
a preference — ``soul.md`` is the fabrication gate's ground truth, and
``config/`` holds ``auth.session_secret``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from program import config

logger = logging.getLogger(__name__)


class GovernanceFileError(Exception):
    """An upload matched a governance or configuration file.

    **Its own type, not an extraction outcome.** ``metadata_only`` means *stored
    but not read*; this file was recognised and refused, and nothing was stored.
    Recording a refusal as a successful-but-unread upload would make the
    ``artifacts`` table describe something that did not happen — the exact
    distinction ``extraction_status`` exists to keep.
    """


#: Directories whose entire contents are blocked, relative to the project root.
#: Recursive.
BLOCKED_DIRECTORIES = (
    # soul.md, and whatever else lands here — Phase 3's architecture.md is the
    # case BUILD_PLAN's note names by example.
    "program/integrity",
    # Designs of record.
    "docs",
    # Dated records of what changed and why.
    "changelog",
    # NOT governance, included on a sharper ground: config/local.toml holds
    # auth.session_secret. Ingesting it would write the HMAC signing key into
    # artifacts.extracted_text AND into chunks — retrievable forever, and
    # surfacing in the entity's own context. That is a credential leak into
    # memory, not a documentation-hygiene question.
    "config",
)

#: Markdown directly at the project root: AGENTS, BUILD_PLAN, BUILT, CLAUDE,
#: GUIDANCE, NOW, PROJECT, README.
#:
#: **A rule, not a list** — a root document added next month is covered without
#: an edit here, which is the whole complaint the generalisation note makes about
#: enumerated filenames.
#:
#: **Non-recursive, deliberately.** ``workspace/`` sits under the project root
#: and decision #10 requires creative writing to be indexed into memory like
#: everything else; ``data/artifacts/`` holds files already uploaded. A recursive
#: root rule would block both and contradict a settled decision.
ROOT_DOCUMENT_GLOB = "*.md"


def blocked_directories() -> tuple[Path, ...]:
    """The blocked directories, resolved. Absent ones are skipped."""
    resolved = []
    for relative in BLOCKED_DIRECTORIES:
        path = (config.PROJECT_ROOT / relative).resolve()
        if path.is_dir():
            resolved.append(path)
    return tuple(resolved)


def is_blocked_path(path: Path | str) -> bool:
    """Whether a filesystem path falls inside a blocked location.

    **Fully resolved first, symlinks included.** A link in an innocent directory
    pointing at ``program/integrity/soul.md`` resolves to the real file and is
    refused; a string comparison against the supplied path would not see it.
    Same discipline as ``web_fetch`` validating the address a socket actually
    connected to rather than the hostname it was given.
    """
    try:
        resolved = Path(path).resolve()
    except OSError:
        # Unresolvable — a broken link, a permission wall. Treated as blocked:
        # a path we cannot check is not a path we may assume is safe.
        return True

    for directory in blocked_directories():
        if resolved == directory or directory in resolved.parents:
            return True

    root = config.PROJECT_ROOT.resolve()
    if resolved.parent == root and resolved.match(ROOT_DOCUMENT_GLOB):
        return True
    return False


def governance_files() -> list[Path]:
    """Every file currently covered by the rule above.

    Walked fresh on each call rather than cached. It costs ~10 ms against the
    ~75 ms of a single embedding call, and a cache would go stale exactly when it
    matters most — these files are edited constantly during development, and
    ``NOW.md`` is rewritten every session.
    """
    found: list[Path] = []
    for directory in blocked_directories():
        found.extend(p for p in directory.rglob("*") if p.is_file())
    found.extend(
        p for p in config.PROJECT_ROOT.resolve().glob(ROOT_DOCUMENT_GLOB)
        if p.is_file()
    )
    return found


def governance_hashes() -> dict[str, Path]:
    """sha256 → the file it belongs to, for every governance file.

    The path is carried so a refusal can say *which* file matched **in the log**.
    It must never reach an HTTP response: Jodie can upload, and naming the file
    would confirm internal structure to a caller who should not learn it.
    """
    digests: dict[str, Path] = {}
    for path in governance_files():
        try:
            digests.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), path)
        except OSError as exc:
            # A file we cannot read cannot be hashed, so it cannot be matched.
            # Logged rather than swallowed: it is a hole in the check, and a
            # silent hole is worse than a narrow one.
            logger.warning("governance file %s could not be hashed: %s", path, exc)
    return digests


def check(data: bytes, digest: str | None = None) -> None:
    """Raise :class:`GovernanceFileError` if these bytes are a governance file.

    ``digest`` is accepted because the caller has usually computed it already —
    ``ingest()`` needs the same hash for its duplicate check — and hashing a
    10 MB upload twice for no reason is waste, not safety.
    """
    digest = digest or hashlib.sha256(data).hexdigest()
    matched = governance_hashes().get(digest)
    if matched is None:
        return

    try:
        shown = matched.relative_to(config.PROJECT_ROOT.resolve())
    except ValueError:
        shown = matched
    # The detail goes here, where the operator can see it and an unprivileged
    # uploader cannot. The exception's own message carries none of it.
    logger.warning(
        "refused an upload matching the governance file %s — it is part of the "
        "system's own configuration and is not ingestible as memory.",
        shown,
    )
    raise GovernanceFileError(
        "This file is part of the system's own governance or configuration and "
        "cannot be ingested as memory."
    )
