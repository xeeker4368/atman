#!/usr/bin/env python3
"""Set a user's password. Operator-run, from a shell on this machine.

    python scripts/set_password.py jodie

This is the only way a password is ever set or changed. There is no
self-service reset, no email flow and no admin-panel field — for a two-person
household the operator setting a new password *is* the reset path
(docs/AUTH_DESIGN.md A9).

The password is read with ``getpass``, never taken as an argument: an argv
password lands in shell history and is visible in ``ps`` to anyone on the box.

This is ``GUIDANCE.md``'s carve-out in its plainest form — a human is directly
driving the action, at a shell, on the machine. It constructs
``Actor.operator()`` to say so explicitly, though no capability is registered
for password setting: a capability the operator sentinel always satisfies would
deny nothing, and ``permissions.py`` keeps its registry to capabilities that
are actually enforceable.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from program import auth  # noqa: E402
from program.memory import db  # noqa: E402
from program.settings.permissions import Actor  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0] if argv else 'set_password.py'} <user-name>")
        return 2

    name = argv[1]
    operator = Actor.operator()

    user = db.get_user_by_name(name)
    if user is None:
        # Named plainly: this is an operator at a shell, not a login attempt.
        # The uninformative-error rule exists to stop an attacker enumerating
        # users over HTTP; it would only waste the operator's time here.
        print(f"no such user: {name!r}")
        return 1

    password = getpass.getpass(f"New password for {user['name']} ({user['role']}): ")
    if not password:
        print("empty password; nothing changed")
        return 1
    if password != getpass.getpass("Confirm: "):
        print("passwords did not match; nothing changed")
        return 1

    db.set_password_hash(user["id"], auth.hash_password(password))
    print(f"password set for {user['name']} (by {operator.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
