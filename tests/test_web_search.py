"""The `web_search` tool. Task 2.4.

**The fixture is a real response**, captured verbatim from the live SearXNG
instance on 2026-09-08 (`tests/fixtures/searxng_response.json`, 27 results for
"espresso grind size extraction"). Every shape hazard asserted below was
observed in it rather than imagined: results out of score order, `content` empty
on one of them, `number_of_results` absent entirely, and two engines in
`unresponsive_engines`.

The normal run does **not** touch the network — these engines CAPTCHA
unpredictably, and a suite that fails because DuckDuckGo felt suspicious tests
nothing useful. `test_a_live_search_against_the_real_instance` does hit
127.0.0.1:8080, and skips rather than fails when it is not up.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import requests

from program import config
from program.tools import web_search
from program.tools.registry import ToolOutcome, ToolRegistry
from program.tools.web_search import WEB_SEARCH, WebSearchError

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "searxng_response.json"


def searxng_is_up() -> bool:
    try:
        return requests.get(config.searxng_url(), timeout=3).status_code == 200
    except requests.exceptions.RequestException:
        return False


live_only = pytest.mark.skipif(
    not searxng_is_up(),
    reason="local SearXNG is not reachable; live-call test skipped",
)


@pytest.fixture
def payload() -> dict:
    """The real captured response."""
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def bench() -> ToolRegistry:
    return ToolRegistry([WEB_SEARCH])


# --- The captured response is what we think it is ----------------------------


def test_the_fixture_still_carries_the_hazards_it_was_captured_for(payload):
    """If this fails the fixture was edited, and the tests below prove less."""
    scores = [r["score"] for r in payload["results"]]
    assert scores != sorted(scores, reverse=True), "fixture is score-sorted"
    assert "number_of_results" not in payload or payload["number_of_results"] is None
    assert any(not r.get("content") for r in payload["results"]), "no empty content"
    assert payload["unresponsive_engines"], "no unresponsive engines"
    assert len(payload["results"]) > config.searxng_max_results()


# --- Hazard 1: not sorted ----------------------------------------------------


def test_results_are_sorted_by_score_here_because_searxng_does_not(payload):
    ordered = web_search.sort_results(payload["results"])
    scores = [r["score"] for r in ordered]

    assert scores == sorted(scores, reverse=True)
    assert scores[0] == max(r["score"] for r in payload["results"])


def test_the_rendered_results_are_the_highest_scoring_ones(payload):
    rendered = web_search.render_search_payload(payload, limit=3)
    top3 = web_search.sort_results(payload["results"])[:3]

    for result in top3:
        assert result["url"] in rendered
    # A low scorer that only survives an unsorted top-3 must not appear.
    worst = web_search.sort_results(payload["results"])[-1]
    assert worst["url"] not in rendered


def test_a_result_with_an_unusable_score_sorts_last_rather_than_raising():
    results = [{"title": "b", "url": "u2", "score": None},
               {"title": "a", "url": "u1", "score": 1.0}]

    assert [r["title"] for r in web_search.sort_results(results)] == ["a", "b"]


# --- Hazard 2: three shapes, not two -----------------------------------------


def test_a_missing_results_key_is_an_error_not_nothing_found():
    """The empty-query 400 shape. Reporting "nothing found" for a search that
    never ran would be a false report of what happened."""
    with pytest.raises(WebSearchError, match="No query"):
        web_search.render_search_payload({"error": "No query"})


def test_an_empty_results_list_is_nothing_found_not_an_error():
    text = web_search.render_search_payload(
        {"query": "x", "results": [], "unresponsive_engines": []}
    )

    assert text.startswith(web_search.NO_RESULTS)
    assert text.strip()


def test_the_two_empty_shapes_produce_different_outcomes():
    """The distinction is the whole point: one ran, the other did not."""
    ran = web_search.render_search_payload({"results": [], "unresponsive_engines": []})
    with pytest.raises(WebSearchError):
        web_search.render_search_payload({"error": "No query"})
    assert web_search.NO_RESULTS in ran


# --- Hazard 3: content can be empty ------------------------------------------


def test_a_result_with_no_content_still_renders_its_title_and_link():
    payload = {"results": [{"title": "A page", "url": "https://example.invalid/a",
                            "content": "", "score": 1.0}],
               "unresponsive_engines": []}

    text = web_search.render_search_payload(payload)

    assert "A page" in text
    assert "https://example.invalid/a" in text
    assert "None" not in text


def test_the_real_empty_content_result_renders_without_a_dangling_line(payload):
    empty = next(r for r in payload["results"] if not r.get("content"))
    text = web_search.render_search_payload(payload, limit=len(payload["results"]))

    assert empty["url"] in text
    assert "\n   \n" not in text, "an empty snippet left a blank indented line"


# --- Hazard 4: number_of_results is never read -------------------------------


def test_nothing_reads_number_of_results():
    """Present-but-null in one response, absent from the next three."""
    source = pathlib.Path(web_search.__file__).read_text()
    assert "number_of_results" not in source.split('"""')[2], (
        "number_of_results is referenced in code, not just in the docstring"
    )

    absent = {"results": [{"title": "t", "url": "u", "score": 1.0}],
              "unresponsive_engines": []}
    null = dict(absent, number_of_results=None)
    lying = dict(absent, number_of_results=99999)

    rendered = [web_search.render_search_payload(p) for p in (absent, null, lying)]
    assert rendered[0] == rendered[1] == rendered[2]


# --- Degradation, the memory_search pattern ----------------------------------


def test_unresponsive_engines_produce_a_degradation_note(payload):
    text = web_search.render_search_payload(payload)

    assert "This search was incomplete" in text
    assert "duckduckgo" in text
    assert "CAPTCHA" in text


def test_nothing_found_with_engines_down_says_both(payload):
    """The case where it matters most: a different claim from nothing found."""
    text = web_search.render_search_payload(
        {"results": [], "unresponsive_engines": payload["unresponsive_engines"]}
    )

    assert web_search.NO_RESULTS in text
    assert "This search was incomplete" in text


def test_a_healthy_search_carries_no_note():
    text = web_search.render_search_payload(
        {"results": [{"title": "t", "url": "u", "score": 1.0}],
         "unresponsive_engines": []}
    )

    assert "incomplete" not in text


# --- The cap, and the context budget it was derived from ---------------------


def test_the_cap_is_applied_and_stated(payload):
    text = web_search.render_search_payload(payload)
    numbered = re.findall(r"^\d+\. ", text, flags=re.MULTILINE)

    assert len(numbered) == config.searxng_max_results() == 6
    assert "Showing the 6 highest-scoring of 27 results" in text


def test_the_rendered_output_fits_under_the_loops_truncation_cap(payload):
    """The cap exists because agent.max_tool_result_chars is where the loop cuts.

    Six results at the measured worst case (~537 chars each) plus header and
    notes comes to ~3,650 — under 4,000. Seven would not.
    """
    text = web_search.render_search_payload(payload)

    assert len(text) < config.agent_max_tool_result_chars()
    assert len(text) < 4000


def test_a_long_snippet_is_clipped_and_says_so():
    payload = {"results": [{"title": "t", "url": "u", "content": "x" * 900,
                            "score": 1.0}],
               "unresponsive_engines": []}

    text = web_search.render_search_payload(payload)

    assert "…" in text
    assert "x" * 400 not in text


def test_a_url_is_never_truncated():
    """A shortened URL is a URL that does not resolve, and the model may quote it."""
    long_url = "https://example.invalid/" + "a" * 300
    payload = {"results": [{"title": "t", "url": long_url, "score": 1.0}],
               "unresponsive_engines": []}

    assert long_url in web_search.render_search_payload(payload)


# --- One parameter, following memory_search ----------------------------------


def test_only_query_is_exposed(payload):
    assert WEB_SEARCH.required == ("query",)
    assert set(WEB_SEARCH.properties) == {"query"}
    for knob in ("max_results", "count", "categories", "time_range", "engines"):
        assert knob not in WEB_SEARCH.properties


def test_the_declared_timeout_sits_above_the_http_timeout_inside_it():
    """Same rule as memory_search: above what the code inside enforces, so a
    real failure surfaces as its own error rather than an opaque timeout."""
    assert WEB_SEARCH.resolved_timeout() == 15.0
    assert WEB_SEARCH.resolved_timeout() > config.searxng_timeout_seconds()
    assert WEB_SEARCH.resolved_timeout() < config.agent_tool_budget_seconds()


# --- Failures the model is told about ----------------------------------------


def test_an_empty_query_is_a_tool_error_before_any_request(bench, monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("a request was made for an empty query")

    monkeypatch.setattr(web_search.requests, "get", must_not_run)
    result = bench.dispatch("web_search", {"query": "   "})

    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert "empty" in (result.error or "")


def test_an_unreachable_searxng_says_what_to_check(bench, monkeypatch):
    def refused(*args, **kwargs):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(web_search.requests, "get", refused)
    result = bench.dispatch("web_search", {"query": "anything"})

    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert "docker ps" in (result.error or "")


def test_a_timeout_names_searxngs_own_ceiling(bench, monkeypatch):
    def slow(*args, **kwargs):
        raise requests.exceptions.Timeout("too slow")

    monkeypatch.setattr(web_search.requests, "get", slow)
    result = bench.dispatch("web_search", {"query": "anything"})

    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert "did not respond within" in (result.error or "")


def test_a_non_json_body_is_reported_as_such(bench, monkeypatch):
    class Response:
        status_code = 200
        text = "<html>gateway error</html>"

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(web_search.requests, "get", lambda *a, **k: Response())
    result = bench.dispatch("web_search", {"query": "anything"})

    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert "non-JSON" in (result.error or "")


# --- Live: the real instance --------------------------------------------------


@live_only
def test_a_live_search_against_the_real_instance(bench):
    """Real HTTP, real engines. Skips rather than fails when SearXNG is down.

    Asserts only what must hold regardless of what the internet says today:
    that it ran, that it produced the header and at least one linked result,
    and that it respected the cap.
    """
    result = bench.dispatch("web_search", {"query": "sqlite fts5 bm25 ranking"})

    assert result.outcome is ToolOutcome.OK, result.error
    assert result.ran is True
    assert "Results from a web search" in result.value
    assert "https://" in result.value
    assert len(result.value) < config.agent_max_tool_result_chars()

    numbered = re.findall(r"^\d+\. ", result.value, flags=re.MULTILINE)
    assert 1 <= len(numbered) <= config.searxng_max_results()


@live_only
def test_the_live_instance_is_bound_to_loopback_only():
    """PROJECT.md is LAN-only for the frontend; that is not a licence for every
    service this build stands up to be LAN-reachable too."""
    import socket
    import subprocess

    ports = subprocess.run(
        ["docker", "ps", "--filter", "name=searxng-core", "--format", "{{.Ports}}"],
        capture_output=True, text=True,
    )
    if ports.returncode != 0:
        pytest.skip("docker not available to inspect the binding")

    assert "127.0.0.1:8080" in ports.stdout, ports.stdout
    assert "0.0.0.0:8080" not in ports.stdout, (
        f"SearXNG is bound to all interfaces: {ports.stdout.strip()}"
    )

    # gethostbyname(gethostname()) returns 127.x on this machine, which would
    # make this test skip silently — exactly when it is most worth running.
    # Opening a UDP socket toward a routable address reveals the interface the
    # kernel would use, and sends no packets.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 1))  # TEST-NET-1, never routed
            lan_ip = probe.getsockname()[0]
        except OSError:
            pytest.skip("no route to determine this machine's LAN address")
    if lan_ip.startswith("127."):
        pytest.skip("no non-loopback address to test against")
    with socket.socket() as probe:
        probe.settimeout(3)
        assert probe.connect_ex((lan_ip, 8080)) != 0, (
            f"SearXNG answered on {lan_ip}:8080 — it is reachable from the LAN"
        )
