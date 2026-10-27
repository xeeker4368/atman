"""The approval setting and the approval log: Notes piece 6 (N10), as Notes v2 leaves them.

Since Notes v2 (decision #36(c)) every proposal is pending: the auto-apply path is removed and
``approval off`` is refused until the web UI has a Notes page. What stays is held to the same
tests: the setting **fails closed**, is **read fresh**, has **one writer** that writes it and its
log row in **one transaction**. A note applied without review before v2 is still never shown as
confirmed. Every test runs on a temporary store.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import sqlite3

import pytest

from program import config
from program.integrity import classifier
from program.memory import db, note_admin, notes
from program.settings import store
from program.settings.permissions import Actor
from program.tools import registry
from program.tools.note_search import NOTE_SEARCH
from program.tools.registry import ToolRegistry
from scripts import note as cli
from tests.notes_helpers import pending_proposal

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
        "reg": ToolRegistry([NOTE_SEARCH])})
    store.reset_cache()
    registry.reset_default_registry()


def propose(world, *, subject="Jodie", text="Takes her coffee with oat milk.", action="add",
            target=None, untrusted=None):
    """A pending proposal, written the way the note pass writes one."""
    return pending_proposal(world.conv, world.lyle, action=action, subject=subject, text=text,
                            line=text[:100] if text else None, quote=QUOTE,
                            context_ids=frozenset({world.msg}), target_note_id=target,
                            untrusted=untrusted)


def apply_without_review(proposal_id):
    """What auto-apply used to do, by direct SQL: no code path does it since Notes v2, but a row
    in this state must still render as unreviewed (note_search's rendering is unchanged)."""
    with db.transaction() as conn:
        p = conn.execute("SELECT * FROM note_proposals WHERE id = ?", (proposal_id,)).fetchone()
        target = (conn.execute("SELECT * FROM notes WHERE id = ?",
                               (p["target_note_id"],)).fetchone()
                  if p["target_note_id"] else None)
        result, _ = notes.apply_change(
            conn, action=p["action"], kind=p["subject_kind"], subject=p["subject"],
            target=target, text=p["text"], subject_user_id=None, carry_link=True, line=p["line"])
        conn.execute("UPDATE note_proposals SET status = 'applied_without_review', "
                     "decided_at = ?, resulting_note_id = ? WHERE id = ?",
                     (db.now_iso(), result, proposal_id))
    return result


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
    propose(world)
    assert rows("notes") == [] and one_proposal()["status"] == "pending"


def test_the_toml_seed_cannot_switch_approval_off(world, monkeypatch):
    """A file edit is not a logged event: with no row the answer is 'required' whatever the seed."""
    real = config.get
    monkeypatch.setattr(config, "get", lambda s, k, d=None: False if (s, k) == (
        "notes", "approval_required") else real(s, k, d))
    assert notes.approval_required_now() is True


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


# --- the toggle: on writes setting and log row in ONE transaction; off is refused ----------


def test_approval_on_writes_the_setting_and_a_log_row(world):
    on = note_admin.set_approval_required(True)
    assert (on.required, on.previous) == (True, True)
    [setting] = rows("settings")
    assert (setting["key"], setting["value"], setting["updated_by"]) == (
        SETTING, "true", "operator")
    [log] = rows("approval_log")
    assert (log["capability"], log["decision"], log["decided_by"], log["subject_id"]) == (
        "notes", "approval_required_on", "operator", SETTING)
    assert json.loads(log["detail"]) == {"before": True, "after": True}
    assert notes.approval_required_now() is True


def test_approval_on_after_a_stored_off_records_the_change(world):
    raw_set("false")
    on = note_admin.set_approval_required(True)
    assert (on.required, on.previous) == (True, False)
    assert json.loads(rows("approval_log")[-1]["detail"]) == {"before": False, "after": True}


def test_approval_off_is_refused_and_writes_nothing(world):
    before = snapshot()
    with pytest.raises(note_admin.AdminError) as refused:
        note_admin.set_approval_required(False)
    assert str(refused.value) == note_admin.APPROVAL_OFF_REFUSED
    assert "web UI has a Notes page" in str(refused.value)
    assert snapshot() == before
    assert rows("settings") == [] and rows("approval_log") == []


def test_approval_off_through_the_cli_is_refused_with_the_reason(world, capsys):
    before = snapshot()
    assert cli.main(["approval", "off"]) == 1
    assert note_admin.APPROVAL_OFF_REFUSED in capsys.readouterr().err
    assert snapshot() == before
    assert cli.main(["approval", "on"]) == 0
    assert "REQUIRED" in capsys.readouterr().out
    assert [r["decision"] for r in rows("approval_log")] == ["approval_required_on"]


def test_the_toggle_invalidates_this_processs_cache(world):
    raw_set("false")
    store.reset_cache()
    assert store.resolve("notes", "approval_required", None) is False      # warms the cache
    note_admin.set_approval_required(True)
    assert store.resolve("notes", "approval_required", None) is True


def test_a_failed_log_row_leaves_the_setting_unchanged(world, monkeypatch):
    before = snapshot()
    monkeypatch.setattr(db, "record_approval",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("injected")))
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        note_admin.set_approval_required(True)
    assert snapshot() == before


def test_a_failed_setting_write_leaves_no_log_row(world, monkeypatch):
    before = snapshot()
    monkeypatch.setattr(store, "write_in_transaction",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("injected")))
    with pytest.raises(sqlite3.OperationalError):
        note_admin.set_approval_required(True)
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


def test_only_an_explicit_false_reads_as_off(world):
    for value in ("false", "0", "no", "off"):
        raw_set(value)
        assert notes.approval_required_now() is False, value
    for value in ("true", "1", "yes", "on"):
        raw_set(value)
        assert notes.approval_required_now() is True, value


def test_a_stored_off_applies_nothing_every_proposal_waits(world):
    """No path applies a proposal since Notes v2, whatever the stored setting says."""
    raw_set("false")
    propose(world)
    assert rows("notes") == [] and one_proposal()["status"] == "pending"
    assert rows("approval_log") == []


def test_the_read_is_fresh_not_through_the_process_cache(world):
    """The cache is invalidated only by a write in the SAME process; the read must not use it."""
    raw_set("true")
    store.reset_cache()
    assert store.resolve("notes", "approval_required", None) is True        # warm the cache
    raw_set("false")                                                          # "another process"
    assert store.resolve("notes", "approval_required", None) is True, "the cache must be stale"
    assert notes.approval_required_now() is False


# --- note_search never calls an unreviewed note confirmed ----------------------------------


def _age(note_id):
    when = "2026-09-20T00:00:00+00:00"
    with db.transaction() as conn:
        conn.execute("UPDATE notes SET last_confirmed_at = ?, created_at = ? WHERE id = ?",
                     (when, when, note_id))


def search(world, query):
    return world.reg.dispatch("note_search", {"query": query}).value


def test_a_note_applied_without_review_is_framed_as_such_never_as_confirmed(world):
    nid = apply_without_review(propose(world, subject="Kettle",
                                       text="The kettle is descaled on Sundays."))
    _age(nid)
    out = search(world, "kettle descaled")
    assert "added without review" in out and "days ago" in out
    assert "confirmed" not in out


def test_a_revise_applied_without_review_says_changed_without_review(world):
    old = add_note("The kettle is descaled monthly.", subject="Kettle")
    apply_without_review(propose(world, action="revise", target=old, subject="Kettle",
                                 text="The kettle is descaled weekly."))
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
        for _ in range(config.notes_max_results() + 2):
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


# --- status and decisions ------------------------------------------------------------------


def test_approval_status_reads_required_and_a_stored_off_says_nothing_is_applied(world, capsys):
    cli.main(["approval", "status"])
    assert "REQUIRED" in capsys.readouterr().out
    raw_set("false")
    cli.main(["approval", "status"])
    out = capsys.readouterr().out
    assert "OFF" in out and "every proposal waits for you" in out


def test_a_human_decision_works_whatever_the_stored_setting(world):
    propose(world)
    raw_set("false")
    note_admin.decide(one_proposal()["id"], "approved")
    assert [n["origin"] for n in rows("notes")] == ["entity"]
    assert rows("note_proposals")[0]["status"] == "approved"


def test_the_approval_command_refuses_a_store_below_the_notes_v2_schema(
        isolated_data_dir, monkeypatch, capsys):
    from program.memory import migrations
    full = list(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", [m for m in full if m.version <= 7])
    db.init_databases()
    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    assert cli.main(["approval", "on"]) == 3
    assert "schema version 7" in capsys.readouterr().err
    assert migrations.current_version() == 7


# --- review --applied (rows applied without review before Notes v2) -------------------------


def test_review_applied_shows_the_same_view_a_pending_proposal_gets(world, capsys):
    apply_without_review(propose(world, untrusted=["web_fetch"]))
    assert cli.main(["review", "--applied"]) == 0
    text = capsys.readouterr().out
    assert "1 proposal(s) applied WITHOUT review" in text
    assert "UNTRUSTED CONTEXT" in text and "WARNING" in text and "web_fetch" in text
    assert world.conv in text and "call id:" in text
    assert "speaker: Lyle (role user)    tier: context" in text
    assert "IDENTITY GATE" in text and "noisy aid" in text
    assert "THE CONVERSATION IT CAME FROM" in text
    assert "RESULT" in text and "[active]" in text and "origin entity" in text
    assert "applied without review" in text
    assert "This proposal is pending" not in text


def test_review_applied_prints_none_recorded_distinctly(world, capsys):
    apply_without_review(propose(world))                    # the pass always records: none
    cli.main(["review", "--applied"])
    assert "none (recorded" in capsys.readouterr().out


def test_review_applied_lists_only_applied_proposals_and_changes_nothing(world, capsys):
    note_admin.decide(propose(world, subject="Rejected one"), "rejected")
    propose(world, subject="Still pending", text="Takes it with honey.")
    apply_without_review(propose(world, subject="Applied one", text="Takes it black."))
    before = snapshot()
    cli.main(["review", "--applied"])
    text = capsys.readouterr().out
    assert text.count("PROPOSAL ") == 1
    assert snapshot() == before


def test_review_applied_with_nothing_applied_says_so(world, capsys):
    cli.main(["review", "--applied"])
    assert "No proposals have been applied without review." in capsys.readouterr().out


# --- the two axes --------------------------------------------------------------------------


def _offered(monkeypatch, enabled):
    monkeypatch.setenv("ANAM_NOTES_ENABLED", "true" if enabled else "false")
    config.reload()
    registry.reset_default_registry()
    return set(registry.default_registry().names)


def test_enabled_off_note_search_is_not_offered_and_operator_controls_still_work(
        world, monkeypatch):
    assert "note_search" not in _offered(monkeypatch, enabled=False)
    note_admin.operator_add("person", "Jodie", "Takes oat milk.")
    assert [n["origin"] for n in rows("notes")] == ["operator"]


def test_enabled_on_offers_note_search_only_and_a_proposal_waits(world, monkeypatch):
    names = _offered(monkeypatch, enabled=True)
    assert "note_search" in names and "note_propose" not in names
    propose(world)
    assert rows("notes") == [] and one_proposal()["status"] == "pending"
    note_admin.decide(one_proposal()["id"], "approved")          # a human decision works
    assert len(rows("notes")) == 1


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
