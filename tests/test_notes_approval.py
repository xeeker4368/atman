"""The approval toggle, the approval log and auto-apply: Notes piece 6 (N10).

The one change that lets a note exist with no person having read it, so it is held to the strictest
tests in the build: **fail closed**, **read fresh at the moment of decision**, **one transaction**,
and **the tool tells the truth in both modes**. Every test runs on a temporary store.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

from program import config
from program.attribution import AttributionContext
from program.engine import loop, turn
from program.integrity import classifier
from program.memory import db, note_admin, notes
from program.origin import OriginContext
from program.settings import store
from program.settings.permissions import Actor, Role
from program.tools import note_texts as texts
from program.tools import receipts, registry
from program.tools.note_propose import NOTE_PROPOSE
from program.tools.note_search import NOTE_SEARCH
from program.tools.registry import Tool, ToolRegistry
from scripts import note as cli

REPO = pathlib.Path(__file__).resolve().parents[1]
QUOTE = "Jodie takes her coffee with oat milk"
SETTING = "notes.approval_required"


@pytest.fixture
def world(isolated_data_dir, monkeypatch):
    config.reload()
    db.init_databases()
    store.reset_cache()
    monkeypatch.setattr(classifier, "classify", lambda p: "CONSISTENT")
    registry.reset_default_registry()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie")
    conv = db.start_conversation(lyle)
    msg = db.save_message(conv, lyle, "user", "Jodie takes her coffee with oat milk, never dairy.",
                          timestamp="2026-10-02T10:00:00+00:00")
    yield type("W", (), {
        "lyle": lyle, "jodie": jodie, "conv": conv, "msg": msg,
        "origin": OriginContext(conv, msg, frozenset({msg})),
        "attribution": AttributionContext(user_id=lyle),
        "reg": ToolRegistry([NOTE_SEARCH, NOTE_PROPOSE])})
    store.reset_cache()
    registry.reset_default_registry()


def propose(world, **kw):
    args = dict(action="add", subject_kind="person", subject="Jodie",
                text="Takes her coffee with oat milk.", quotes=[QUOTE])
    args.update({k: v for k, v in kw.items() if v is not None})
    if kw.get("text", 1) is None:
        args.pop("text", None)
    return world.reg.dispatch("note_propose", args, attribution=world.attribution,
                              origin=world.origin)


def rows(table):
    with db.connection() as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]


def snapshot():
    return {t: rows(t) for t in ("notes", "note_proposals", "approval_log", "settings")}


def raw_set(value):
    """Write the setting through a SEPARATE plain connection, the way another process would."""
    conn = sqlite3.connect(db.working_path())
    try:
        conn.execute(
            "INSERT INTO settings (key, value, value_type, updated_at, updated_by) "
            "VALUES (?, ?, 'bool', 'x', 'other-process') ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value", (SETTING, value))
        conn.commit()
    finally:
        conn.close()


def one_proposal():
    [p] = rows("note_proposals")
    return p


def add_note(text="Takes milk.", **kw):
    return note_admin.operator_add("person", kw.pop("subject", "Jodie"), text, **kw).note_id


# --- the setting: settings-backed, default required, one writer ----------------------------


def test_the_setting_is_registered_and_defaults_to_required(world):
    assert store.spec_for(SETTING).value_type == "bool"
    assert store.is_settings_backed("notes", "approval_required")
    assert rows("settings") == []
    assert notes.approval_required_now() is True
    result = propose(world)
    assert result.value == texts.PENDING_ADD
    assert rows("notes") == [] and one_proposal()["status"] == "pending"


def test_the_toml_seed_cannot_switch_approval_off(world, monkeypatch):
    """A file edit is not a logged event: with no row the answer is 'required' whatever the seed."""
    real = config.get
    monkeypatch.setattr(config, "get", lambda s, k, d=None: False if (s, k) == (
        "notes", "approval_required") else real(s, k, d))
    assert notes.approval_required_now() is True
    assert propose(world).value == texts.PENDING_ADD


def test_set_and_clear_refuse_the_setting_so_only_the_operator_command_writes_it(world):
    for call in (lambda: store.set(SETTING, False, Actor.operator()),
                 lambda: store.clear(SETTING, Actor.operator())):
        with pytest.raises(store.LoggedWriterOnlyError, match="scripts.note approval"):
            call()
    assert rows("settings") == []


def test_only_note_admin_writes_the_setting_in_transaction():
    callers = []
    for base in ("program", "scripts"):
        for path in (REPO / base).rglob("*.py"):
            tree = ast.parse(path.read_text())
            if any(isinstance(n, ast.Attribute) and n.attr == "write_in_transaction"
                   for n in ast.walk(tree)):
                callers.append(str(path.relative_to(REPO)))
    assert callers == ["program/memory/note_admin.py"]


# --- the toggle: setting and log row in ONE transaction ------------------------------------


def test_the_toggle_writes_the_setting_and_a_log_row(world):
    off = note_admin.set_approval_required(False)
    assert (off.required, off.previous) == (False, True)
    [setting] = rows("settings")
    assert (setting["key"], setting["value"], setting["updated_by"]) == (
        SETTING, "false", "operator")
    [log] = rows("approval_log")
    assert (log["capability"], log["decision"], log["decided_by"], log["subject_id"]) == (
        "notes", "approval_required_off", "operator", SETTING)
    assert json.loads(log["detail"]) == {"before": True, "after": False}

    on = note_admin.set_approval_required(True)
    assert (on.required, on.previous) == (True, False)
    assert rows("approval_log")[-1]["decision"] == "approval_required_on"
    assert notes.approval_required_now() is True


def test_the_toggle_invalidates_this_processs_cache(world):
    assert store.resolve("notes", "approval_required", None) is None        # warms the cache
    note_admin.set_approval_required(False)
    assert store.resolve("notes", "approval_required", None) is False
    note_admin.set_approval_required(True)
    assert store.resolve("notes", "approval_required", None) is True


def test_a_failed_log_row_leaves_the_setting_unchanged(world, monkeypatch):
    before = snapshot()
    monkeypatch.setattr(db, "record_approval",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("injected")))
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        note_admin.set_approval_required(False)
    assert snapshot() == before and notes.approval_required_now() is True


def test_a_failed_setting_write_leaves_no_log_row(world, monkeypatch):
    before = snapshot()
    monkeypatch.setattr(store, "write_in_transaction",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("injected")))
    with pytest.raises(sqlite3.OperationalError):
        note_admin.set_approval_required(False)
    assert snapshot() == before


# --- fail closed ---------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["no row", "garbage value", "empty value", "table missing",
                                 "connection fails"])
def test_an_unreadable_setting_means_approval_is_required(world, monkeypatch, how):
    if how == "garbage value":
        raw_set("maybe")
    elif how == "empty value":
        raw_set("")
    elif how == "table missing":
        conn = sqlite3.connect(db.working_path())
        conn.execute("ALTER TABLE settings RENAME TO settings_gone")
        conn.commit()
        conn.close()
    elif how == "connection fails":
        class Broken:
            def execute(self, *a, **k):
                raise sqlite3.OperationalError("disk I/O error")

        assert notes.approval_required_in(Broken()) is True
        return
    assert notes.approval_required_now() is True
    result = propose(world)
    assert result.value == texts.PENDING_ADD
    assert rows("notes") == [] and one_proposal()["status"] == "pending"


def test_only_an_explicit_false_switches_approval_off(world):
    for value in ("false", "0", "no", "off"):
        raw_set(value)
        assert notes.approval_required_now() is False, value
    for value in ("true", "1", "yes", "on"):
        raw_set(value)
        assert notes.approval_required_now() is True, value


# --- read FRESH: a change another process makes is seen by the next proposal ---------------


def test_the_process_cache_is_stale_and_the_decision_does_not_use_it(world):
    """How the cache is invalidated: only by `store.set` / `store.clear` in the SAME process
    (`store.invalidate`). A write from another process invalidates nothing. So the decision must not
    read through it, and does not: proven in both directions with the cache warm."""
    raw_set("true")
    store.reset_cache()
    assert store.resolve("notes", "approval_required", None) is True        # warm the cache
    raw_set("false")                                                          # "another process"
    assert store.resolve("notes", "approval_required", None) is True, "the cache must be stale here"
    assert propose(world).value == texts.APPLIED_ADD                          # fresh read: off
    assert one_proposal()["status"] == "applied_without_review"


    # the other direction: the cache says OFF, the row says ON
    store.reset_cache()
    raw_set("false")
    assert store.resolve("notes", "approval_required", None) is False     # warm the cache
    raw_set("true")                                                       # "another process"
    assert store.resolve("notes", "approval_required", None) is False, "the cache must be stale"
    assert propose(world, subject="Another").value == texts.PENDING_ADD   # fresh read: on
    assert [p["status"] for p in rows("note_proposals")][-1] == "pending"


def test_a_change_made_by_a_separate_process_is_seen_by_the_next_proposal(world):
    env = {**os.environ, "PYTHONPATH": str(REPO)}

    def operator(*argv):
        done = subprocess.run([sys.executable, "-m", "scripts.note", *argv], cwd=REPO, env=env,
                              capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr
        return done.stdout

    assert propose(world).value == texts.PENDING_ADD                          # cache warm, on
    out = operator("approval", "off")
    assert "OFF" in out
    assert propose(world, subject="Second").value == texts.APPLIED_ADD
    operator("approval", "on")
    assert propose(world, subject="Third").value == texts.PENDING_ADD
    assert [r["decision"] for r in rows("approval_log")][:2] == [
        "approval_required_off", "applied_without_review"][:1] + ["applied_without_review"]
    assert {r["decision"] for r in rows("approval_log")} == {
        "approval_required_off", "approval_required_on", "applied_without_review"}


# --- auto-apply ----------------------------------------------------------------------------


def test_an_applied_add_writes_the_note_flips_the_proposal_and_logs_in_one_step(world):
    note_admin.set_approval_required(False)
    result = propose(world)

    assert result.outcome.value == "ok" and result.value == texts.APPLIED_ADD
    [note] = rows("notes")
    p = one_proposal()
    assert (note["status"], note["origin"], note["version"], note["text"]) == (
        "active", "entity", 1, "Takes her coffee with oat milk.")
    assert note["subject_user_id"] is None, "never linked silently, applied or not"
    assert (p["status"], p["resulting_note_id"]) == ("applied_without_review", note["id"])
    assert p["decided_at"]
    applied = [r for r in rows("approval_log") if r["decision"] == "applied_without_review"]
    assert len(applied) == 1
    assert (applied[0]["decided_by"], applied[0]["subject_id"]) == (notes.AUTO_DECIDER, p["id"])
    assert json.loads(applied[0]["detail"])["note_id"] == note["id"]
    assert note_admin.pending_count() == 0
    assert note_admin.check_index().consistent


def test_an_applied_revise_supersedes_and_carries_the_link(world):
    old = add_note("Takes milk.", subject_user="Jodie")
    note_admin.set_approval_required(False)
    result = propose(world, action="revise", note_id=old[:8], text="Takes oat milk.")
    assert result.value == texts.APPLIED_CHANGE
    by_id = {n["id"]: n for n in rows("notes")}
    assert by_id[old]["status"] == "superseded"
    [new] = [n for n in by_id.values() if n["previous_note_id"] == old]
    assert (new["version"], new["origin"], new["subject_user_id"]) == (2, "entity", world.jodie)
    assert one_proposal()["resulting_note_id"] == new["id"]


def test_an_applied_retire_retires_and_names_the_retired_note(world):
    old = add_note("Takes milk.")
    note_admin.set_approval_required(False)
    result = propose(world, action="retire", note_id=old[:8], text=None)
    assert result.value == texts.APPLIED_RETIRE
    assert {n["id"]: n["status"] for n in rows("notes")}[old] == "retired"
    assert one_proposal()["resulting_note_id"] == old


def test_a_target_that_is_no_longer_active_is_not_applied(world):
    old = add_note("Takes milk.")
    note_admin.operator_retire(old[:8])
    note_admin.set_approval_required(False)
    outcome = notes.record_proposal(
        proposal_id=db.new_id(), action="revise", target_note_id=old, subject_kind="person",
        subject="Jodie", text="x", evidence=[{"message_id": world.msg}],
        conversation_id=world.conv, user_message_id=world.msg, call_id=None, user_id=world.lyle,
        integrity_check=None)
    assert outcome == "pending"
    assert one_proposal()["status"] == "pending"
    assert {n["id"]: n["status"] for n in rows("notes")} == {old: "retired"}


@pytest.mark.parametrize("fails", ["log", "apply", "flip"])
def test_a_failure_anywhere_in_an_auto_apply_leaves_every_table_unchanged(
        world, monkeypatch, fails):
    old = add_note("Takes milk.")
    note_admin.set_approval_required(False)
    before = snapshot()
    boom = sqlite3.OperationalError("injected")
    if fails == "log":
        monkeypatch.setattr(db, "record_approval", lambda *a, **k: (_ for _ in ()).throw(boom))
    elif fails == "apply":
        monkeypatch.setattr(notes, "apply_change", lambda *a, **k: (_ for _ in ()).throw(boom))
    else:
        monkeypatch.setattr(db, "now_iso", _fail_on_second_call())
    for kw in (dict(), dict(action="revise", note_id=old[:8], text="New.")):
        result = propose(world, **kw)
        assert result.outcome.value == "tool_error"
    assert snapshot() == before, "an auto-apply survived a failed step"
    assert note_admin.check_index().consistent


def _fail_on_second_call():
    real = db.now_iso
    calls = {"n": 0}

    def now():
        calls["n"] += 1
        if calls["n"] >= 2:
            raise sqlite3.OperationalError("injected")
        return real()

    return now


def test_the_proposal_is_inserted_pending_first_and_flipped_in_the_same_transaction(world):
    """The schema refuses any other start; this checks the order of statements on one connection."""
    note_admin.set_approval_required(False)
    seen = []

    class Spy:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *a):
            seen.append(" ".join(sql.split())[:48])
            return self.conn.execute(sql, *a)

    real = db.transaction

    class T:
        def __enter__(self):
            self.cm = real()
            return Spy(self.cm.__enter__())

        def __exit__(self, *e):
            return self.cm.__exit__(*e)

    db_tx = db.transaction
    db.transaction = lambda: T()
    try:
        propose(world)
    finally:
        db.transaction = db_tx
    insert = next(i for i, s in enumerate(seen) if s.startswith("INSERT INTO note_proposals"))
    flip = next(i for i, s in enumerate(seen) if s.startswith("UPDATE note_proposals SET status"))
    assert insert < flip


# --- the tool tells the truth in both modes ------------------------------------------------


def test_the_description_is_static_and_makes_no_claim_about_review(world):
    description = NOTE_PROPOSE.description.lower()
    for word in ("review", "approv", "pending", "a person reviews"):
        assert word not in description
    schema_on = json.dumps(NOTE_PROPOSE.to_ollama_schema())
    note_admin.set_approval_required(False)
    assert json.dumps(NOTE_PROPOSE.to_ollama_schema()) == schema_on
    assert "reviewed" not in NOTE_SEARCH.description.lower()


def test_the_result_text_says_which_mode_happened(world):
    pending = propose(world).value
    note_admin.set_approval_required(False)
    applied = propose(world, subject="Other").value
    assert pending == texts.PENDING_ADD and applied == texts.APPLIED_ADD
    assert "No note exists yet" in pending and "A person will review it" in pending
    assert "No one reviewed it" in applied and "A person will review" not in applied
    for value in (pending, applied):
        assert not any(p["id"] in value for p in rows("note_proposals"))
    assert texts.APPLIED_ADD != texts.APPLIED_CHANGE != texts.APPLIED_RETIRE


def test_an_auto_applied_proposals_receipt_reads_applied_without_review(world):
    note_admin.set_approval_required(False)
    entry = propose(world).to_trace_entry()
    [r] = receipts.for_trace([entry])
    assert (r.outcome, r.status, r.text) == (
        "applied_without_review", "applied_without_review", "Applied without review.")
    assert "reviewed" not in r.text.lower().replace("without review", "")


# --- note_search never calls an unreviewed note confirmed ----------------------------------


def _age(note_id, days):
    when = "2026-09-20T00:00:00+00:00"
    with db.transaction() as conn:
        conn.execute("UPDATE notes SET last_confirmed_at = ?, created_at = ? WHERE id = ?",
                     (when, when, note_id))


def search(world, query):
    return world.reg.dispatch("note_search", {"query": query}).value


def test_a_note_applied_without_review_is_framed_as_such_never_as_confirmed(world):
    note_admin.set_approval_required(False)
    propose(world, subject="Kettle", text="The kettle is descaled on Sundays.")
    [nid] = [n["id"] for n in rows("notes")]
    _age(nid, 12)
    out = search(world, "kettle descaled")
    assert "added without review" in out and "days ago" in out
    assert "confirmed" not in out


def test_a_revise_applied_without_review_says_changed_without_review(world):
    old = add_note("The kettle is descaled monthly.", subject="Kettle")
    note_admin.set_approval_required(False)
    propose(world, action="revise", note_id=old[:8], text="The kettle is descaled weekly.")
    out = search(world, "kettle descaled weekly")
    assert "changed without review" in out and "confirmed" not in out


def test_reviewed_and_operator_notes_still_say_confirmed(world):
    add_note("The grinder is burr.", subject="Grinder")                       # operator
    propose(world, subject="Mug", text="The blue mug is Jodie's.")             # pending
    note_admin.decide(one_proposal()["id"], "approved")                        # a person approved
    assert "confirmed" in search(world, "grinder burr")
    assert "confirmed" in search(world, "blue mug") and "without review" not in search(
        world, "blue mug")


def test_a_full_page_of_applied_notes_renders_under_the_cap(world):
    longest = config.notes_max_text_chars()
    when = "2026-01-01T00:00:00+00:00"
    subject = "S" * config.notes_max_subject_chars()
    with db.transaction() as conn:
        for i in range(config.notes_max_results() + 2):
            base, new, pid = db.new_id(), db.new_id(), db.new_id()
            for nid, status, version in ((base, "active", 1), (new, "active", 2)):
                conn.execute(
                    "INSERT INTO notes (id, subject_kind, subject, text, status, version, origin, "
                    "created_at, last_confirmed_at) VALUES (?, 'project', ?, ?, ?, ?, 'entity', "
                    "?, ?)", (nid, subject, "T" * longest if nid == new else "base", status,
                              version, when, when))
            conn.execute("UPDATE notes SET status = 'superseded' WHERE id = ?", (base,))
            conn.execute(
                "INSERT INTO note_proposals (id, action, target_note_id, subject_kind, subject, "
                "text, evidence, conversation_id, user_message_id, user_id, status, created_at) "
                "VALUES (?, 'revise', ?, 'project', 's', 't', '[{}]', ?, ?, ?, 'pending', 'x')",
                (pid, base, world.conv, world.msg, world.lyle))
            conn.execute("UPDATE note_proposals SET status = 'applied_without_review', "
                         "decided_at = 'x', resulting_note_id = ? WHERE id = ?", (new, pid))
    out = search(world, subject)
    assert out.count("[note ") == config.notes_max_results()
    assert "changed without review" in out
    assert len(out) <= config.agent_max_tool_result_chars()


# --- pending stays pending; the command says so --------------------------------------------


def test_pending_proposals_stay_pending_when_approval_is_switched_off(world, capsys):
    propose(world)
    code = cli.main(["approval", "off"])
    out = capsys.readouterr().out
    assert code == 0 and "OFF" in out
    assert "1 pending proposal(s) stay pending" in out and "does not apply them" in out
    assert one_proposal()["status"] == "pending" and rows("notes") == []
    cli.main(["approval", "on"])
    out = capsys.readouterr().out
    assert "REQUIRED" in out and "stay pending" in out
    assert one_proposal()["status"] == "pending"


def test_approval_status_and_the_review_banner(world, capsys):
    cli.main(["approval", "status"])
    assert "REQUIRED" in capsys.readouterr().out
    note_admin.set_approval_required(False)
    propose(world)                                    # applied, so nothing pending
    cli.main(["approval", "status"])
    assert "OFF" in capsys.readouterr().out
    cli.main(["review"])
    assert "approval is OFF" in capsys.readouterr().out


def test_a_human_decision_always_works_in_both_modes(world):
    propose(world)
    note_admin.set_approval_required(False)
    note_admin.decide(one_proposal()["id"], "approved")
    assert [n["origin"] for n in rows("notes")] == ["entity"]
    assert rows("note_proposals")[0]["status"] == "approved"


def test_the_approval_command_refuses_a_store_below_version_8(isolated_data_dir, monkeypatch,
                                                              capsys):
    from program.memory import migrations
    full = list(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", [m for m in full if m.version <= 7])
    db.init_databases()
    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    assert cli.main(["approval", "off"]) == 3
    assert "schema version 7" in capsys.readouterr().err
    assert migrations.current_version() == 7


# --- the untrusted context of an applied proposal ------------------------------------------


def test_an_applied_proposals_untrusted_context_is_appended_to_the_log_once(world):
    note_admin.set_approval_required(False)
    out = propose(world)
    call_id = out.call_id
    assert one_proposal()["untrusted_context"] is None
    assert notes.set_untrusted_context(call_id, ["web_fetch"]) == 1
    assert notes.set_untrusted_context(call_id, ["web_fetch"]) == 0, "once per proposal"
    assert one_proposal()["untrusted_context"] is None, "a decided proposal is frozen"
    [row] = [r for r in rows("approval_log") if r["decision"] == "untrusted_context_recorded"]
    assert json.loads(row["detail"]) == {"tools": ["web_fetch"]}
    assert (row["subject_id"], row["decided_by"]) == (one_proposal()["id"], "system")


def test_a_pending_proposals_context_still_goes_on_its_row(world):
    out = propose(world)
    assert notes.set_untrusted_context(out.call_id, ["web_fetch"]) == 1
    assert json.loads(one_proposal()["untrusted_context"]) == ["web_fetch"]
    assert [r for r in rows("approval_log")] == []


def test_a_real_turn_after_an_untrusted_read_is_applied_and_its_flag_recorded_afterwards(
        world, monkeypatch):
    note_admin.set_approval_required(False)
    web = Tool(name="probe_web", description="TEST-ONLY: outside text.",
               parameters={"type": "object", "properties": {}, "required": []},
               handler=lambda: "text written outside the household", untrusted_output=True)
    reg = ToolRegistry([web, NOTE_PROPOSE])
    actor = Actor(user_id=world.lyle, name="Lyle", role=Role.ADMIN)
    monkeypatch.setattr(turn.retrieval, "search", lambda q: None)
    sent = []

    def fake(messages, model=None, options=None, tools=None, timeout=None):
        sent.append(1)
        if len(sent) == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "probe_web", "arguments": {}}},
                {"function": {"name": "note_propose", "arguments": dict(
                    action="add", subject_kind="person", subject="Jodie",
                    text="Takes oat milk.", quotes=[QUOTE])}}]}, "done_reason": "stop"}
        return {"message": {"role": "assistant", "content": "Done."}, "done_reason": "stop"}

    monkeypatch.setattr(loop.ollama, "chat", fake)
    conv = db.start_conversation(world.lyle)
    turn.handle_user_message(actor, "Jodie takes her coffee with oat milk, never dairy.", conv,
                             situation="", registry=reg)

    [p] = [r for r in rows("note_proposals")]
    assert p["status"] == "applied_without_review", "applied despite the untrusted read"
    assert p["untrusted_context"] is None
    [row] = [r for r in rows("approval_log") if r["decision"] == "untrusted_context_recorded"]
    assert json.loads(row["detail"]) == {"tools": ["probe_web"]}


# --- review --applied ----------------------------------------------------------------------


def _applied_with_reply(world, claim="I've saved that note about Jodie."):
    note_admin.set_approval_required(False)
    out = propose(world)
    db.save_message(world.conv, world.lyle, "assistant", claim,
                    tool_trace=json.dumps([{"iteration": 1, "tool": "note_propose",
                                            "call_id": out.call_id, "outcome": "ok"}]),
                    timestamp="2026-10-02T10:01:20+00:00")
    return out


def test_review_applied_shows_the_same_view_a_pending_proposal_gets(world, capsys):
    out = _applied_with_reply(world)
    notes.set_untrusted_context(out.call_id, ["web_fetch"])
    assert cli.main(["review", "--applied"]) == 0
    text = capsys.readouterr().out
    assert "1 proposal(s) applied WITHOUT review" in text
    assert "UNTRUSTED CONTEXT" in text and "WARNING" in text and "web_fetch" in text
    assert world.conv in text and "call id:" in text
    assert "speaker: Lyle (role user)    tier: context" in text
    assert "IDENTITY GATE" in text and "noisy aid" in text
    assert "I've saved that note about Jodie." in text
    assert "RESULT" in text and "[active]" in text and "origin entity" in text
    assert "applied without review" in text
    assert "This proposal is pending" not in text


def test_review_applied_prints_not_recorded_and_none_distinctly(world, capsys):
    out_a = _applied_with_reply(world)                       # never recorded
    out_b = propose(world, subject="Second")
    notes.set_untrusted_context(out_b.call_id, [])           # recorded: none
    cli.main(["review", "--applied"])
    text = capsys.readouterr().out
    assert "not recorded (the turn did not store it" in text
    assert "none (recorded" in text
    assert out_a.call_id != out_b.call_id


def test_review_applied_lists_only_applied_proposals_and_changes_nothing(world, capsys):
    propose(world, subject="Rejected one")                   # approval on
    note_admin.decide(rows("note_proposals")[0]["id"], "rejected")
    propose(world, subject="Still pending")                  # approval on: stays pending
    _applied_with_reply(world)
    before = snapshot()
    cli.main(["review", "--applied"])
    text = capsys.readouterr().out
    assert text.count("PROPOSAL ") == 1
    assert snapshot() == before


def test_review_applied_with_nothing_applied_says_so(world, capsys):
    cli.main(["review", "--applied"])
    assert "No proposals have been applied without review." in capsys.readouterr().out


# --- the two-axis table: enabled x approval_required, one test per cell --------------------


def _offered(monkeypatch, enabled):
    monkeypatch.setenv("ANAM_NOTES_ENABLED", "true" if enabled else "false")
    config.reload()
    registry.reset_default_registry()
    return set(registry.default_registry().names)


@pytest.mark.parametrize("approval_required", [True, False])
def test_cell_enabled_off_nothing_is_offered_whatever_approval_says(
        world, monkeypatch, approval_required):
    """enabled off: the tools do not exist for the model, in either approval state."""
    note_admin.set_approval_required(approval_required)
    names = _offered(monkeypatch, enabled=False)
    assert not {"note_search", "note_propose"} & names
    # a person still has every operator control
    note_admin.operator_add("person", "Jodie", "Takes oat milk.")
    assert [n["origin"] for n in rows("notes")] == ["operator"]


def test_cell_enabled_on_approval_required_a_proposal_waits(world, monkeypatch):
    names = _offered(monkeypatch, enabled=True)
    assert {"note_search", "note_propose"} <= names
    result = propose(world)
    assert result.value == texts.PENDING_ADD
    assert rows("notes") == [] and one_proposal()["status"] == "pending"
    note_admin.decide(one_proposal()["id"], "approved")          # a human decision works
    assert len(rows("notes")) == 1


def test_cell_enabled_on_approval_off_a_proposal_is_applied_and_logged(world, monkeypatch):
    note_admin.set_approval_required(False)
    names = _offered(monkeypatch, enabled=True)
    assert {"note_search", "note_propose"} <= names
    result = propose(world)
    assert result.value == texts.APPLIED_ADD
    assert len(rows("notes")) == 1
    assert one_proposal()["status"] == "applied_without_review"
    assert "applied_without_review" in {r["decision"] for r in rows("approval_log")}
    # and a human decision still works on what is pending (nothing), and operator controls too
    note_admin.operator_add("topic", "Backup", "Runs weekly.")
    assert len(rows("notes")) == 2


def test_enabled_stays_bootstrap_and_approval_stays_settings_backed():
    assert not store.is_settings_backed("notes", "enabled")
    assert store.is_settings_backed("notes", "approval_required")
    assert not hasattr(config, "notes_approval_required")


# --- the writer enumeration ----------------------------------------------------------------


def test_every_write_in_the_notes_module_still_carries_the_retry():
    undecorated = []
    for name, fn in inspect.getmembers(notes, inspect.isfunction):
        if fn.__module__ != notes.__name__ or name.startswith("_"):
            continue
        if "db.transaction()" in inspect.getsource(fn) and not hasattr(fn, "__wrapped__"):
            undecorated.append(name)
    assert undecorated == []
