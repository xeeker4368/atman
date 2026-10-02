"""`scripts.note` and `program/memory/note_admin.py`: Notes piece 4 (`docs/NOTES_BUILD_PLAN.md`).

The only human control on what a note may say. Every test runs on a temporary store; the real
`data/` is never opened (the suite's isolation guard would stop it). The identity classifier is
scripted, nothing else is.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from program import config
from program.attribution import AttributionContext
from program.engine import loop, turn
from program.integrity import classifier
from program.memory import db, migrations, notes
from program.memory import note_admin as admin
from program.origin import OriginContext
from program.settings.permissions import Actor, Role
from program.tools import note_texts as texts
from program.tools.note_propose import NOTE_PROPOSE
from program.tools.note_search import NOTE_SEARCH
from program.tools.registry import Tool, ToolRegistry
from scripts import note as cli

HEX = "0123456789abcdef"


def run_cli(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def world(isolated_data_dir, monkeypatch):
    config.reload()
    db.init_databases()
    monkeypatch.setattr(classifier, "classify", lambda p: "CONSISTENT")
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie")
    conv = db.start_conversation(lyle)
    other = db.start_conversation(jodie)

    def say(cid, uid, role, text, ts):
        return db.save_message(cid, uid, role, text, timestamp=ts)

    ids = {
        "context": say(conv, lyle, "user", "Jodie takes her coffee with oat milk, never dairy.",
                       "2026-10-02T14:00:00+00:00"),
        "trigger": say(conv, lyle, "user", "Please make a note of that for her.",
                       "2026-10-02T14:01:00+00:00"),
        "store": say(other, jodie, "user", "My grandmother kept her starter above the stove.",
                     "2026-09-30T09:00:00+00:00"),
    }
    origin = OriginContext(conv, ids["trigger"], frozenset({ids["context"], ids["trigger"]}))
    return type("W", (), {"lyle": lyle, "jodie": jodie, "conv": conv, "other": other,
                          "ids": ids, "origin": origin,
                          "attribution": AttributionContext(user_id=lyle),
                          "reg": ToolRegistry([NOTE_SEARCH, NOTE_PROPOSE])})


def propose(world, **kw):
    base = dict(action="add", subject_kind="person", subject="Jodie",
                text="Takes her coffee with oat milk.",
                quotes=["Jodie takes her coffee with oat milk"])
    base.update(kw)
    base = {k: v for k, v in base.items() if v is not None}
    out = world.reg.dispatch("note_propose", base, attribution=world.attribution,
                             origin=world.origin)
    assert out.outcome.value == "ok", out
    return out


def rows(table):
    with db.connection() as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]


def snapshot():
    return {t: rows(t) for t in ("notes", "note_proposals", "approval_log")}


def pending_id():
    return [r["id"] for r in rows("note_proposals") if r["status"] == "pending"][-1]


def add_note(text="Takes oat milk.", **kw):
    return admin.operator_add(kw.pop("kind", "person"), kw.pop("subject", "Jodie"), text,
                              **kw).note_id


# --- the store guard: never migrates, never creates ----------------------------------------


def store_hash():
    h = hashlib.sha256()
    for path in (db.working_path(), db.archive_path()):
        h.update(path.read_bytes())
    return h.hexdigest()


def store_at(version, monkeypatch):
    full = list(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", [m for m in full if m.version <= version])
    db.init_databases()
    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    assert migrations.current_version() == version


@pytest.mark.parametrize("version", (6, 7))
def test_a_store_below_version_8_is_refused_untouched(isolated_data_dir, monkeypatch, capsys,
                                                      version):
    store_at(version, monkeypatch)
    before = store_hash()

    code, out, err = run_cli(capsys, "list")

    assert code == 3 and out == ""
    assert f"schema version {version}" in err and "never migrates" in err
    assert store_hash() == before, "the refused command changed the store"
    assert migrations.current_version() == version, "a command must never migrate"


@pytest.mark.parametrize("argv", [
    ["add", "--kind", "person", "--subject", "J", "--text", "t"], ["revise", "abc", "--text", "t"],
    ["retire", "abc"], ["list"], ["review"], ["review", "abc"], ["approve", "abc"],
    ["edit", "abc", "--text", "t"], ["reject", "abc"], ["misses"], ["check"], ["reindex"]])
def test_every_command_is_guarded_by_the_schema_check(isolated_data_dir, monkeypatch, capsys, argv):
    store_at(7, monkeypatch)
    before = store_hash()
    code, _, err = run_cli(capsys, *argv)
    assert code == 3 and "schema version 7" in err
    assert store_hash() == before


def test_a_missing_store_is_refused_and_nothing_is_created(isolated_data_dir, capsys):
    assert not db.working_path().exists()
    code, _, err = run_cli(capsys, "list")
    assert code == 3 and "never creates one" in err
    assert not db.working_path().exists() and not db.archive_path().exists()
    assert list(Path(isolated_data_dir).rglob("*.db")) == []


def test_neither_module_can_migrate_or_create_a_store():
    """Checked on the code: no call that initialises, migrates or opens a database by path other
    than the one read-only `require_schema` read."""
    banned = {"init_databases", "run_working_migrations", "executescript"}
    for module in (admin, cli):
        tree = ast.parse(inspect.getsource(module))
        called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                  for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert not called & banned, (module.__name__, called & banned)
    tree = ast.parse(inspect.getsource(admin))
    connects = [(fn.name, n) for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                for n in ast.walk(fn) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "connect"
                and getattr(n.func.value, "id", "") == "sqlite3"]
    assert {name for name, _ in connects} == {"require_schema"}, (
        "only the read-only schema check may open a plain sqlite3 connection (foreign keys are "
        "inert on one); everything else uses db.connection / db.transaction")


def test_the_real_data_directory_is_never_the_one_in_use(isolated_data_dir):
    assert str(db.working_path()).startswith(str(isolated_data_dir))


# --- the operator's own notes --------------------------------------------------------------


def test_add_creates_an_active_operator_note_and_logs_it(world):
    done = admin.operator_add("person", " Jodie ", " Takes oat milk. ")
    [note] = rows("notes")
    assert note["id"] == done.note_id and note["status"] == "active"
    assert (note["origin"], note["version"], note["previous_note_id"]) == ("operator", 1, None)
    assert (note["subject"], note["text"]) == ("Jodie", "Takes oat milk.")
    assert note["subject_user_id"] is None and note["last_confirmed_at"] == note["created_at"]
    [entry] = rows("approval_log")
    assert (entry["capability"], entry["decision"], entry["decided_by"]) == (
        "notes", "operator_add", "operator")
    assert (entry["subject_kind"], entry["subject_id"]) == ("note", note["id"])
    assert json.loads(entry["detail"])["note_id"] == note["id"]


def test_add_links_a_household_member_only_when_asked_and_never_the_entity(world):
    admin.operator_add("person", "Jodie", "x", subject_user="jodie")
    admin.operator_add("person", "Jodie too", "y")
    admin.operator_add("person", "jodie", "z")   # subject IS a household name: still no link
    assert [n["subject_user_id"] for n in rows("notes")] == [world.jodie, None, None]
    for bad in ("Nobody", db.ENTITY_USER_NAME):
        with pytest.raises(admin.AdminError, match="not a household member"):
            admin.operator_add("person", "Z", "z", subject_user=bad)
    assert len(rows("notes")) == 3


def test_add_records_evidence_message_ids_in_the_log_and_refuses_unknown_ones(world):
    admin.operator_add("topic", "the backup", "Should run on a schedule.",
                       evidence=[world.ids["context"][:8]])
    assert json.loads(rows("approval_log")[0]["detail"])["evidence_message_ids"] == [
        world.ids["context"]]
    with pytest.raises(admin.AdminError, match="no message"):
        admin.operator_add("topic", "x", "y", evidence=["zzzzzzzz"])
    assert len(rows("notes")) == 1 and len(rows("approval_log")) == 1


@pytest.mark.parametrize("kwargs, message", [
    (dict(kind="self", subject="x", text="y"), "kind must be one of"),
    (dict(kind="person", subject="  ", text="y"), "subject is empty"),
    (dict(kind="person", subject="S" * 61, text="y"), "limit is 60"),
    (dict(kind="person", subject="x", text=" "), "note text is empty"),
    (dict(kind="person", subject="x", text="T" * 651), "limit is 650"),
])
def test_add_refuses_what_a_proposal_would_refuse(world, kwargs, message):
    with pytest.raises(admin.AdminError, match=message):
        admin.operator_add(kwargs["kind"], kwargs["subject"], kwargs["text"])
    assert snapshot() == {"notes": [], "note_proposals": [], "approval_log": []}


def test_revise_supersedes_the_old_note_and_keeps_its_text(world):
    old = add_note("Takes milk.")
    done = admin.operator_revise(old[:8], "Takes oat milk now.")
    notes_by_id = {n["id"]: n for n in rows("notes")}
    assert notes_by_id[old]["status"] == "superseded" and notes_by_id[old]["text"] == "Takes milk."
    new = notes_by_id[done.note_id]
    assert (new["version"], new["previous_note_id"], new["origin"], new["status"]) == (
        2, old, "operator", "active")
    log = [r for r in rows("approval_log") if r["decision"] == "operator_revise"][0]
    assert json.loads(log["detail"]) == {"previous_note_id": old, "note_id": done.note_id,
                                         "before": "Takes milk.", "after": "Takes oat milk now."}


def test_revise_keeps_the_link_unless_told_otherwise(world):
    old = add_note("a", subject_user="Jodie")
    new = admin.operator_revise(old[:8], "b").note_id
    assert {n["id"]: n["subject_user_id"] for n in rows("notes")}[new] == world.jodie


def test_retire_keeps_the_text_and_logs_it(world):
    nid = add_note("Takes oat milk.")
    admin.operator_retire(nid[:8])
    [note] = rows("notes")
    assert (note["status"], note["text"]) == ("retired", "Takes oat milk.") and note["retired_at"]
    assert rows("approval_log")[-1]["decision"] == "operator_retire"


def test_only_an_active_note_can_be_revised_or_retired(world):
    nid = add_note("x")
    admin.operator_retire(nid[:8])
    attempts = (lambda: admin.operator_revise(nid[:8], "y"), lambda: admin.operator_retire(nid[:8]))
    for attempt in attempts:
        with pytest.raises(admin.AdminError, match="not active"):
            attempt()
    with pytest.raises(admin.AdminError, match="no note has an id"):
        admin.operator_retire("ffffffff")


def test_an_ambiguous_prefix_is_refused(world):
    with db.transaction() as conn:
        for i in (1, 2):
            conn.execute(
                "INSERT INTO notes (id, subject_kind, subject, text, status, version, origin, "
                "created_at, last_confirmed_at) VALUES (?, 'topic', 's', 't', 'active', 1, "
                "'operator', 'x', 'x')", (f"abcdef01-0000-4000-8000-00000000000{i}",))
    with pytest.raises(admin.AdminError, match="matches 2 notes"):
        admin.operator_retire("abcdef01")


def test_the_index_stays_consistent_through_every_operator_change(world):
    a = add_note("first")
    b = add_note("second", subject="Lyle")
    admin.operator_revise(a[:8], "first, revised")
    admin.operator_retire(b[:8])
    assert admin.check_index().consistent


# --- deciding a proposal -------------------------------------------------------------------


def test_approve_an_add_creates_the_note_and_flips_the_proposal_and_logs_in_one_step(world):
    propose(world)
    pid = pending_id()
    result = admin.decide(pid[:8], "approved")

    [note] = rows("notes")
    [proposal] = rows("note_proposals")
    assert result.outcome == "approved" and result.note_id == note["id"]
    assert (note["origin"], note["status"], note["version"], note["text"]) == (
        "entity", "active", 1, "Takes her coffee with oat milk.")
    assert note["subject_user_id"] is None, "a household link must never be set silently"
    assert (proposal["status"], proposal["resulting_note_id"]) == ("approved", note["id"])
    assert proposal["decided_at"]
    [entry] = rows("approval_log")
    assert (entry["decision"], entry["subject_kind"], entry["subject_id"]) == (
        "approved", "note_proposal", pid)
    assert json.loads(entry["detail"])["note_id"] == note["id"]


def test_approve_with_subject_user_links_and_an_unknown_user_changes_nothing(world):
    propose(world)
    before = snapshot()
    for bad in ("Nobody", db.ENTITY_USER_NAME):
        with pytest.raises(admin.AdminError, match="not a household member"):
            admin.decide(pending_id(), "approved", subject_user=bad)
    assert snapshot() == before
    admin.decide(pending_id(), "approved", subject_user="JODIE")
    assert rows("notes")[0]["subject_user_id"] == world.jodie


def test_a_household_name_in_the_subject_is_suggested_and_never_applied(world, capsys):
    propose(world)
    pid = pending_id()
    result = admin.decide(pid, "approved")
    assert result.suggestion == "Jodie" and rows("notes")[0]["subject_user_id"] is None

    propose(world, subject="the market stall")
    assert admin.decide(pending_id(), "approved").suggestion is None


def test_the_suggestion_is_printed_by_review_and_after_approval(world, capsys):
    propose(world)
    code, out, _ = run_cli(capsys, "review", pending_id()[:8])
    assert "household member Jodie" in out and "--subject-user Jodie" in out
    code, out, _ = run_cli(capsys, "approve", pending_id()[:8])
    assert code == 0 and "no link was set" in out and "--subject-user Jodie" in out


def test_edit_applies_the_reviewers_text_and_keeps_both_in_the_log(world):
    propose(world)
    pid = pending_id()
    admin.decide(pid, "edited", text="Takes oat milk in her coffee.")

    [note] = rows("notes")
    [proposal] = rows("note_proposals")
    assert note["text"] == "Takes oat milk in her coffee."
    assert proposal["status"] == "edited" and proposal["text"] == "Takes her coffee with oat milk."
    detail = json.loads(rows("approval_log")[0]["detail"])
    assert (detail["before"], detail["after"]) == ("Takes her coffee with oat milk.",
                                                   "Takes oat milk in her coffee.")
    assert rows("approval_log")[0]["decision"] == "edited"


def test_an_edit_needs_text_within_the_cap(world):
    propose(world)
    before = snapshot()
    for text in (None, "  ", "T" * 651):
        with pytest.raises(admin.AdminError):
            admin.decide(pending_id(), "edited", text=text)
    assert snapshot() == before


def test_reject_changes_no_note_and_keeps_the_reason(world):
    propose(world)
    admin.decide(pending_id(), "rejected", reason="not what she said")
    assert rows("notes") == []
    assert rows("note_proposals")[0]["status"] == "rejected"
    assert rows("note_proposals")[0]["resulting_note_id"] is None
    entry = rows("approval_log")[0]
    assert entry["decision"] == "rejected"
    assert json.loads(entry["detail"]) == {"reason": "not what she said"}


def test_approve_a_revise_supersedes_the_target_and_carries_its_link(world):
    old = add_note("Takes milk.", subject_user="Jodie")
    propose(world, action="revise", note_id=old[:8], text="Takes oat milk.")
    result = admin.decide(pending_id(), "approved")
    by_id = {n["id"]: n for n in rows("notes")}
    assert by_id[old]["status"] == "superseded"
    new = by_id[result.note_id]
    assert (new["version"], new["previous_note_id"], new["origin"]) == (2, old, "entity")
    assert new["subject_user_id"] == world.jodie, "an existing link carries forward"
    assert rows("note_proposals")[0]["resulting_note_id"] == result.note_id


def test_approve_a_retire_retires_the_target_and_names_it_as_the_result(world):
    old = add_note("Takes milk.")
    propose(world, action="retire", note_id=old[:8], text=None)
    result = admin.decide(pending_id(), "approved")
    assert result.note_id == old
    assert rows("notes")[0]["status"] == "retired"
    assert rows("note_proposals")[0]["resulting_note_id"] == old


@pytest.mark.parametrize("decision, text", [("approved", None), ("edited", "other text")])
@pytest.mark.parametrize("action", ("revise", "retire"))
def test_a_stale_target_is_refused_at_approval_and_recorded_as_rejected(
        world, capsys, action, decision, text):
    old = add_note("Takes milk.")
    propose(world, action=action, note_id=old[:8], text="New text." if action == "revise" else None)
    admin.operator_retire(old[:8])            # the note changes after the proposal was made
    before_notes = rows("notes")

    code, out, _ = run_cli(capsys, "approve" if decision == "approved" else "edit",
                           pending_id()[:8], *(["--text", text] if text else []))

    assert code == 1 and "the note changed since this was proposed" in out
    assert rows("notes") == before_notes, "a stale proposal changed a note"
    [proposal] = rows("note_proposals")
    assert proposal["status"] == "rejected" and proposal["resulting_note_id"] is None
    entry = [r for r in rows("approval_log") if r["decision"] == "rejected"][0]
    detail = json.loads(entry["detail"])
    assert detail["reason"] == admin.STALE_REASON and detail["target_note_id"] == old
    assert detail["target_status"] == "retired"


def test_a_proposal_is_decided_once(world):
    propose(world)
    pid = pending_id()
    admin.decide(pid, "approved")
    before = snapshot()
    for decision in ("approved", "rejected", "edited"):
        with pytest.raises(admin.AdminError, match="already approved"):
            admin.decide(pid, decision, text="x")
    assert snapshot() == before


def test_the_flip_refuses_a_proposal_that_is_no_longer_pending(world):
    propose(world)
    pid = pending_id()
    admin.decide(pid, "rejected")
    with db.transaction() as conn, pytest.raises(admin.AdminError, match="already decided"):
        admin._flip(conn, pid, "approved", None)


def test_unknown_and_ambiguous_proposal_ids_are_refused(world):
    with pytest.raises(admin.AdminError, match="no proposal has an id"):
        admin.decide("ffffffff", "approved")
    with pytest.raises(admin.AdminError, match="give a proposal id"):
        admin.decide("", "approved")


# --- one transaction: a failed log row changes nothing -------------------------------------


def _boom(*a, **k):
    raise sqlite3.OperationalError("disk I/O error (injected)")


@pytest.mark.parametrize("what", ["add", "revise", "retire"])
def test_a_failed_log_row_leaves_the_operator_change_undone(world, monkeypatch, what):
    old = add_note("Takes milk.")
    before = snapshot()
    monkeypatch.setattr(db, "record_approval", _boom)
    call = {"add": lambda: admin.operator_add("person", "Z", "z"),
            "revise": lambda: admin.operator_revise(old[:8], "new"),
            "retire": lambda: admin.operator_retire(old[:8])}[what]
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        call()
    assert snapshot() == before


@pytest.mark.parametrize("action, decision", [
    ("add", "approved"), ("add", "edited"), ("add", "rejected"),
    ("revise", "approved"), ("retire", "approved"), ("revise", "edited")])
def test_a_failed_log_row_leaves_a_decision_undone_nothing_changes(
        world, monkeypatch, action, decision):
    old = add_note("Takes milk.")
    propose(world, action=action, note_id=old[:8] if action != "add" else None,
            text=None if action == "retire" else "Takes oat milk.")
    before = snapshot()
    monkeypatch.setattr(db, "record_approval", _boom)

    with pytest.raises(sqlite3.OperationalError, match="injected"):
        admin.decide(pending_id(), decision, text="changed")

    assert snapshot() == before, "a decision survived a failed log write"
    assert rows("note_proposals")[-1]["status"] == "pending"
    assert admin.check_index().consistent


def test_a_failed_log_row_on_a_stale_rejection_leaves_the_proposal_pending(world, monkeypatch):
    old = add_note("Takes milk.")
    propose(world, action="retire", note_id=old[:8], text=None)
    admin.operator_retire(old[:8])
    before = snapshot()
    monkeypatch.setattr(db, "record_approval", _boom)
    with pytest.raises(sqlite3.OperationalError):
        admin.decide(pending_id(), "approved")
    assert snapshot() == before and rows("note_proposals")[0]["status"] == "pending"


def test_a_failed_flip_after_the_note_was_written_undoes_the_note(world, monkeypatch):
    propose(world)
    before = snapshot()
    monkeypatch.setattr(admin, "_flip", _boom)
    with pytest.raises(sqlite3.OperationalError):
        admin.decide(pending_id(), "approved")
    assert snapshot() == before and rows("notes") == []


def test_every_write_in_the_admin_module_carries_the_retry():
    undecorated = []
    for name, fn in inspect.getmembers(admin, inspect.isfunction):
        if fn.__module__ != admin.__name__ or name.startswith("_"):
            continue
        if "with db.transaction()" in inspect.getsource(fn) and not hasattr(fn, "__wrapped__"):
            undecorated.append(name)
    assert undecorated == []


# --- the review output ---------------------------------------------------------------------


def seed_review(world, *, untrusted='["web_fetch"]', flagged=True, reply_claims_saved=True):
    """A proposal with evidence from BOTH tiers, a gate flag, an untrusted context (or NULL), and
    the entity's own reply to the turn, claiming the note is saved."""
    import types

    if flagged:
        classifier.classify = lambda p: (
            "CONTRADICTS-SELF\n- Takes her coffee with oat milk. | nothing runs between replies")
    out = propose(world, quotes=["Jodie takes her coffee with oat milk",
                                 "My grandmother kept her starter above the stove"],
                  text="Takes her coffee with oat milk.")
    pid = pending_id()
    if untrusted is not None:
        notes.set_untrusted_context(out.call_id, json.loads(untrusted))
    # an earlier assistant message in the same conversation that is NOT the reply to this turn
    db.save_message(world.conv, world.lyle, "assistant", "An earlier answer, unrelated.",
                    tool_trace=json.dumps([{"iteration": 1, "tool": "web_search",
                                            "call_id": "x" * 32, "outcome": "ok",
                                            # mentions this call's id without being its turn
                                            "value": f"see call {out.call_id}"}]),
                    timestamp="2026-10-02T14:00:30+00:00")
    reply = db.save_message(
        world.conv, world.lyle, "assistant",
        "I've saved that note about Jodie." if reply_claims_saved else "Proposed for review.",
        tool_trace=json.dumps([{"iteration": 1, "tool": "note_propose", "call_id": out.call_id,
                                "outcome": "ok", "value": texts.PENDING_ADD}]),
        timestamp="2026-10-02T14:01:20+00:00")
    db.set_message_integrity_check(reply, json.dumps({"status": "clean", "findings": []}))
    return types.SimpleNamespace(pid=pid, call_id=out.call_id, reply=reply)


def review(capsys, pid):
    code, out, err = run_cli(capsys, "review", pid[:8])
    assert code == 0, err
    return out


def test_the_review_prints_origin_evidence_both_tiers_gate_and_the_entitys_reply(world, capsys):
    s = seed_review(world)
    out = review(capsys, s.pid)

    assert f"PROPOSAL {s.pid}" in out
    # origin: conversation, triggering message verbatim, call id
    assert world.conv in out and "belongs to Lyle" in out
    assert world.ids["trigger"] in out and "Please make a note of that for her." in out
    assert f"call id: {s.call_id}" in out
    # evidence: quote verbatim, the speaker's NAME, tier, duplicate count, the message itself
    assert "quote: 'Jodie takes her coffee with oat milk'" in out
    assert "speaker: Lyle (role user)    tier: context    identical messages: 1" in out
    assert "quote: 'My grandmother kept her starter above the stove'" in out
    assert "speaker: Jodie (role user)    tier: store    identical messages: 1" in out
    assert "Jodie takes her coffee with oat milk, never dairy." in out
    assert "found only outside this turn's context" in out
    # proposed text and the gate, stated as a noisy aid
    assert "text: Takes her coffee with oat milk." in out
    assert "a noisy aid, NOT a control" in out
    assert "status: flagged" in out and "nothing runs between replies" in out
    # the entity's own reply, found by call id (not the earlier unrelated one), with its verdict
    assert "I've saved that note about Jodie." in out
    assert "An earlier answer, unrelated." not in out
    assert "the gate's verdict on that reply:" in out and "status: clean" in out
    assert "Nothing is saved until you approve it." in out


def test_the_speaker_is_a_name_not_only_a_role(world, capsys):
    s = seed_review(world)
    out = review(capsys, s.pid)
    assert "Lyle (role user)" in out and "Jodie (role user)" in out


def test_null_none_and_a_list_are_three_different_statements_never_merged(world, capsys):
    null = untrusted_text(world, capsys, None)
    none = untrusted_text(world, capsys, "[]")
    flagged = untrusted_text(world, capsys, '["web_fetch", "moltbook_read_post"]')

    assert "not recorded" in null and "NOT a statement that none ran" in null
    assert "none (recorded" in none and "not recorded" not in none
    assert "WARNING" in flagged and "web_fetch, moltbook_read_post" in flagged
    assert "none" not in flagged.lower().replace("nothing", "") and "not recorded" not in flagged
    for a, b in ((null, none), (null, flagged), (none, flagged)):
        assert a != b


def untrusted_text(world, capsys, raw):
    with db.transaction() as conn:
        conn.execute("DELETE FROM note_proposals WHERE status = 'pending'")
    s = seed_review(world, untrusted=raw)
    out = review(capsys, s.pid)
    start = out.index("UNTRUSTED CONTEXT")
    return out[start:out.index("ORIGIN")]


def test_the_untrusted_context_is_printed_before_everything_else(world, capsys):
    out = review(capsys, seed_review(world).pid)
    assert out.index("UNTRUSTED CONTEXT") < out.index("ORIGIN") < out.index("PROPOSED") < out.index(
        "EVIDENCE")


def test_a_reply_that_was_not_found_is_said_plainly(world, capsys):
    propose(world)
    out = review(capsys, pending_id())
    assert "no reply found whose trace contains this call" in out


def test_a_clean_unavailable_and_missing_verdict_are_distinguished(world, capsys):
    s = seed_review(world, flagged=False)
    with db.transaction() as conn:
        conn.execute("UPDATE note_proposals SET integrity_check = NULL WHERE id = ?", (s.pid,))
    assert "no verdict recorded (this is not 'clean')" in review(capsys, s.pid)
    with db.transaction() as conn:
        conn.execute("UPDATE note_proposals SET integrity_check = ? WHERE id = ?",
                     (json.dumps({"status": "unavailable", "semantic_error": "down"}), s.pid))
    out = review(capsys, s.pid)
    assert "UNCHECKED, not clean" in out and "status: unavailable" in out


def test_a_revise_review_shows_the_target_the_diff_and_a_stale_warning(world, capsys):
    old = add_note("Takes milk.")
    propose(world, action="revise", note_id=old[:8], text="Takes oat milk.")
    out = review(capsys, pending_id())
    assert f"target note: {old}" in out and "the note now says: Takes milk." in out
    assert "-Takes milk." in out and "+Takes oat milk." in out
    assert "the note changed since this was proposed" not in out

    admin.operator_retire(old[:8])
    out = review(capsys, pending_id())
    assert "[RETIRED]" in out and "the note changed since this was proposed" in out


def test_review_without_an_id_lists_pending_proposals_with_their_flags(world, capsys):
    s = seed_review(world, untrusted='["web_fetch"]')
    code, out, _ = run_cli(capsys, "review")
    assert code == 0 and "1 pending proposal(s)" in out and s.pid[:8] in out
    assert "UNTRUSTED CONTEXT: web_fetch" in out
    code, out, _ = run_cli(capsys, "approve", s.pid[:8])
    assert run_cli(capsys, "review")[1].strip() == "No pending proposals."


def test_a_decided_proposal_says_decisions_are_final(world, capsys):
    s = seed_review(world)
    admin.decide(s.pid, "rejected")
    assert "this proposal is rejected; decisions are final" in review(capsys, s.pid)
    assert "decide:" not in review(capsys, s.pid)


# --- misses, check, reindex, list ----------------------------------------------------------


def run_real_turn(world, monkeypatch, rounds, tools):
    actor = Actor(user_id=world.lyle, name="Lyle", role=Role.ADMIN)
    monkeypatch.setattr(turn.retrieval, "search", lambda q: None)
    sent = []

    def fake(messages, model=None, options=None, tools=None, timeout=None):
        sent.append(1)
        if tools is not None and len(sent) <= len(rounds):
            return {"message": {"role": "assistant", "content": "", "tool_calls": rounds[
                len(sent) - 1]}, "done_reason": "stop"}
        return {"message": {"role": "assistant", "content": "Done."}, "done_reason": "stop"}

    monkeypatch.setattr(loop.ollama, "chat", fake)
    conv = db.start_conversation(world.lyle)
    return conv, turn.handle_user_message(actor, "Look it up.", conv, situation="",
                                          registry=ToolRegistry(tools))


def call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


def test_misses_lists_searches_that_found_nothing_newest_first(world, monkeypatch, capsys):
    add_note("Takes oat milk.")
    run_real_turn(world, monkeypatch, [[call("note_search", query="pottery kilns")]],
                  [NOTE_SEARCH])
    run_real_turn(world, monkeypatch, [[call("note_search", query="oat milk")]], [NOTE_SEARCH])
    run_real_turn(world, monkeypatch, [[call("note_search", query="espresso machine")]],
                  [NOTE_SEARCH])
    run_real_turn(world, monkeypatch, [[call("note_search", query="   ")]], [NOTE_SEARCH])

    code, out, _ = run_cli(capsys, "misses")

    assert code == 0
    lines = [ln for ln in out.splitlines() if "query:" in ln]
    assert [ln.split("query: ")[1] for ln in lines] == ["'espresso machine'", "'pottery kilns'"]
    assert all("Lyle's turn" in ln for ln in lines), "whose turn is shown"
    assert "oat milk" not in out, "a search that found a note is not a miss"
    assert "'   '" not in out, "a search that failed (empty query) is not a miss"
    assert "visible here, not explained" in out


def test_the_misses_report_and_the_tool_share_one_sentence():
    assert "note_texts.NO_MATCH" in inspect.getsource(admin.misses)
    assert inspect.getsource(admin.misses).count("NO_MATCH") >= 1
    from program.tools import note_search
    assert note_search.search_notes.__globals__["texts"] is texts


def test_a_stored_trace_carries_each_tools_returned_value_the_misses_report_rests_on(
        world, monkeypatch):
    """The plan's real-trace pin: through a real turn and the real store, `tool_trace` holds what
    each tool returned. A successful search's value equals its text; a failed call carries an error
    and no value; a result longer than `agent.max_tool_result_chars` is stored untruncated."""
    big = "B" * (config.agent_max_tool_result_chars() + 1000)
    tools = [NOTE_SEARCH,
             Tool(name="probe_fail", description="TEST-ONLY.", handler=_fail,
                  parameters={"type": "object", "properties": {}, "required": []}),
             Tool(name="probe_big", description="TEST-ONLY.", handler=lambda: big,
                  parameters={"type": "object", "properties": {}, "required": []})]
    conv, outcome = run_real_turn(
        world, monkeypatch,
        [[call("note_search", query="pottery kilns"), call("probe_fail"), call("probe_big")]],
        tools)

    stored = json.loads(next(r["tool_trace"] for r in db.get_conversation_messages(conv)
                             if r["tool_trace"]))
    by_tool = {e["tool"]: e for e in stored}
    assert by_tool["note_search"]["value"] == texts.NO_MATCH and by_tool["note_search"][
        "outcome"] == "ok"
    assert by_tool["probe_fail"]["value"] is None and "failed on purpose" in by_tool[
        "probe_fail"]["error"]
    assert by_tool["probe_big"]["value"] == big and len(big) > config.agent_max_tool_result_chars()


def _fail():
    raise RuntimeError("failed on purpose")


def test_check_is_clean_then_detects_drift_and_reindex_repairs_it_without_touching_a_note(
        world, capsys):
    a = add_note("first")
    add_note("second", subject="Lyle")
    admin.operator_retire(a[:8])
    code, out, _ = run_cli(capsys, "check")
    assert code == 0 and "consistent" in out and "FTS5 integrity check: ok" in out

    with db.transaction() as conn:   # drift: take active notes out of the index
        conn.execute("INSERT INTO notes_fts(notes_fts, rowid, subject, text) "
                     "SELECT 'delete', rowid, subject, text FROM notes WHERE status = 'active'")
    code, out, _ = run_cli(capsys, "check")
    assert code == 1 and "DRIFT" in out and "active but not indexed" in out

    notes_before = rows("notes")
    code, out, _ = run_cli(capsys, "reindex")
    assert code == 0 and rows("notes") == notes_before
    assert run_cli(capsys, "check")[0] == 0


def test_list_shows_active_notes_by_default_and_everything_with_all(world, capsys):
    a = add_note("kept")
    b = add_note("gone", subject="Lyle")
    admin.operator_retire(b[:8])
    out = run_cli(capsys, "list")[1]
    assert a[:8] in out and b[:8] not in out
    out = run_cli(capsys, "list", "--all")[1]
    assert a[:8] in out and b[:8] in out and "retired" in out


# --- the command line ----------------------------------------------------------------------


def test_help_states_the_guarantees_and_names_every_command(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    for word in ("add", "revise", "retire", "list", "review", "approve", "edit", "reject",
                 "misses", "check", "reindex", "ONE transaction", "never migrates",
                 "schema version 8", "decided once", "never applied", "NOT a control"):
        assert word in text, word


def test_a_bad_command_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["frobnicate"])
    assert exit_info.value.code == 2


def test_the_command_decides_through_the_cli_end_to_end(world, capsys):
    propose(world)
    pid = pending_id()[:8]
    assert run_cli(capsys, "approve", pid, "--subject-user", "Jodie")[0] == 0
    code, out, _ = run_cli(capsys, "list")
    assert "(linked to Jodie)" in out
    code, _, err = run_cli(capsys, "approve", pid)
    assert code == 1 and "already approved" in err
    assert sys.modules["scripts.note"] is cli


def test_the_pending_list_shows_not_recorded_none_and_a_flag_distinctly_in_one_listing(
        world, capsys):
    """A first version evaluated `json.loads` on a NULL while building the line (found by
    printing a seeded sample, not by a test): the listing now covers all three at once."""
    seed_review(world, untrusted=None)
    seed_review(world, untrusted="[]")
    seed_review(world, untrusted='["web_fetch"]')

    code, out, _ = run_cli(capsys, "review")

    assert code == 0 and "3 pending proposal(s)" in out
    lines = [ln for ln in out.splitlines() if "[untrusted" in ln.lower() or "[UNTRUSTED" in ln]
    assert sorted(ln.split("[")[-1].rstrip("]") for ln in lines) == [
        "UNTRUSTED CONTEXT: web_fetch", "untrusted: none", "untrusted: not recorded"]
