"""Check the commits that would be pushed for text that must not reach the public repository.

    python scripts/leak_check.py                 # the upstream range: @{upstream}..HEAD
    python scripts/leak_check.py main..cc/piece  # an explicit range
    python scripts/leak_check.py --patterns PATH

The repository is public (`AGENTS.md` working rule 3). This scans every commit in the range,
**both its diff and its commit message**, for plain-text patterns kept in a file **outside the
repository**, so the list of what must not leak never leaks itself:

- ``--patterns PATH``, else the environment variable ``ANAM_LEAK_PATTERNS``, else
  ``~/.config/anam/leak-patterns.txt``.
- One pattern per line, matched case-insensitively as plain text (not a regular expression).
  Blank lines and lines starting with ``#`` are ignored.

**It fails closed.** A missing pattern file, one with no patterns, a range git cannot resolve
(including no upstream), or a git error is exit 2, never a pass.

**What it reads in a diff:** the lines a commit adds. A removed line was already in the
parent, so pushing its removal publishes nothing new. A binary file's content is not read; its
path is named on a warning line so it can be checked by hand.

**What it prints on a match:** the pattern, the commit and the file (or "commit message") —
never the matched line, so a leak is not copied into a terminal log or a hook's output.

Exit codes: 0 clean, 1 at least one match, 2 the check could not run.

No ``program`` import: this reads git only, so it needs no scratch environment.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "ANAM_LEAK_PATTERNS"
DEFAULT_PATTERNS = Path.home() / ".config" / "anam" / "leak-patterns.txt"
DEFAULT_RANGE = "@{upstream}..HEAD"
MESSAGE = "commit message"


class CheckError(RuntimeError):
    """The check could not run. Exit 2: a check that cannot run has not passed."""


@dataclass(frozen=True)
class Match:
    pattern: str
    commit: str
    where: str  # a file path, or MESSAGE


def patterns_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get(ENV_VAR):
        return Path(os.environ[ENV_VAR]).expanduser()
    return DEFAULT_PATTERNS


def load_patterns(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise CheckError(f"pattern file not found: {path}") from exc
    except OSError as exc:
        raise CheckError(f"pattern file unreadable: {path} ({exc.strerror})") from exc
    patterns = [line.strip() for line in lines]
    patterns = [p for p in patterns if p and not p.startswith("#")]
    if not patterns:
        raise CheckError(f"pattern file has no patterns: {path}")
    return patterns


def _git(args: list[str], cwd: Path | None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise CheckError(f"git {args[0]} failed: {detail[-1] if detail else 'no output'}")
    return result.stdout.decode("utf-8", "replace")


def commits(revision_range: str, cwd: Path | None = None) -> list[str]:
    return _git(["rev-list", "--reverse", revision_range], cwd).split()


def added_lines_by_file(
    commit: str, cwd: Path | None = None,
) -> tuple[dict[str, list[str]], list[str]]:
    """({path: [added line, ...]}, [binary paths]) for one commit, against its first parent."""
    diff = _git(["show", "--format=", "--no-color", "--no-ext-diff", "--unified=0",
                 "--first-parent", commit], cwd)
    added: dict[str, list[str]] = {}
    binary: list[str] = []
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            current = line.split(" b/", 1)[1] if " b/" in line else line[len("diff --git "):]
            added.setdefault(current, [])
        elif line.startswith("+++ "):
            if line != "+++ /dev/null":
                current = line[len("+++ b/"):] if line.startswith("+++ b/") else line[4:]
                added.setdefault(current, [])
        elif line.startswith("Binary files ") and current is not None:
            binary.append(current)
        elif line.startswith("+") and current is not None:
            added[current].append(line[1:])
    return added, binary


def scan(revision_range: str, patterns: list[str],
         cwd: Path | None = None) -> tuple[list[Match], list[tuple[str, str]], int]:
    """(matches, binary files not read as (commit, path), commits checked)."""
    lowered = [(p, p.lower()) for p in patterns]
    matches: list[Match] = []
    skipped: list[tuple[str, str]] = []
    checked = commits(revision_range, cwd)
    for commit in checked:
        message = _git(["show", "-s", "--format=%B", commit], cwd).lower()
        for pattern, low in lowered:
            if low in message:
                matches.append(Match(pattern, commit, MESSAGE))
        added, binary = added_lines_by_file(commit, cwd)
        skipped.extend((commit, path) for path in binary)
        for path, lines in added.items():
            text = "\n".join(lines).lower()
            for pattern, low in lowered:
                if low in text or low in path.lower():
                    matches.append(Match(pattern, commit, path))
    return matches, skipped, len(checked)


def main(argv: list[str] | None = None, cwd: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("range", nargs="?", default=DEFAULT_RANGE,
                        help=f"git revision range (default {DEFAULT_RANGE})")
    parser.add_argument("--patterns",
                        help=f"pattern file (default ${ENV_VAR}, then {DEFAULT_PATTERNS})")
    args = parser.parse_args(argv)

    try:
        patterns = load_patterns(patterns_path(args.patterns))
        matches, skipped, checked = scan(args.range, patterns, cwd)
    except CheckError as exc:
        print(f"leak_check: cannot run, failing closed: {exc}", file=sys.stderr)
        if args.range == DEFAULT_RANGE:
            print("leak_check: with no upstream, pass a range, e.g. main..HEAD", file=sys.stderr)
        return 2

    for commit, path in skipped:
        print(f"leak_check: warning: binary file not read: {path} (commit {commit[:12]})")
    for m in matches:
        print(f"leak_check: MATCH pattern {m.pattern!r} in commit {m.commit[:12]}, {m.where}")
    if matches:
        print(f"leak_check: {len(matches)} match(es) in {checked} commit(s)")
        return 1
    print(f"leak_check: clean, {checked} commit(s) checked against {len(patterns)} pattern(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
