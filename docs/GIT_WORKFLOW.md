# Git workflow

## The rule

CC never commits to main, and never pushes, merges, rebases or rewrites history. For each task CC creates a branch `cc/<piece>` from the current main, commits to it with explicit `git add <file>` (never `-A` or `.`), and stops. Lyle reviews `git diff main..cc/<piece>`, runs the leak check, squash-merges, commits and pushes. A Tier 3 piece's branch is merged before the next piece's branch is created.

CC also never uses `git reset --hard`, `git branch -D` or `git config`, never switches to main after creating its branch, and never uses `--no-verify`. Commit messages are public, so they follow the public-repo rules in `AGENTS.md` (no keys, account names or real conversation text).

## Hooks

Two local hooks enforce the rule. They live in `.git/hooks` and are **not part of the repository**, so a fresh clone must reinstall them (save each file, `chmod +x`).

`.git/hooks/pre-commit`:

```sh
#!/bin/sh
[ "$ALLOW_MAIN" = 1 ] && exit 0
b=$(git symbolic-ref --short HEAD 2>/dev/null)
case "$b" in
  main|master) echo "refused: no commits on $b. Work on a cc/ branch."; exit 1;;
esac
exit 0
```

`.git/hooks/pre-push`:

```sh
#!/bin/sh
[ "$ALLOW_PUSH" = 1 ] && exit 0
echo "refused: pushes are Lyle's. Run: ALLOW_PUSH=1 git push"
exit 1
```

**The leak check in the pre-push hook.** Add this line to `.git/hooks/pre-push` **as the first
line after `#!/bin/sh`**, before the `ALLOW_PUSH` line, so it runs on every push including an
allowed one (CC does not edit `.git/hooks`; Lyle adds it):

```sh
"$(git rev-parse --show-toplevel)/venv/bin/python" "$(git rev-parse --show-toplevel)/scripts/leak_check.py" || exit 1
```

It scans `@{upstream}..HEAD` (every commit the push would publish), diffs and messages, against
the patterns in `~/.config/anam/leak-patterns.txt` (or `$ANAM_LEAK_PATTERNS`): one plain-text
pattern per line, case-insensitive, `#` comments and blank lines ignored. A missing or empty
pattern file, or a branch with no upstream, stops the push (exit 2): the check fails closed.
On a match it prints the pattern, the commit and the file, never the matched line. To check a
branch before merging: `venv/bin/python scripts/leak_check.py main..cc/<piece>`.

## Lyle's review and merge

**Who must look first** (until Phase 10, `AGENTS.md` "Until go-live", item 9): a Tier 0 to 2
piece whose report's merge checklist is clean (suite green, `ruff` clean, `leak_check` clean,
no stop condition, no file outside the plan) may be merged by Lyle without external review.
A Tier 3 piece, or any report with a stop condition or an open decision, goes to the reviewer
first. CC never merges either way.

1. `git diff --stat main..cc/<piece>` and `git log -p main..cc/<piece>`: read both.
2. `git switch main`, then `git merge --squash cc/<piece>`, then `git diff --cached`.
3. Run the leak check over the branch history (`venv/bin/python scripts/leak_check.py main..cc/<piece>`) and read the staged diff (step 2); the pre-push hook runs it again over the squash commit.
4. `ALLOW_MAIN=1 git commit`, then `ALLOW_PUSH=1 git push`.
5. `git branch -D cc/<piece>`.

**Why squash:** intermediate commits never reach main, so a leak added and later removed on a branch cannot reach public history. Docs written on a branch must not cite that branch's commit hashes or say the branch is awaiting merge, because the squash gives the work a new hash and the branch is deleted; cite the item or `git log --grep '<piece>'` instead.

## Stopping, naming, parallel work

- **Stop rule:** a Tier 3 piece stops after each piece. The next branch is created only after the previous one is merged.
- **Naming:** `cc/<piece>`, one branch per piece.
- **Parallel sessions:** give each lane its own working directory with `git worktree add`, so two sessions never share one checkout.
- **Two lanes until Phase 10** (`AGENTS.md` "Until go-live", item 6): two branches may be open at once if they touch no common file and only one makes model calls; not beside an unmerged Tier 3 branch.
- A task report names the branch, shows `git diff --stat main..<branch>` and `git log --oneline main..<branch>`, and says which files another open branch also touches.
