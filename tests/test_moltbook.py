"""Read-only Moltbook tools. Design of record: docs/MOLTBOOK_READ_DESIGN.md.

Every test runs against scrubbed fixtures captured from the live API on
2026-09-30 (tests/fixtures/moltbook/, provenance in its README) through a fake
session, so the suite never touches the network. The two live tests are opt-in
(``ANAM_MOLTBOOK_LIVE=1``) and send no key.
"""

from __future__ import annotations

import inspect
import json
import os
import re
from pathlib import Path

import pytest
import requests

from program import config
from program.tools import catalog, moltbook, registry
from program.tools.registry import ToolOutcome

FIXTURES = Path(__file__).parent / "fixtures" / "moltbook"
SENTINEL = "SENTINEL-MUST-NOT-RENDER"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def enabled(monkeypatch):
    """Moltbook switched on. Conftest switches it off for everyone."""
    monkeypatch.setenv("ANAM_MOLTBOOK_ENABLED", "true")
    config.reload()
    registry.reset_default_registry()
    yield
    registry.reset_default_registry()


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, text=None):
        self.status_code = status
        self._payload = payload
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self._text = text

    def json(self):
        if self._text is not None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Records every request; answers from a queue. Replaces requests.Session."""

    calls: list[dict] = []
    answers: list = []
    sessions: list["FakeSession"] = []

    def __init__(self):
        self.trust_env = True
        FakeSession.sessions.append(self)

    def get(self, url, params=None, timeout=None, allow_redirects=True, headers=None):
        FakeSession.calls.append({
            "url": url, "params": dict(params or {}), "timeout": timeout,
            "allow_redirects": allow_redirects, "headers": dict(headers or {}),
            "trust_env": self.trust_env,
        })
        answer = FakeSession.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def close(self):
        pass


@pytest.fixture
def api(monkeypatch):
    FakeSession.calls, FakeSession.answers, FakeSession.sessions = [], [], []
    monkeypatch.setattr(moltbook.requests, "Session", FakeSession)

    def answer(*responses):
        FakeSession.answers.extend(responses)
        return FakeSession.calls

    return answer


# --- the transport: what leaves this machine --------------------------------


def test_no_request_carries_credentials_or_follows_a_proxy_or_redirect(api):
    """Every read endpoint answered without authentication (measured 2026-09-30),
    so the key is never sent. Proven on the request actually made, not on a
    reading of the code."""
    calls = api(FakeResponse(payload=fixture("posts_list")))
    moltbook.browse()

    (call,) = calls
    assert not any(k.lower() == "authorization" for k in call["headers"])
    assert call["trust_env"] is False, "a proxy variable could re-route the request"
    assert call["allow_redirects"] is False
    assert call["url"] == "https://www.moltbook.com/api/v1/posts"


def test_the_module_never_reads_the_key():
    """Structural half of the above: the read tools have no route to the secret.

    Checked on the code, not on its prose: docstrings explain why there is no
    Authorization header, so a text search would fail on the explanation, or pass
    on a rewording. The AST is walked with docstrings removed."""
    import ast

    tree = ast.parse(inspect.getsource(moltbook))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    code_strings = [
        n.value.lower() for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and id(n) not in docstrings
    ]
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

    assert not [s for s in code_strings if "authorization" in s or "bearer" in s]
    assert not [s for s in code_strings if "api_key" in s]
    assert not {n for n in names if "api_key" in n.lower()}


def test_a_redirect_is_refused_and_not_followed(api):
    calls = api(FakeResponse(302, headers={"Location": "https://moltbook.com/api/v1/posts"}))
    with pytest.raises(moltbook.MoltbookError, match="redirect .* moltbook.com.*not followed"):
        moltbook.browse()
    assert len(calls) == 1


def test_a_rate_limit_names_the_wait_and_is_never_retried(api):
    calls = api(FakeResponse(429, {"statusCode": 429, "message": "Rate limit exceeded"},
                             headers={"Retry-After": "42"}))
    with pytest.raises(moltbook.MoltbookError, match=r"about 42 seconds\. Nothing was retried"):
        moltbook.search("anything")
    assert len(calls) == 1


def test_a_rate_limit_wait_is_read_from_the_body_when_the_header_is_absent(api):
    api(FakeResponse(429, {"statusCode": 429, "retry_after_seconds": 7}))
    with pytest.raises(moltbook.MoltbookError, match="about 7 seconds"):
        moltbook.browse()


@pytest.mark.parametrize("status", [401, 403])
def test_moltbook_starting_to_require_authentication_is_named(api, status):
    """Reads are public today. If that changes, the entity and the operator should
    see why, not a bare status."""
    api(FakeResponse(status, {"statusCode": status, "message": "Unauthorized"}))
    with pytest.raises(moltbook.MoltbookError,
                       match=rf"refused a read that needs no key .*HTTP {status}.*send no key"):
        moltbook.browse()


@pytest.mark.parametrize("payload, expected", [
    ({"statusCode": 500, "message": "observed shape", "error": "Internal"}, "observed shape"),
    ({"success": False, "error": "documented shape", "hint": "h"}, "documented shape"),
    ({}, "HTTP 500"),
])
def test_both_error_shapes_are_read(api, payload, expected):
    """The live 404 did not match the published error shape (M2), so both are read."""
    api(FakeResponse(500, payload))
    with pytest.raises(moltbook.MoltbookError, match=expected):
        moltbook.browse()


def test_a_timeout_reports_the_bound(api):
    api(requests.exceptions.Timeout("slow"))
    with pytest.raises(moltbook.MoltbookError, match="did not respond within 10s"):
        moltbook.browse()


def test_an_unreachable_host_says_what_to_check(api):
    api(requests.exceptions.ConnectionError("down"))
    with pytest.raises(moltbook.MoltbookError, match="Cannot reach Moltbook"):
        moltbook.browse()


def test_a_non_json_body_is_an_error(api):
    api(FakeResponse(200, text="<html>"))
    with pytest.raises(moltbook.MoltbookError, match="non-JSON"):
        moltbook.browse()


# --- browse ------------------------------------------------------------------


def test_browse_renders_named_fields_only(api):
    payload = fixture("posts_list")
    for post in payload["posts"]:
        post["author"]["description"] = SENTINEL  # promotional text in the capture
    calls = api(FakeResponse(payload=payload))

    out = moltbook.browse(sort="new", submolt="submolt-01")

    assert calls[0]["params"] == {"sort": "new", "limit": 10, "submolt": "submolt-01"}
    assert out.startswith(moltbook.HEADER)
    assert SENTINEL not in out
    assert out.count("post id: ") == config.moltbook_max_results()
    assert "agent-" in out  # authors by name


def test_an_unknown_top_level_field_never_renders(api):
    """/feed's server-written `tip` stands in for any field Moltbook adds later:
    rendering reads an allowlist, so it cannot reach the entity by default."""
    feed = fixture("feed_with_tip")
    assert feed["tip"]
    api(FakeResponse(payload=feed))
    assert feed["tip"] not in moltbook.browse()


def test_spam_and_deleted_posts_are_left_out_and_counted(api):
    payload = fixture("posts_list")
    payload["posts"][0]["is_spam"] = True
    payload["posts"][1]["is_deleted"] = True
    payload["posts"][0]["title"] = SENTINEL
    api(FakeResponse(payload=payload))

    out = moltbook.browse()

    assert SENTINEL not in out
    assert "2 item(s) Moltbook marks as spam or deleted were left out" in out


def test_an_empty_list_says_so(api):
    api(FakeResponse(payload={"success": True, "posts": [], "has_more": False}))
    assert moltbook.browse() == moltbook.NO_RESULTS


@pytest.mark.parametrize("bad", ["best", "../x", "HOT; drop"])
def test_an_unknown_sort_is_refused_before_any_request(api, bad):
    calls = api()
    with pytest.raises(ValueError, match="sort must be one of"):
        moltbook.browse(sort=bad)
    assert calls == []


# --- search ------------------------------------------------------------------


def test_search_renders_each_kind_by_type(api):
    payload = fixture("search_mixed")
    for result in payload["results"]:
        if result["type"] == "agent":
            result["content"] = SENTINEL  # an agent result's text is its self-description
    payload["results"].append(
        {"type": "submolt", "id": "x", "content": SENTINEL, "author": {"name": "n"}})
    calls = api(FakeResponse(payload=payload))

    out = moltbook.search("what agents say about memory", type="all")

    assert calls[0]["params"] == {"q": "what agents say about memory", "type": "all", "limit": 10}
    assert SENTINEL not in out
    assert "an agent named agent-" in out
    assert "a reply by agent-" in out and "post id: " in out
    assert "a result of a kind this tool does not show (submolt)" not in out  # 6th: capped
    assert out.startswith(moltbook.HEADER)


def test_an_unknown_result_kind_is_described_not_dumped():
    item = {"type": "submolt", "content": SENTINEL}
    rendered = moltbook._render_search_item(item, 1)
    assert "does not show (submolt)" in rendered and SENTINEL not in rendered


def test_a_query_over_500_characters_is_refused_before_any_request(api):
    """The server accepted 501 characters (M2), so the limit is enforced here."""
    calls = api()
    with pytest.raises(ValueError, match="501 characters; the limit is 500"):
        moltbook.search("x" * 501)
    assert calls == []
    api(FakeResponse(payload=fixture("search_mixed")))
    moltbook.search("x" * 500)  # the limit itself is allowed


@pytest.mark.parametrize("query", ["", "   "])
def test_an_empty_query_is_refused(api, query):
    with pytest.raises(ValueError, match="query was empty"):
        moltbook.search(query)


def test_a_search_with_no_result_set_is_an_error_not_nothing_found(api):
    api(FakeResponse(payload={"success": True}))
    with pytest.raises(moltbook.MoltbookError, match="no result set"):
        moltbook.search("anything")


# --- read_post ---------------------------------------------------------------


COMMENTS = {
    "success": True, "count": 3, "has_more": False,
    "comments": [
        {"id": "c1", "content": "first reply", "author": {"name": "agent-20",
                                                          "description": SENTINEL},
         "created_at": "2026-09-01T00:00:00Z",
         "replies": [{"id": "c2", "content": "nested reply", "author": {"name": "agent-21"},
                      "created_at": "2026-09-01T01:00:00Z", "replies": []}]},
        {"id": "c3", "content": SENTINEL, "is_deleted": True, "author": {"name": "agent-22"}},
    ],
}


def test_read_post_renders_the_post_and_its_replies(api):
    post = fixture("post")
    calls = api(FakeResponse(payload=post), FakeResponse(payload=COMMENTS))

    out = moltbook.read_post(post["post"]["id"])

    assert calls[0]["url"].endswith(f"/posts/{post['post']['id']}")
    assert calls[1]["url"].endswith(f"/posts/{post['post']['id']}/comments")
    assert "first reply" in out and "nested reply" in out
    assert SENTINEL not in out
    assert out.startswith(moltbook.HEADER)


@pytest.mark.parametrize("bad", ["../agents/profile", "123", "not-a-uuid", ""])
def test_a_post_id_that_is_not_a_uuid_is_refused_before_any_request(api, bad):
    """The id becomes a path segment, so anything else could walk the API."""
    calls = api()
    with pytest.raises(ValueError):
        moltbook.read_post(bad)
    assert calls == []


def test_a_missing_post_says_so(api):
    api(FakeResponse(404, {"statusCode": 404, "message": "Post not found"}))
    out = moltbook.read_post("00000000-0000-4000-8000-000000000001")
    assert out == "Moltbook has no post with id 00000000-0000-4000-8000-000000000001."


def test_replies_that_fail_to_load_still_return_the_post(api):
    post = fixture("post")
    api(FakeResponse(payload=post), requests.exceptions.Timeout("slow"))
    out = moltbook.read_post(post["post"]["id"])
    assert "Replies could not be loaded: Moltbook did not respond" in out
    assert post["post"]["content"][:40] in out


def test_the_two_requests_share_one_deadline(api, monkeypatch):
    """The replies get what the post left, never a fresh timeout (M3, item 18)."""
    clock = [1000.0]
    monkeypatch.setattr(moltbook.time, "monotonic", lambda: clock[0])
    post = fixture("post")

    def slow_post(*a, **k):
        clock[0] += 15.0  # the post request used 15 of the 20 seconds
        return FakeResponse(payload=post)

    calls = api()
    FakeSession.answers.extend([None, FakeResponse(payload=COMMENTS)])
    real_get = FakeSession.get

    def get(self, url, **kw):
        if FakeSession.answers and FakeSession.answers[0] is None:
            FakeSession.answers.pop(0)
            FakeSession.calls.append({"url": url, **kw})
            return slow_post()
        return real_get(self, url, **kw)

    monkeypatch.setattr(FakeSession, "get", get)
    moltbook.read_post(post["post"]["id"])

    assert calls[0]["timeout"] == 10.0
    assert calls[1]["timeout"] == pytest.approx(5.0), "the replies got a fresh timeout"


def test_no_replies_are_requested_once_the_deadline_is_spent(api, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(moltbook.time, "monotonic", lambda: clock[0])
    post = fixture("post")

    class Slow(FakeResponse):
        def json(self):
            clock[0] += 21.0
            return post

    calls = api(Slow())
    out = moltbook.read_post(post["post"]["id"])
    assert len(calls) == 1
    assert "time allowed for this post was used up" in out


def test_a_pathological_post_stays_under_the_tool_result_cap(api):
    post = fixture("post")
    post["post"].update(title="T" * 5000, content="C" * 100_000,
                        author={"name": "A" * 500}, submolt={"name": "S" * 500})
    many = {"comments": [{"content": "R" * 5000, "author": {"name": "B" * 500},
                          "replies": []} for _ in range(20)]}
    api(FakeResponse(payload=post), FakeResponse(payload=many))

    out = moltbook.read_post(post["post"]["id"])

    assert len(out) <= config.agent_max_tool_result_chars()
    assert "the rest of this post is not shown" in out
    assert "more repl(ies) not shown" in out


# --- read_agent --------------------------------------------------------------


def test_read_agent_never_renders_the_people_behind_an_account(api):
    """`owner` and `claimed_by` identify real people. The scrubbed fixture has
    them removed, so they are put back here as sentinels to prove the renderer
    would not show them."""
    profile = fixture("profile")
    profile["agent"]["owner"] = {"x_handle": SENTINEL, "x_name": SENTINEL, "x_bio": SENTINEL}
    profile["agent"]["claimed_by"] = SENTINEL
    calls = api(FakeResponse(payload=profile))

    out = moltbook.read_agent("agent-09")

    assert calls[0]["params"] == {"name": "agent-09"}
    assert SENTINEL not in out
    assert "How it describes itself:" in out
    assert "older posts are not available here" in out
    assert out.startswith(moltbook.HEADER)


def test_an_unknown_agent_says_so(api):
    api(FakeResponse(404, fixture("error_404")))
    assert moltbook.read_agent("nobody") == "Moltbook has no agent named 'nobody'."


# --- registration ------------------------------------------------------------


NAMES = ("moltbook_browse", "moltbook_search", "moltbook_read_post", "moltbook_read_agent")


@pytest.mark.parametrize("switch, key, offered", [
    ("true", "", True),        # reads need no key
    ("true", "a-key", True),
    ("false", "a-key", False),  # a configured key does not switch reading on
    ("false", "", False),
])
def test_the_tools_follow_their_own_switch_not_the_key(monkeypatch, switch, key, offered):
    """Reading and posting on separate axes (revision 4): only `moltbook.enabled`
    decides whether the read tools exist. The key is posting's."""
    monkeypatch.setenv("ANAM_MOLTBOOK_ENABLED", switch)
    monkeypatch.setenv("ANAM_MOLTBOOK_API_KEY", key)
    config.reload()
    registry.reset_default_registry()
    try:
        present = set(NAMES) <= set(registry.default_registry().names)
        absent = not set(NAMES) & set(registry.default_registry().names)
        assert (present if offered else absent)
    finally:
        registry.reset_default_registry()
    # listed in the catalogue regardless, so the full set stays greppable
    assert set(NAMES) <= {t.name for t in catalog.TOOLS}


def test_reading_is_off_by_default_in_both_config_layers():
    """Fail closed: a fresh checkout does not call a third-party service. Read from
    the files themselves, since conftest overrides the value for every test."""
    import tomllib

    defaults = tomllib.loads((config.PROJECT_ROOT / "config" / "defaults.toml")
                             .read_text(encoding="utf-8"))
    assert defaults["moltbook"]["enabled"] is False
    assert config._FALLBACK["moltbook"]["enabled"] is False


def test_none_is_a_side_effect_tool():
    """Reading writes nothing, so ACTION and receipts do not apply (M1)."""
    for tool in moltbook.TOOLS:
        assert tool.takes_attribution is False
    assert not set(NAMES) & set(registry.side_effect_tools())


def test_a_failure_reaches_the_model_as_a_tool_error(api, enabled):
    api(FakeResponse(429, {"retry_after_seconds": 3}))
    result = registry.dispatch("moltbook_browse", {})
    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert "about 3 seconds" in result.error


def test_the_timeout_chain_holds_and_leaves_the_grace_floor_alone():
    """Each tool timeout sits above the bound inside it, and under the turn's
    aggregate tool budget, which is the only tool term in the in-flight grace
    floor (tests/test_idle.py). None of this can move that floor."""
    client = config.moltbook_timeout_seconds()
    for tool in (moltbook.MOLTBOOK_BROWSE, moltbook.MOLTBOOK_SEARCH,
                 moltbook.MOLTBOOK_READ_AGENT):
        assert client < tool.resolved_timeout() < config.agent_tool_budget_seconds()
    read_post = moltbook.MOLTBOOK_READ_POST.resolved_timeout()
    assert config.moltbook_read_post_deadline_seconds() < read_post
    assert read_post < config.agent_tool_budget_seconds()
    assert config.IN_FLIGHT_GRACE_FLOOR_MINUTES == 35


def test_max_results_is_the_largest_that_fits_the_cap():
    """The derivation in config/defaults.toml, recomputed from the live renderer."""
    def worst(i):
        return {"id": f"00000000-0000-4000-8000-{i:012d}", "title": "T" * 500,
                "content": "C" * 5000, "author": {"name": "A" * 200},
                "submolt": {"name": "S" * 200}, "created_at": "2026-09-30T00:00:00Z",
                "upvotes": 999999, "downvotes": 0, "comment_count": 999999}

    def rendered(n):
        blocks = [moltbook.HEADER, "Posts in " + "S" * 60 + ", sorted by rising:"]
        blocks += [moltbook._render_post_item(worst(i), i + 1) for i in range(n)]
        blocks.append(moltbook._skipped_note(1))
        return len("\n\n".join(blocks))

    n = config.moltbook_max_results()
    cap = config.agent_max_tool_result_chars()
    assert rendered(n) <= cap < rendered(n + 1)


# --- config ------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://www.moltbook.com/api/v1",       # not https
    "https://moltbook.com/api/v1",          # the bare host
    "https://www.moltbook.com.evil/api/v1",
    "https://www.moltbook.com:8443/api/v1",
    "https://example.com/api/v1",
])
def test_the_base_url_is_pinned_to_one_host(monkeypatch, url):
    monkeypatch.setenv("ANAM_MOLTBOOK_BASE_URL", url)
    config.reload()
    with pytest.raises(config.ConfigError, match="www.moltbook.com"):
        config.moltbook_base_url()


def test_the_shared_deadline_cannot_be_shorter_than_one_request(monkeypatch):
    monkeypatch.setenv("ANAM_MOLTBOOK_READ_POST_DEADLINE_SECONDS", "5")
    config.reload()
    with pytest.raises(config.ConfigError, match="at least"):
        config.moltbook_read_post_deadline_seconds()




# --- the fixtures themselves -------------------------------------------------


def _walk(value, key=None):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from _walk(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v, key)


def test_no_fixture_carries_the_people_behind_an_account():
    """The committed half of the scrub check (M10). The other half compared every
    string against the raw captures at build time; it needs the raw identifiers,
    so it cannot live in a public repository."""
    for path in FIXTURES.glob("*.json"):
        keys = {k for k, _ in _walk(json.loads(path.read_text(encoding="utf-8")))}
        assert not keys & {"owner", "claimed_by"}, path.name


def test_every_fixture_agent_name_is_synthetic():
    pattern = re.compile(r"^agent-\d{2}$")
    for path in FIXTURES.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in _walk(data):
            if key == "author" and isinstance(value, dict):
                assert pattern.match(value["name"]), (path.name, value["name"])
        agent = data.get("agent")
        if agent:
            assert pattern.match(agent["name"]) and pattern.match(agent["display_name"])


def test_each_fixture_still_carries_the_hazard_it_exists_for():
    """web_search's practice: a fixture that lost its hazard tests nothing."""
    assert fixture("feed_with_tip")["tip"]
    kinds = {r["type"] for r in fixture("search_mixed")["results"]}
    assert {"agent", "comment", "post"} <= kinds
    assert any(r["type"] == "agent" and r["post"] is None
               for r in fixture("search_mixed")["results"])
    err = fixture("error_404")
    assert err["statusCode"] == 404 and "message" in err and "success" not in err
    assert fixture("comments_empty")["comments"] == []
    assert fixture("profile")["recentPosts"]
    assert all("is_spam" in p for p in fixture("posts_list")["posts"])


# --- live, opt-in ------------------------------------------------------------

live = pytest.mark.skipif(
    os.environ.get("ANAM_MOLTBOOK_LIVE") != "1",
    reason="live Moltbook tests are opt-in: set ANAM_MOLTBOOK_LIVE=1",
)


@live
def test_live_browse_returns_rendered_posts():
    out = moltbook.browse(sort="new")
    assert out.startswith(moltbook.HEADER) and "post id: " in out


@live
def test_live_search_returns_rendered_matches():
    out = moltbook.search("memory")
    assert out == moltbook.NO_RESULTS or out.startswith(moltbook.HEADER)
