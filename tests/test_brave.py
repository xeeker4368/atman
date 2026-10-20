"""The Brave Search API as web_search's fallback (decisions #38, #39).

Design: ``docs/BRAVE_SEARCH_DESIGN.md`` revision 2. **No test here reaches the network**: every
request goes to a fake ``requests.get`` that routes SearXNG and Brave by URL, and ``conftest``
refuses any real request to ``api.search.brave.com``. **The key is fake** (``BSA`` and 28 zeros),
written to a temporary ``local.toml``. **The clock is fake** (``brave._clock``).
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient
from requests.structures import CaseInsensitiveDict

from program import auth, config
from program.api.app import create_app
from program.engine import loop
from program.integrity import classifier, corrections
from program.memory import db
from program.tools import brave, registry, web_search
from program.tools.registry import StoredText, ToolOutcome, ToolRegistry
from program.tools.web_search import WEB_SEARCH
from tests import conftest

FAKE_KEY = "BSA" + "0" * 28
OCTOBER = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
SECRET = "test-signing-secret-that-is-long-enough"
PASSWORD = "correct horse battery staple"

#: The headers Brave sent on Lyle's credit-billed plan (2026-10-19): monthly limit 0.
CREDIT_PLAN_HEADERS = {
    "X-RateLimit-Limit": "50, 0", "X-RateLimit-Policy": "50;w=1, 0;w=2678400",
    "X-RateLimit-Remaining": "49, 0", "X-RateLimit-Reset": "1, 1046159",
}

SNIPPET = "A distinctive snippet that only the turn should see."


# --- fixtures ------------------------------------------------------------------


def _write_config(tmp_path, monkeypatch, local: str):
    directory = tmp_path / "config"
    directory.mkdir(exist_ok=True)
    shutil.copy2(conftest.TRACKED_CONFIG_DIR / "defaults.toml", directory / "defaults.toml")
    (directory / "local.toml").write_text(local)
    monkeypatch.setenv("ANAM_CONFIG_DIR", str(directory))
    config.reload()
    return directory


@pytest.fixture
def clock(monkeypatch):
    now = {"t": OCTOBER}
    monkeypatch.setattr(brave, "_clock", lambda: now["t"])
    monkeypatch.setattr(brave, "_stop_logged", set())
    return now


@pytest.fixture
def fallback_on(isolated_data_dir, monkeypatch, clock):
    """Brave as the fallback, with the fake key, a migrated scratch store and a fake clock."""
    _write_config(isolated_data_dir, monkeypatch,
                  f'[search]\nfallback = "brave"\nbrave_api_key = "{FAKE_KEY}"\n')
    db.init_databases()
    return isolated_data_dir


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code = status
        self._body = body
        self.headers = CaseInsensitiveDict(headers or {})
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


def searxng_payload(results=0, down=()):
    return {"results": [{"title": f"SearXNG result {i}", "url": f"https://s.example/{i}",
                         "content": SNIPPET, "score": 1.0, "engines": ["google cse"]}
                        for i in range(results)],
            "unresponsive_engines": [list(d) for d in down]}


def brave_body(results=3):
    return {"web": {"results": [
        {"title": f"<strong>Brave</strong> result {i}", "url": f"https://b.example/{i}",
         "description": f"{SNIPPET} &amp; <strong>more</strong>"} for i in range(results)]}}


class Net:
    """Routes SearXNG and Brave requests; records each request."""

    def __init__(self, searxng=None, brave_answer=None):
        self.searxng = searxng if searxng is not None else FakeResponse(body=searxng_payload(3))
        self.brave = brave_answer if brave_answer is not None else FakeResponse(
            body=brave_body(), headers=CREDIT_PLAN_HEADERS)
        self.requests: list[dict] = []

    def __call__(self, url, params=None, headers=None, timeout=None, **_):
        self.requests.append({"url": url, "params": params, "headers": headers,
                              "timeout": timeout})
        answer = self.brave if url.startswith("https://api.search.brave.com") else self.searxng
        if isinstance(answer, BaseException):
            raise answer
        return answer

    @property
    def brave_calls(self):
        return [r for r in self.requests if r["url"] == brave.ENDPOINT]


@pytest.fixture
def net(monkeypatch):
    fake = Net()
    monkeypatch.setattr(requests, "get", fake)
    return fake


def seed(n, month="2026-10", limit=None, remaining=None):
    with db.transaction() as conn:
        conn.executemany(
            "INSERT INTO brave_calls (id, month, called_at, outcome, month_limit, "
            "month_remaining) VALUES (?, ?, ?, 'ok', ?, ?)",
            [(f"seed-{month}-{i}", month, f"{month}-01T00:00:00+00:00", limit, remaining)
             for i in range(n)])


def rows(month="2026-10"):
    with db.connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM brave_calls WHERE month = ? AND id NOT LIKE 'seed-%' ORDER BY rowid",
            (month,))]


def search(query="rare thing"):
    return ToolRegistry([WEB_SEARCH]).dispatch("web_search", {"query": query})


# --- config and the key ----------------------------------------------------------


def test_the_shipped_default_is_searxng_alone_and_the_key_is_never_read(monkeypatch, net):
    def must_not_read():
        raise AssertionError("the key was read with the fallback off")

    monkeypatch.setattr(config, "brave_api_key", must_not_read)
    net.searxng = FakeResponse(status=500, body={"error": "boom"})
    assert config.search_fallback() == "none"
    config.check_search()

    result = search()
    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert "SearXNG returned HTTP 500" in result.error
    assert net.brave_calls == []


def test_the_key_lives_nowhere_but_local_toml():
    """Not in the public defaults, not in the environment map, not in the code's fallback."""
    defaults_path = conftest.TRACKED_CONFIG_DIR / "defaults.toml"
    defaults = tomllib.loads(defaults_path.read_text())
    assert config.BRAVE_KEY_NAME not in defaults.get("search", {})
    assert not [line for line in defaults_path.read_text().splitlines()
                if line.strip().startswith(config.BRAVE_KEY_NAME)]
    assert all(key != config.BRAVE_KEY_NAME for _, key, _ in config._ENV_MAP.values())
    assert config.BRAVE_KEY_NAME not in config._FALLBACK.get("search", {})


def test_the_merged_configuration_never_holds_the_key(fallback_on):
    assert config.get("search", config.BRAVE_KEY_NAME) is None
    assert FAKE_KEY not in json.dumps(config._config(), default=str)
    assert config.brave_api_key() == FAKE_KEY


@pytest.mark.parametrize("value, why", [
    (None, "is not set"),
    ('"short"', "malformed"),
    ('"has spaces in it but is long enough"', "malformed"),
    ("12345678901234567890123", "malformed"),
    ('"YOUR_BRAVE_API_KEY_GOES_HERE"', "placeholder"),
])
def test_a_missing_or_malformed_key_is_refused_without_showing_it(
        isolated_data_dir, monkeypatch, value, why):
    line = "" if value is None else f"brave_api_key = {value}\n"
    _write_config(isolated_data_dir, monkeypatch, f'[search]\nfallback = "brave"\n{line}')
    with pytest.raises(config.ConfigError, match=why) as caught:
        config.check_search()
    message = str(caught.value)
    assert "local.toml [search] brave_api_key" in message
    if value is not None:
        assert value.strip('"') not in message


def test_startup_refuses_a_malformed_key_and_starts_with_a_good_one(
        isolated_data_dir, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    _write_config(isolated_data_dir, monkeypatch,
                  '[search]\nfallback = "brave"\nbrave_api_key = "not a key"\n')
    with pytest.raises(config.ConfigError, match="malformed"):
        with TestClient(create_app()):
            pass
    assert not db.working_path().exists(), "refused before the store was touched"

    _write_config(isolated_data_dir, monkeypatch,
                  f'[search]\nfallback = "brave"\nbrave_api_key = "{FAKE_KEY}"\n')
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200


def test_startup_with_a_search_section_and_the_fallback_off(isolated_data_dir, monkeypatch):
    """Review item 5: the server starts with `[search]` present and holding a key."""
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    _write_config(isolated_data_dir, monkeypatch, f'[search]\nbrave_api_key = "{FAKE_KEY}"\n')
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200


def test_the_stop_is_derived_from_the_published_price():
    """$5 credit / ($5 per 1,000 requests) = 1,000 calls; less 20 unseen = 980."""
    credit = config.get("search", "brave_credit_usd")
    price = config.get("search", "brave_price_per_1000_usd")
    assert (credit, price) == (5.0, 5.0)
    assert config.brave_credit_calls() == int(credit / price * 1000) == 1000
    assert config.brave_stop_monthly() == 1000 - config.BRAVE_UNSEEN_CALLS == 980
    assert config.brave_warn_monthly() == 900
    assert config.brave_max_per_turn() == 3
    assert config.search_store_snippets() is False


def test_a_stop_above_the_credit_is_refused(isolated_data_dir, monkeypatch):
    _write_config(isolated_data_dir, monkeypatch, "[search]\nbrave_stop_monthly = 1001\n")
    with pytest.raises(config.ConfigError, match="credit"):
        config.check_search()


# --- the fallback rule -------------------------------------------------------------


def test_searxng_with_results_is_used_and_brave_is_not_called(fallback_on, net):
    result = search()
    assert result.ok
    assert web_search.SOURCE_SEARXNG in result.value
    assert net.brave_calls == []
    assert rows() == []


def test_nothing_found_with_every_engine_answering_is_not_retried(fallback_on, net):
    net.searxng = FakeResponse(body=searxng_payload(0))
    result = search()
    assert result.value == f"{web_search.NO_RESULTS}\n\n{web_search.NOTHING_FOUND_SEARXNG}"
    assert net.brave_calls == []


def test_nothing_found_with_engines_down_asks_brave_and_says_why(fallback_on, net):
    net.searxng = FakeResponse(body=searxng_payload(0, down=[("duckduckgo", "CAPTCHA")]))
    result = search()
    text = result.value
    assert "[Source: Brave Search, the fallback. It ran because the primary search (SearXNG) " \
           "returned nothing, and 1 of its engines did not answer (duckduckgo).]" in text
    assert "1. Brave result 0" in text, "tags are stripped from Brave's titles"
    assert f"{SNIPPET} & more" in text
    [call] = net.brave_calls
    assert call["params"] == {"q": "rare thing", "count": config.searxng_max_results()}
    assert call["headers"]["X-Subscription-Token"] == FAKE_KEY
    assert FAKE_KEY not in call["url"]
    assert call["timeout"] == config.brave_timeout_seconds()
    [row] = rows()
    assert (row["outcome"], row["http_status"]) == ("ok", 200)
    assert (row["month_limit"], row["month_remaining"]) == (0, 0)


@pytest.mark.parametrize("failure, reason", [
    (requests.exceptions.Timeout(), "failed: it did not respond within 10s"),
    (requests.exceptions.ConnectionError(), "failed: it could not be reached"),
    (FakeResponse(status=502, body={"error": "bad gateway"}), "failed: it answered HTTP 502"),
    (FakeResponse(body=None, text="<html>"), "failed: it returned an answer that was not JSON"),
    (FakeResponse(body={"no": "results key"}), "failed: it returned no result set"),
])
def test_a_searxng_failure_asks_brave_and_names_the_failure(fallback_on, net, failure, reason):
    net.searxng = failure
    result = search()
    assert result.ok
    assert f"It ran because the primary search (SearXNG) {reason}.]" in result.value
    assert len(net.brave_calls) == 1


def test_brave_with_no_results_is_a_real_answer(fallback_on, net):
    net.searxng = requests.exceptions.Timeout()
    net.brave = FakeResponse(body={"query": {"original": "rare thing"}})
    result = search()
    assert result.value.startswith(web_search.NO_RESULTS), "CO17 still sees an empty search"
    assert "[Source: Brave Search, the fallback, which answered with no results. It ran because " \
           "the primary search (SearXNG) failed: it did not respond within 10s.]" in result.value
    assert corrections.entries_report_nothing_found([search().to_trace_entry()])


def test_both_failing_after_a_searxng_error_is_a_tool_error_naming_both(fallback_on, net):
    net.searxng = requests.exceptions.ConnectionError()
    net.brave = FakeResponse(status=429, body={"type": "ErrorResponse"})
    result = search()
    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert ("No search ran: the primary search (SearXNG) failed: it could not be reached, and "
            "the fallback search (Brave) failed: it answered HTTP 429.") in result.error
    [row] = rows()
    assert (row["outcome"], row["http_status"]) == ("failed", 429), "a failed call still counts"


def test_brave_failing_after_an_empty_searxng_keeps_searxngs_note(fallback_on, net):
    net.searxng = FakeResponse(body=searxng_payload(0, down=[("duckduckgo", "CAPTCHA")]))
    net.brave = requests.exceptions.Timeout()
    text = search().value
    assert text.startswith(web_search.NO_RESULTS)
    assert "No search engine returned anything" in text
    assert text.endswith("[The fallback search (Brave) failed: it did not respond within 5s.]")


def test_a_brave_fallback_render_fits_under_the_loops_cap(fallback_on, net):
    long = "x" * 2000
    net.searxng = FakeResponse(body=searxng_payload(
        0, down=[(f"engine{i}", "a long reason " * 3) for i in range(4)]))
    net.brave = FakeResponse(body={"web": {"results": [
        {"title": long, "url": "https://b.example/" + "a" * 116, "description": long}
        for _ in range(10)]}})
    text = search().value
    assert text.count("\n   https://b.example/") == config.searxng_max_results()
    assert len(text) < config.agent_max_tool_result_chars()


# --- the count, the warning, the stop and the month -----------------------------------


def test_the_warning_fires_at_900_and_every_tenth_call_after(fallback_on, net, caplog):
    net.searxng = requests.exceptions.Timeout()
    seed(898)
    caplog.set_level(logging.WARNING, logger="program.tools.brave")

    def warnings():
        return [r.getMessage() for r in caplog.records if "paid fallback calls used" in r.message]

    search()                                   # call 899
    assert warnings() == []
    search()                                   # call 900
    assert warnings() == ["Brave search: 900 of 980 paid fallback calls used this month "
                          "(warning from 900). scripts/brave_usage.py has the detail."]
    for _ in range(9):                         # calls 901 to 909
        search()
    assert len(warnings()) == 1
    search()                                   # call 910
    assert len(warnings()) == 2 and "910 of 980" in warnings()[-1]


def test_at_the_stop_no_call_is_made_and_the_entity_is_told_plainly(fallback_on, net, caplog):
    seed(980)
    net.searxng = FakeResponse(body=searxng_payload(0, down=[("duckduckgo", "CAPTCHA")]))
    caplog.set_level(logging.WARNING, logger="program.tools.brave")
    text = search().value
    assert net.brave_calls == []
    assert text.startswith(web_search.NO_RESULTS)
    assert text.endswith("[The fallback search (Brave) was not used: the paid search is used up "
                         "for this month, until 1 November 2026 (UTC).]")
    search()
    stop_logs = [r for r in caplog.records if "used up for 2026-10" in r.getMessage()]
    assert len(stop_logs) == 1, "the stop is logged once a month, not on every search"

    net.searxng = requests.exceptions.ConnectionError()
    result = search()
    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert "the fallback search (Brave) was not used: the paid search is used up" in result.error
    assert net.brave_calls == [] and rows() == []


def test_the_month_rolls_over_at_midnight_utc(fallback_on, net, clock):
    seed(980)
    net.searxng = requests.exceptions.Timeout()
    clock["t"] = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert "used up for this month, until 1 November 2026" in search().error
    clock["t"] = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert search().ok
    assert len(net.brave_calls) == 1
    assert [r["month"] for r in rows("2026-11")] == ["2026-11"]


def test_december_rolls_into_the_next_year(fallback_on, net, clock):
    clock["t"] = datetime(2026, 12, 15, tzinfo=timezone.utc)
    seed(980, month="2026-12")
    net.searxng = requests.exceptions.Timeout()
    assert "until 1 January 2027 (UTC)" in search().error


def test_the_credit_plans_monthly_header_reads_zero_and_does_not_stop(fallback_on, net):
    """Lyle's real headers: limit "50, 0", remaining "49, 0". Taken at its word, that 0
    would stop every call; the local counter is the only monthly stop on this plan."""
    net.searxng = requests.exceptions.Timeout()
    for _ in range(3):
        assert search().ok
    assert len(net.brave_calls) == 3
    assert all((r["month_limit"], r["month_remaining"]) == (0, 0) for r in rows())


@pytest.mark.parametrize("remaining, stops", [(21, False), (20, True), (3, True)])
def test_a_monthly_header_with_a_real_limit_is_an_earlier_stop(fallback_on, net, remaining,
                                                               stops):
    net.searxng = requests.exceptions.Timeout()
    net.brave = FakeResponse(body=brave_body(), headers={
        "X-RateLimit-Limit": "50, 2000", "X-RateLimit-Remaining": f"49, {remaining}"})
    assert search().ok
    second = search()
    assert len(net.brave_calls) == (1 if stops else 2)
    assert ("used up" in (second.error or "")) is stops


def test_two_reservations_at_979_let_exactly_one_through(fallback_on):
    seed(979)
    results, barrier = [], threading.Barrier(2)

    def reserve():
        barrier.wait()
        results.append(brave._reserve(OCTOBER))

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r is None for r in results) == [False, True]
    assert brave.usage(OCTOBER).used == 980


def test_the_per_turn_cap_holds_inside_a_turn_and_reaches_the_tools_thread(fallback_on, net):
    net.searxng = requests.exceptions.Timeout()
    bench = ToolRegistry([WEB_SEARCH])
    with brave.turn_scope():
        outcomes = [bench.dispatch("web_search", {"query": f"q{i}"}) for i in range(4)]
    assert [o.ok for o in outcomes] == [True, True, True, False]
    assert ("the fallback search (Brave) was not used: this turn has already made 3 paid "
            "searches, the most one turn may make") in outcomes[3].error
    with brave.turn_scope():
        assert bench.dispatch("web_search", {"query": "a new turn"}).ok
    assert len(net.brave_calls) == 4


def test_outside_a_turn_only_the_monthly_stop_applies(fallback_on, net):
    net.searxng = requests.exceptions.Timeout()
    assert all(search(f"q{i}").ok for i in range(5))


def test_the_agent_loop_scopes_one_count_per_turn(fallback_on, net, monkeypatch):
    net.searxng = requests.exceptions.Timeout()
    calls = {"n": 0}

    def model(messages, *, model=None, options=None, tools=None, timeout=None):
        calls["n"] += 1
        if tools and calls["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "web_search", "arguments": {"query": f"q{i}"}}}
                for i in range(4)]}}
        return {"message": {"role": "assistant", "content": "Done."}}

    monkeypatch.setattr(loop.ollama, "chat", model)
    result = loop.run_turn([{"role": "user", "content": "search"}],
                           registry=ToolRegistry([WEB_SEARCH]))
    assert [e["outcome"] for e in loop.call_entries(result.trace)] == ["ok"] * 3 + ["tool_error"]
    assert brave._turn_calls.get() is None, "the scope ends with the turn"


# --- the stored trace (search.store_snippets) ---------------------------------------


def test_with_snippets_off_the_stored_trace_keeps_titles_and_urls_only(fallback_on, net):
    for source in ("searxng", "brave"):
        if source == "brave":
            net.searxng = requests.exceptions.Timeout()
        result = search()
        assert isinstance(result.value, StoredText)
        assert SNIPPET in result.value, "the model reads the full results"
        [stored] = loop.storable_trace([{"iteration": 1, **result.to_trace_entry()}])
        assert SNIPPET not in stored["value"]
        assert "https://" in stored["value"] and "result 0" in stored["value"]
        assert stored["value"].endswith(web_search.SNIPPETS_NOT_STORED)
        assert SNIPPET not in json.dumps(loop.storable_trace([result.to_trace_entry()]))


def test_with_snippets_on_the_trace_is_stored_as_seen(isolated_data_dir, monkeypatch, net):
    _write_config(isolated_data_dir, monkeypatch, "[search]\nstore_snippets = true\n")
    result = search()
    assert not isinstance(result.value, StoredText)
    [stored] = loop.storable_trace([result.to_trace_entry()])
    assert SNIPPET in stored["value"]


def test_an_empty_search_is_stored_unchanged_so_co17_still_reads_it(fallback_on, net):
    net.searxng = FakeResponse(body=searxng_payload(0))
    stored = json.dumps(loop.storable_trace([search().to_trace_entry()]))
    assert corrections.reports_nothing_found(stored)


# --- /api/me notices, admin only ---------------------------------------------------


@pytest.fixture
def client(fallback_on, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", "4096")
    config.reload()
    auth.throttle.reset()
    registry.reset_default_registry()
    for name, role in (("Lyle", "admin"), ("Jodie", "user")):
        db.set_password_hash(db.create_user(name, role=role), auth.hash_password(PASSWORD))
    yield TestClient(create_app())
    auth.throttle.reset()


def me(client, name):
    token = client.post("/api/login", json={"name": name, "password": PASSWORD}).json()["token"]
    return client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()


def test_below_the_warning_nobody_gets_a_notice(client):
    seed(899)
    assert me(client, "Lyle") == {"name": "Lyle", "role": "admin"}


def test_at_the_warning_the_admin_gets_a_notice_and_the_user_never_receives_one(client):
    seed(900)
    assert me(client, "Lyle")["notices"] == [
        "Brave search, the paid fallback: 900 of 980 calls used in 2026-10 (warning from 900). "
        "scripts/brave_usage.py has the detail."]
    assert me(client, "Jodie") == {"name": "Jodie", "role": "user"}


def test_at_the_stop_the_admin_notice_says_it_is_used_up(client):
    seed(980)
    [notice] = me(client, "Lyle")["notices"]
    assert notice.startswith("Brave search, the paid fallback, is used up for 2026-10: web "
                             "search runs on SearXNG alone until 1 November 2026 (UTC).")
    assert "notices" not in me(client, "Jodie")


def test_the_page_shows_notices_with_textcontent_only():
    source = (Path(__file__).resolve().parents[1] / "program" / "api" / "static"
              / "app.js").read_text()
    assert 'show($("admin-notice"), notices.join(" "));' in source
    assert "node.textContent = text;" in source, "show() sets text, never markup"


def test_a_real_turn_stores_the_short_form_and_the_gate_reads_the_full_one(client, net,
                                                                          monkeypatch):
    """Through POST /api/chat: the turn, the gate and the save are real; only the model,
    the classifier and retrieval are substituted."""
    from program.engine import turn

    seen_by_gate = []
    real_check = turn.gate.check

    def gate_spy(text, trace, situation):
        seen_by_gate.append(json.dumps(list(trace), default=str))
        return real_check(text, trace, situation)

    def model(messages, *, model=None, options=None, tools=None, timeout=None):
        if tools and not any(m.get("role") == "tool" for m in messages):
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "web_search", "arguments": {"query": "kettles"}}}]}}
        return {"message": {"role": "assistant", "content": "I found three pages."}}

    monkeypatch.setattr(turn.gate, "check", gate_spy)
    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    monkeypatch.setattr(classifier, "classify", lambda prompt: "CONSISTENT")
    monkeypatch.setattr(loop.ollama, "chat", model)
    token = client.post("/api/login", json={"name": "Lyle", "password": PASSWORD}).json()
    reply = client.post("/api/chat", json={"message": "Look up kettles."},
                        headers={"Authorization": f"Bearer {token['token']}"})
    assert reply.status_code == 200
    assert SNIPPET in seen_by_gate[0]
    with db.connection() as conn:
        [stored] = [r["tool_trace"] for r in conn.execute(
            "SELECT tool_trace FROM messages WHERE role = 'assistant'")]
    assert "SearXNG result 0" in stored and "https://s.example/0" in stored
    assert SNIPPET not in stored


# --- the usage script -------------------------------------------------------------


def test_the_usage_script_reports_read_only(fallback_on):
    from scripts import brave_usage

    seed(905, limit=0, remaining=0)
    conn = sqlite3.connect(f"file:{db.working_path()}?mode=ro", uri=True)
    try:
        text = "\n".join(brave_usage.report(conn))
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM brave_calls")
    finally:
        conn.close()
    assert "Brave fallback usage, 2026-10 (UTC)" in text
    assert "calls this month   905" in text
    assert "calls left         75" in text
    assert "credit left        $0.47 of $5.00" in text
    assert "not used: Brave's monthly limit reads 0 on this plan" in text
    assert "WARNING: past the warning" in text
    assert FAKE_KEY not in text


def test_the_usage_script_says_when_the_store_has_no_table_yet(tmp_path):
    from scripts import brave_usage

    conn = sqlite3.connect(tmp_path / "empty.db")
    assert "migration 10 has not run" in brave_usage.report(conn)[0]


# --- the key never leaks -----------------------------------------------------------


def test_the_key_appears_in_no_log_result_error_or_stored_trace(fallback_on, net, caplog,
                                                                  clock):
    """Every path, with logging at DEBUG, the fake key searched for everywhere it could go."""
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []
    paths = [
        (FakeResponse(body=searxng_payload(2)), None),
        (requests.exceptions.Timeout(),
         FakeResponse(body=brave_body(), headers=CREDIT_PLAN_HEADERS)),
        (requests.exceptions.Timeout(), FakeResponse(body={"web": {"results": []}})),
        (requests.exceptions.Timeout(), requests.exceptions.Timeout(FAKE_KEY)),
        (requests.exceptions.Timeout(), requests.exceptions.ConnectionError(
            f"https://api.search.brave.com/?token={FAKE_KEY}")),
        (requests.exceptions.Timeout(), FakeResponse(status=401, body={"key": FAKE_KEY},
                                                     text=f"bad key {FAKE_KEY}")),
        (requests.exceptions.Timeout(), FakeResponse(body=None, text=FAKE_KEY)),
        (FakeResponse(body=searxng_payload(0, down=[("x", "y")])), requests.exceptions.Timeout()),
    ]
    for searxng, brave_answer in paths:
        net.searxng = searxng
        if brave_answer is not None:
            net.brave = brave_answer
        with brave.turn_scope():
            result = search()
        seen += [json.dumps(result.to_trace_entry(), default=str),
                 json.dumps(loop.storable_trace([result.to_trace_entry()]))]
    seed(980)
    net.searxng = requests.exceptions.Timeout()
    seen.append(json.dumps(search().to_trace_entry()))
    try:
        brave._reserve(OCTOBER)
    except Exception as exc:  # noqa: BLE001
        seen.append(repr(exc))
    with db.connection() as conn:
        seen.append(json.dumps([list(r) for r in conn.execute("SELECT * FROM brave_calls")]))
    seen += [r.getMessage() for r in caplog.records]
    seen += [str(r.exc_info) for r in caplog.records if r.exc_info]
    assert len(net.brave_calls) >= 6, "the paths really reached the fake Brave"
    assert not [s for s in seen if FAKE_KEY in s]


def test_a_test_cannot_reach_the_real_brave_api():
    with pytest.raises(conftest.NetworkGuardViolation, match="api.search.brave.com"):
        requests.get(brave.ENDPOINT, params={"q": "x"}, timeout=1)
