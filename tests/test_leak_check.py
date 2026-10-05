"""scripts/leak_check.py: the pre-push scan of diffs and commit messages.

Every test builds its own git repository and pattern file under tmp_path; nothing reads the
real pattern file or this repository's history.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import leak_check

SCRIPT = Path(leak_check.__file__)
SECRET_LINE = "api_token = sk-live-ZZZTOPSECRETZZZ and more context"
PATTERN = "sk-live-"


def git(repo: Path, *args: str) -> str:
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo, env=env,
                          check=True, capture_output=True, text=True).stdout


def commit(repo: Path, name: str, text: str, message: str = "a change") -> None:
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit(r, "README.md", "base\n", "base")
    git(r, "branch", "base")
    return r


@pytest.fixture
def patterns(tmp_path):
    p = tmp_path / "leak-patterns.txt"
    p.write_text(f"# comment line\n\n{PATTERN}\nanother-pattern\n", encoding="utf-8")
    return p


def run(repo: Path, *argv: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *argv], cwd=repo, capture_output=True,
                          text=True, env={**os.environ, **(env or {})})


def test_a_match_in_a_diff_names_pattern_commit_and_file_and_never_the_line(repo, patterns):
    commit(repo, "config.txt", "harmless\n" + SECRET_LINE + "\n")
    sha = git(repo, "rev-parse", "HEAD").strip()

    out = run(repo, "base..HEAD", "--patterns", str(patterns))

    assert out.returncode == 1
    assert repr(PATTERN) in out.stdout and sha[:12] in out.stdout and "config.txt" in out.stdout
    printed = (out.stdout + out.stderr).lower()
    assert "zzztopsecretzzz" not in printed, "the matched line was printed"
    assert "harmless" not in printed


def test_no_match_is_clean(repo, patterns):
    commit(repo, "notes.txt", "nothing to see\n")
    out = run(repo, "base..HEAD", "--patterns", str(patterns))
    assert out.returncode == 0
    assert "clean, 1 commit(s)" in out.stdout


def test_a_match_in_a_commit_message_is_caught(repo, patterns):
    commit(repo, "notes.txt", "nothing to see\n", message=f"fix: rotate {PATTERN}abc")
    out = run(repo, "base..HEAD", "--patterns", str(patterns))
    assert out.returncode == 1
    assert "commit message" in out.stdout
    assert f"{PATTERN}abc" not in out.stdout.replace(repr(PATTERN), "")


def test_matching_is_case_insensitive_on_both_sides(repo, tmp_path):
    mixed = tmp_path / "mixed.txt"
    mixed.write_text("Mixed-Case-Token\n", encoding="utf-8")
    commit(repo, "notes.txt", "a MIXED-case-TOKEN here\n")
    assert run(repo, "base..HEAD", "--patterns", str(mixed)).returncode == 1


def test_a_removed_line_is_not_a_new_leak(repo, patterns):
    commit(repo, "config.txt", SECRET_LINE + "\n")
    git(repo, "branch", "-f", "base")
    commit(repo, "config.txt", "cleaned\n")
    assert run(repo, "base..HEAD", "--patterns", str(patterns)).returncode == 0


def test_a_missing_pattern_file_fails_closed(repo, tmp_path):
    out = run(repo, "base..HEAD", "--patterns", str(tmp_path / "absent.txt"))
    assert out.returncode == 2
    assert "not found" in out.stderr


def test_an_empty_pattern_file_fails_closed(repo, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("# only a comment\n\n   \n", encoding="utf-8")
    out = run(repo, "base..HEAD", "--patterns", str(empty))
    assert out.returncode == 2
    assert "no patterns" in out.stderr


def test_the_environment_variable_names_the_pattern_file(repo, patterns):
    commit(repo, "config.txt", SECRET_LINE + "\n")
    out = run(repo, "base..HEAD", env={leak_check.ENV_VAR: str(patterns)})
    assert out.returncode == 1


def test_the_default_pattern_file_is_outside_the_repository():
    assert leak_check.DEFAULT_PATTERNS == Path.home() / ".config" / "anam" / "leak-patterns.txt"
    root = Path(leak_check.__file__).resolve().parents[1]
    assert root not in leak_check.DEFAULT_PATTERNS.resolve().parents


def test_no_upstream_fails_closed_rather_than_checking_nothing(repo, patterns):
    out = run(repo, "--patterns", str(patterns))  # default range, and this repo has no upstream
    assert out.returncode == 2
    assert "pass a range" in out.stderr


def test_every_commit_in_the_range_is_checked_not_only_the_tip(repo, patterns):
    commit(repo, "a.txt", SECRET_LINE + "\n")
    commit(repo, "b.txt", "later and harmless\n")
    out = run(repo, "base..HEAD", "--patterns", str(patterns))
    assert out.returncode == 1 and "a.txt" in out.stdout
