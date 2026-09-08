"""The `web_fetch` tool, and the SSRF guard that is most of it. Task 2.5.

The guard tests are the point of this file. They assert **refusal**, and that
refusal happens *before* anything is sent or read — not that a fetch happened to
fail, which a wrong guard would also produce when the target is simply down.

Two things make that distinction real here:

* `no_requests` replaces the HTTP layer with something that raises. A test using
  it proves the URL never reached the network, rather than proving the network
  said no.
* `test_a_running_local_service_is_refused_while_being_reachable` first confirms
  SearXNG on 127.0.0.1:8080 actually answers, so the refusal cannot be mistaken
  for "nothing was listening".
"""

from __future__ import annotations

import socket

import pytest
import requests

from program import config
from program.tools import web_fetch
from program.tools.registry import ToolOutcome, ToolRegistry
from program.tools.web_fetch import (
    WEB_FETCH,
    FetchedPage,
    UnsafeURLError,
    WebFetchError,
)

PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def bench() -> ToolRegistry:
    return ToolRegistry([WEB_FETCH])


@pytest.fixture
def no_requests(monkeypatch):
    """Any HTTP call is a test failure: the guard must refuse before the wire."""
    attempts: list[str] = []

    def explode(*args, **kwargs):
        attempts.append(str(args))
        raise AssertionError(
            f"an HTTP request was made for a URL that must have been refused: "
            f"{args!r}"
        )

    monkeypatch.setattr(requests.Session, "get", explode)
    return attempts


def resolves_to(monkeypatch, *addresses: str):
    """Make every hostname resolve to exactly these addresses."""
    monkeypatch.setattr(
        web_fetch.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ],
    )


class FakeSocket:
    def __init__(self, peer: str | None):
        self._peer = peer

    def getpeername(self):
        if self._peer is None:
            raise OSError("not connected")
        return (self._peer, 443)


class FakeResponse:
    """Enough of a requests.Response to drive the fetch loop.

    `body_reads` records whether the body was ever read, which is how the
    rebinding test proves nothing was taken from the connection.
    """

    def __init__(self, peer, status_code=200, headers=None, body=b"hello",
                 has_socket=True):
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "text/plain"}
        self._body = body
        self.body_reads = 0
        sock = FakeSocket(peer) if has_socket else None
        self.raw = type("Raw", (), {"_connection": type("C", (), {"sock": sock})()})()

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    @property
    def is_permanent_redirect(self):
        return self.status_code in (301, 308)

    def iter_content(self, chunk_size=1):
        self.body_reads += 1
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


# --- Refusal proved against real local services ------------------------------


def test_a_running_local_service_is_refused_while_being_reachable(no_requests):
    """The refusal must not be "nothing was listening" wearing a guard's clothes.

    SearXNG really is on 127.0.0.1:8080 — confirmed in this test before the
    assertion — and web_fetch still refuses it, without a request being made.
    """
    probe = socket.socket()
    probe.settimeout(2)
    reachable = probe.connect_ex(("127.0.0.1", 8080)) == 0
    probe.close()
    if not reachable:
        pytest.skip("no local service on 127.0.0.1:8080 to prove refusal against")

    with pytest.raises(UnsafeURLError, match="loopback"):
        web_fetch.fetch("http://127.0.0.1:8080/search?q=test")

    assert no_requests == [], "a request was attempted at a loopback address"


def test_the_cloud_metadata_address_is_refused(no_requests):
    with pytest.raises(UnsafeURLError, match="link-local"):
        web_fetch.fetch("http://169.254.169.254/latest/meta-data/")

    assert no_requests == []


def test_the_ollama_api_on_this_machine_is_refused(no_requests):
    """The most valuable thing on this host to an injected instruction."""
    with pytest.raises(UnsafeURLError):
        web_fetch.fetch("http://127.0.0.1:11434/api/tags")

    assert no_requests == []


def test_another_device_on_the_lan_is_refused(no_requests):
    with pytest.raises(UnsafeURLError, match="private"):
        web_fetch.fetch("http://192.168.0.1/")

    assert no_requests == []


@pytest.mark.parametrize(
    "address",
    ["10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.0.82", "127.0.0.1",
     "169.254.169.254", "0.0.0.0", "100.64.0.1", "224.0.0.1"],
)
def test_every_blocked_ipv4_range_is_refused(address):
    assert web_fetch.blocked_reason(address) is not None


@pytest.mark.parametrize("address", ["::1", "fe80::1", "fc00::1", "fd00::1", "::"])
def test_every_blocked_ipv6_range_is_refused(address):
    assert web_fetch.blocked_reason(address) is not None


@pytest.mark.parametrize("address", ["8.8.8.8", "93.184.216.34", "172.32.0.1",
                                     "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address):
    """The guard has to let the internet through, or it is just an off switch."""
    assert web_fetch.blocked_reason(address) is None


def test_an_ipv4_mapped_ipv6_loopback_is_unwrapped_and_refused():
    """::ffff:127.0.0.1 is the classic way past a v4-only check."""
    assert web_fetch.blocked_reason("::ffff:127.0.0.1") is not None
    assert web_fetch.blocked_reason("::ffff:169.254.169.254") is not None


def test_an_unclassifiable_address_is_refused_not_allowed():
    """Fails closed: 'cannot classify' is not 'safe'."""
    assert web_fetch.blocked_reason("not-an-ip") is not None
    assert web_fetch.blocked_reason("") is not None


# --- A hostname is not what connects to a socket -----------------------------


def test_a_public_hostname_resolving_to_a_private_address_is_refused(
    monkeypatch, no_requests
):
    """The reason the check is on the resolved address, not the name."""
    resolves_to(monkeypatch, "127.0.0.1")

    with pytest.raises(UnsafeURLError, match="loopback"):
        web_fetch.fetch("https://totally-innocent.example/")

    assert no_requests == []


def test_every_resolved_address_is_checked_not_just_the_first(
    monkeypatch, no_requests
):
    """One public and one private A record: which gets used is the resolver's
    choice, so passing on the public one would be luck, not a check."""
    resolves_to(monkeypatch, PUBLIC_IP, "10.1.2.3")

    with pytest.raises(UnsafeURLError, match="private"):
        web_fetch.fetch("https://round-robin.example/")

    assert no_requests == []


# --- Layer 3: the address actually connected to ------------------------------


def test_dns_rebinding_is_caught_after_connecting_and_before_any_body_is_read(
    monkeypatch,
):
    """The hole a resolve-then-hope check leaves.

    Resolution says public, the socket lands on 169.254.169.254. The body is
    what would reach the model, so the test asserts it was never read.
    """
    resolves_to(monkeypatch, PUBLIC_IP)
    response = FakeResponse(peer="169.254.169.254", body=b"secret metadata")
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: response)

    with pytest.raises(UnsafeURLError, match="landed on"):
        web_fetch.fetch("https://looks-fine.example/")

    assert response.body_reads == 0, "the body was read from a private address"


def test_an_unreadable_peer_address_fails_closed(monkeypatch):
    """Layer 3 reaches into private API. If that ever breaks, it must break
    loudly rather than quietly removing the layer."""
    resolves_to(monkeypatch, PUBLIC_IP)
    response = FakeResponse(peer=None, has_socket=False)
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: response)

    with pytest.raises(UnsafeURLError, match="could not be determined"):
        web_fetch.fetch("https://looks-fine.example/")

    assert response.body_reads == 0


def test_a_verified_public_peer_is_allowed_through(monkeypatch):
    """The guard must not refuse everything — that would pass every test above."""
    resolves_to(monkeypatch, PUBLIC_IP)
    response = FakeResponse(peer=PUBLIC_IP, body=b"the page")
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: response)

    page = web_fetch.fetch("https://fine.example/")

    assert page.body == b"the page"
    assert page.peer == PUBLIC_IP


# --- Layer 4: redirects, validated per hop ----------------------------------


def test_a_redirect_to_a_private_address_is_refused(monkeypatch):
    """The first URL passing every check says nothing about the second."""
    addresses = {"public.example": PUBLIC_IP, "internal.example": "127.0.0.1"}
    monkeypatch.setattr(
        web_fetch.socket, "getaddrinfo",
        lambda host, port, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "",
             (addresses.get(host, PUBLIC_IP), port))
        ],
    )
    hop = FakeResponse(peer=PUBLIC_IP, status_code=302,
                       headers={"Location": "http://internal.example/admin"})
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: hop)

    with pytest.raises(UnsafeURLError, match="loopback"):
        web_fetch.fetch("https://public.example/redirect")


def test_a_redirect_to_the_metadata_endpoint_is_refused(monkeypatch):
    resolves_to(monkeypatch, PUBLIC_IP)
    hop = FakeResponse(peer=PUBLIC_IP, status_code=301,
                       headers={"Location": "http://169.254.169.254/"})
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: hop)

    with pytest.raises(UnsafeURLError, match="link-local"):
        web_fetch.fetch("https://public.example/go")


def test_redirects_are_followed_up_to_the_limit_then_refused(monkeypatch):
    resolves_to(monkeypatch, PUBLIC_IP)
    calls = {"n": 0}

    def always_redirect(*args, **kwargs):
        calls["n"] += 1
        return FakeResponse(peer=PUBLIC_IP, status_code=302,
                            headers={"Location": f"https://example.com/{calls['n']}"})

    monkeypatch.setattr(requests.Session, "get", always_redirect)

    with pytest.raises(WebFetchError, match="redirects"):
        web_fetch.fetch("https://example.com/start")

    assert calls["n"] == config.web_fetch_max_redirects() + 1


def test_a_single_redirect_is_followed_and_reported(monkeypatch):
    resolves_to(monkeypatch, PUBLIC_IP)
    responses = [
        FakeResponse(peer=PUBLIC_IP, status_code=301,
                     headers={"Location": "https://example.com/final"}),
        FakeResponse(peer=PUBLIC_IP, body=b"arrived"),
    ]
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: responses.pop(0))

    page = web_fetch.fetch("http://example.com/start")

    assert page.body == b"arrived"
    assert page.final_url == "https://example.com/final"
    assert len(page.hops) == 1
    assert "redirected from" in web_fetch.render_page(page)


def test_requests_own_redirect_following_is_disabled(monkeypatch):
    """If requests followed redirects, hops would never reach our validation."""
    resolves_to(monkeypatch, PUBLIC_IP)
    seen = {}

    def capture(self, url, **kwargs):
        seen.update(kwargs)
        return FakeResponse(peer=PUBLIC_IP)

    monkeypatch.setattr(requests.Session, "get", capture)
    web_fetch.fetch("https://example.com/")

    assert seen["allow_redirects"] is False
    assert seen["stream"] is True, "layer 3 needs the socket before the body"


# --- Layer 0 and 1 -----------------------------------------------------------


def test_proxy_environment_is_ignored(monkeypatch):
    """With trust_env on, HTTP_PROXY sends the connection somewhere we never
    validated — which would make every other layer decoration."""
    resolves_to(monkeypatch, PUBLIC_IP)
    sessions = []
    original = requests.Session.get

    def capture(self, url, **kwargs):
        sessions.append(self)
        return FakeResponse(peer=PUBLIC_IP)

    monkeypatch.setattr(requests.Session, "get", capture)
    web_fetch.fetch("https://example.com/")

    assert sessions and sessions[0].trust_env is False
    assert original is not None


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/",
     "data:text/html,<b>x</b>", "javascript:alert(1)", "ws://example.com/"],
)
def test_non_http_schemes_are_refused_by_name(url, no_requests):
    with pytest.raises(UnsafeURLError, match="scheme is not fetchable"):
        web_fetch.fetch(url)

    assert no_requests == []


def test_a_url_with_no_scheme_or_host_is_refused(no_requests):
    with pytest.raises(UnsafeURLError, match="no scheme"):
        web_fetch.fetch("example.com/page")
    with pytest.raises(UnsafeURLError, match="no host"):
        web_fetch.fetch("http:///page")


def test_an_empty_url_is_a_tool_error(bench, no_requests):
    result = bench.dispatch("web_fetch", {"url": "   "})

    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert "empty" in (result.error or "")


# --- Content handling --------------------------------------------------------


def page_of(body: bytes, content_type: str) -> FetchedPage:
    return FetchedPage(url="https://example.com/x", final_url="https://example.com/x",
                       status_code=200, content_type=content_type, body=body)


def test_html_is_extracted_to_text_not_dumped_as_markup():
    html = b"""<html><head><title>The Title</title>
    <style>body{color:red}</style></head><body>
    <script>var secret = "should not appear";</script>
    <nav>Home About</nav><p>The actual sentence.</p>
    <footer>copyright</footer></body></html>"""

    text = web_fetch.render_page(page_of(html, "text/html"))

    assert "The actual sentence." in text
    assert "Title: The Title" in text
    assert "should not appear" not in text
    assert "color:red" not in text
    assert "<p>" not in text


def test_plain_text_is_used_as_is():
    text = web_fetch.render_page(page_of(b"just some text", "text/plain"))
    assert "just some text" in text


def test_json_is_used_as_is():
    text = web_fetch.render_page(page_of(b'{"a": 1}', "application/json"))
    assert '{"a": 1}' in text


def test_a_pdf_is_reported_not_pretended():
    """Claiming to have read a PDF and returning nothing would be worse than
    saying plainly that this tool does not read them."""
    text = web_fetch.render_page(page_of(b"%PDF-1.7 binary", "application/pdf"))

    assert "PDF" in text
    assert "does not extract text from PDFs" in text
    assert "%PDF" not in text


@pytest.mark.parametrize(
    "content_type", ["image/png", "video/mp4", "application/octet-stream", ""],
)
def test_binary_and_unknown_types_are_described_not_dumped(content_type):
    text = web_fetch.render_page(page_of(b"\x89PNG\r\n\x1a\n\x00binary", content_type))

    assert "not a readable page" in text
    assert "binary" not in text.replace("binary content", "")


def test_malformed_html_does_not_take_the_fetch_down():
    text = web_fetch.render_page(page_of(b"<html><p>text<<<>>", "text/html"))
    assert "text" in text


# --- The render budget -------------------------------------------------------


def test_the_render_stays_under_the_loops_truncation_cap_even_when_pathological():
    """A long URL is printed twice on a redirect, plus a long title. A fixed
    body cap plus an unbounded header can exceed the loop's cap; measuring the
    header first cannot."""
    long_url = "https://example.com/" + "a" * 380
    page = FetchedPage(
        url="https://start.example/x", final_url=long_url, status_code=200,
        content_type="text/html",
        body=(f"<html><head><title>{'T' * 300}</title></head><body>"
              + "word " * 5000 + "</body></html>").encode(),
        hops=["https://start.example/x"], truncated_download=True,
    )

    rendered = web_fetch.render_page(page)

    assert len(rendered) < config.agent_max_tool_result_chars()
    assert "Truncated to the first" in rendered


def test_an_ordinary_page_gets_the_configured_cap_not_a_squeezed_one():
    page = page_of(("word " * 5000).encode(), "text/plain")

    rendered = web_fetch.render_page(page)

    assert config.web_fetch_max_text_chars() <= len(rendered)
    assert len(rendered) < config.agent_max_tool_result_chars()


def test_the_download_is_capped_in_bytes(monkeypatch):
    resolves_to(monkeypatch, PUBLIC_IP)
    monkeypatch.setenv("ANAM_WEB_FETCH_MAX_DOWNLOAD_BYTES", "100")
    config.reload()
    response = FakeResponse(peer=PUBLIC_IP, body=b"x" * 5000)
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: response)

    page = web_fetch.fetch("https://example.com/big")

    assert len(page.body) == 100
    assert page.truncated_download is True
    assert "Only the first" in web_fetch.render_page(page)


# --- Parameters and the declared timeout -------------------------------------


def test_only_url_is_exposed():
    assert WEB_FETCH.required == ("url",)
    assert set(WEB_FETCH.properties) == {"url"}
    for knob in ("selector", "extract", "hint", "max_chars", "follow_redirects"):
        assert knob not in WEB_FETCH.properties


def test_the_declared_timeout_sits_above_the_fetch_budget_inside_it():
    assert WEB_FETCH.resolved_timeout() == 25.0
    assert WEB_FETCH.resolved_timeout() > config.web_fetch_total_timeout_seconds()
    assert WEB_FETCH.resolved_timeout() < config.agent_tool_budget_seconds()


def test_an_http_error_is_reported_with_its_status(monkeypatch, bench):
    resolves_to(monkeypatch, PUBLIC_IP)
    monkeypatch.setattr(
        requests.Session, "get",
        lambda *a, **k: FakeResponse(peer=PUBLIC_IP, status_code=404),
    )

    result = bench.dispatch("web_fetch", {"url": "https://example.com/missing"})

    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert "404" in (result.error or "")


# --- Live: a real page over the real internet --------------------------------


def internet_is_reachable() -> bool:
    try:
        requests.get("https://example.com/", timeout=5)
        return True
    except requests.exceptions.RequestException:
        return False


@pytest.mark.skipif(
    not internet_is_reachable(), reason="no internet; live fetch skipped"
)
def test_a_live_fetch_of_a_real_page(bench):
    """Real DNS, real TLS, real socket, real peer check."""
    result = bench.dispatch("web_fetch", {"url": "https://example.com/"})

    assert result.outcome is ToolOutcome.OK, result.error
    assert "Example Domain" in result.value
    assert "<html" not in result.value.lower()
    assert len(result.value) < config.agent_max_tool_result_chars()
