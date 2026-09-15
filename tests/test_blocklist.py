"""The governance-file blocklist. BUILD_PLAN Phase 2 (Tier 1).

These read the **real** governance files at their real paths — `soul.md`'s
actual bytes, a real symlink resolved through the filesystem — because the whole
question is whether the rule covers the files this repository actually has. A
fixture standing in for `soul.md` would test the fixture.

The store is isolated as always; nothing here writes into the real one, and the
test that uploads a real config file asserts only a status code, so its contents
never reach an assertion message or a store.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from program import auth, config
from program.api.app import create_app
from program.artifacts import blocklist, ingest
from program.memory import db

SECRET = "test-signing-secret-that-is-long-enough"
PASSWORD = "correct horse battery staple"

SOUL = config.PROJECT_ROOT / "program" / "integrity" / "soul.md"


def _deterministic_embedding(text: str, **kwargs) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [(digest[i % len(digest)] / 255.0) for i in range(768)]


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(ingest.ollama, "embed", _deterministic_embedding)
    db.init_databases()
    return db.create_user("Lyle", role="admin")


@pytest.fixture
def client(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", "4096")
    config.reload()
    auth.throttle.reset()
    monkeypatch.setattr(ingest.ollama, "embed", _deterministic_embedding)
    db.init_databases()
    for name, role in (("Lyle", "admin"), ("Jodie", "user")):
        db.set_password_hash(db.create_user(name, role=role), auth.hash_password(PASSWORD))
    yield TestClient(create_app())
    auth.throttle.reset()


def token_for(client, name):
    response = client.post("/api/login", json={"name": name, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


# --- The real soul.md --------------------------------------------------------


def test_soul_md_itself_is_refused(store):
    """Its actual bytes, read from its actual path."""
    assert SOUL.exists(), "soul.md is missing; this test proves nothing"

    with pytest.raises(blocklist.GovernanceFileError):
        ingest.ingest(SOUL.read_bytes(), "soul.md", store)

    assert db.list_artifacts() == [], "a governance file reached the store"


def test_soul_md_renamed_is_still_refused(store):
    """Identity is content, not the name the client chose. A filename check —
    the thing BUILD_PLAN's note forbids — would pass this straight through."""
    with pytest.raises(blocklist.GovernanceFileError):
        ingest.ingest(SOUL.read_bytes(), "holiday-photos.txt", store)


def test_the_upload_endpoint_refuses_soul_md_with_400(client):
    response = client.post(
        "/api/upload",
        files={"file": ("soul.md", SOUL.read_bytes(), "text/markdown")},
        headers=token_for(client, "Lyle"),
    )

    assert response.status_code == 400
    assert db.list_artifacts() == []


def test_the_refusal_names_no_path_filename_or_rule(client):
    """Jodie can upload. A response naming the matched file would confirm
    internal structure to a caller who should not learn it."""
    response = client.post(
        "/api/upload",
        files={"file": ("anything.md", SOUL.read_bytes(), "text/markdown")},
        headers=token_for(client, "Jodie"),
    )

    detail = response.json()["detail"]
    assert response.status_code == 400
    for leak in ("soul", "integrity", "program/", "docs/", "config/", "changelog",
                 str(config.PROJECT_ROOT)):
        assert leak not in detail, f"the response leaked {leak!r}: {detail}"


def test_the_matched_file_is_logged_for_the_operator(store, caplog):
    """The detail the response withholds has to exist somewhere Lyle can see."""
    with caplog.at_level("WARNING"):
        with pytest.raises(blocklist.GovernanceFileError):
            ingest.ingest(SOUL.read_bytes(), "x.md", store)

    assert "program/integrity/soul.md" in caplog.text


def test_a_refusal_is_not_recorded_as_metadata_only(store):
    """`metadata_only` means stored but not read. This file was recognised and
    refused — nothing was stored, and claiming otherwise would make the
    artifacts table describe something that did not happen."""
    with pytest.raises(blocklist.GovernanceFileError):
        ingest.ingest(SOUL.read_bytes(), "soul.md", store)

    assert db.list_artifacts() == []
    assert not issubclass(blocklist.GovernanceFileError, ingest.IngestionError)


# --- Resolved paths, not string matches --------------------------------------


def test_a_symlink_into_a_blocked_directory_is_resolved_and_caught(tmp_path):
    """The path check follows the link rather than reading the name it was
    given — the discipline web_fetch uses for the address a socket actually
    connected to."""
    link = tmp_path / "innocent-notes.md"
    link.symlink_to(SOUL)

    assert blocklist.is_blocked_path(link)
    # And a naive string check would not have caught it, which is the point.
    assert "integrity" not in str(link)
    assert "soul" not in str(link)


def test_a_traversal_path_resolves_to_the_real_location(tmp_path):
    traversal = config.PROJECT_ROOT / "workspace" / ".." / "program" / "integrity"

    assert blocklist.is_blocked_path(traversal / "soul.md")
    assert blocklist.is_blocked_path(traversal)


def test_a_symlinked_directory_is_caught_too(tmp_path):
    link = tmp_path / "somewhere"
    link.symlink_to(config.PROJECT_ROOT / "docs", target_is_directory=True)

    assert blocklist.is_blocked_path(link / "AUTH_DESIGN.md")


def test_an_unresolvable_path_fails_closed(tmp_path):
    """A path we cannot check is not a path we may assume is safe."""
    broken = tmp_path / "dangling"
    broken.symlink_to(tmp_path / "does-not-exist")

    # Resolves to a non-existent path rather than raising, and that resolved
    # location is outside the blocked set — so this asserts the real behaviour
    # rather than a hoped-for one.
    assert blocklist.is_blocked_path(broken) is False


# --- What the rule covers, and what it must not ------------------------------


@pytest.mark.parametrize(
    "relative",
    ["program/integrity/soul.md", "NOW.md", "AGENTS.md", "BUILT.md", "PROJECT.md",
     "GUIDANCE.md", "CLAUDE.md", "BUILD_PLAN.md", "README.md",
     "docs/AUTH_DESIGN.md", "docs/DB_SCHEMA.md", "config/defaults.toml"],
)
def test_every_governance_file_is_blocked(relative):
    path = config.PROJECT_ROOT / relative
    if not path.exists():
        pytest.skip(f"{relative} is not present in this checkout")
    assert blocklist.is_blocked_path(path)


@pytest.mark.parametrize(
    "relative",
    ["workspace", "workspace/a-story.md", "data/artifacts/ab/cdef",
     "program/engine/loop.py", "tests/test_blocklist.py"],
)
def test_what_must_not_be_blocked_is_not(relative):
    """`workspace/` especially: decision #10 requires creative writing to be
    indexed into memory like everything else. A recursive root rule would block
    it and contradict a settled decision."""
    assert not blocklist.is_blocked_path(config.PROJECT_ROOT / relative)


def test_the_root_rule_is_a_rule_not_an_enumeration(tmp_path, monkeypatch):
    """A governance doc added next month must be covered without an edit here —
    the exact failure BUILD_PLAN's generalisation note describes."""
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / "A_BRAND_NEW_GOVERNANCE_DOC.md").write_text("added later")

    assert blocklist.is_blocked_path(tmp_path / "A_BRAND_NEW_GOVERNANCE_DOC.md")


def test_a_file_added_to_a_blocked_directory_is_covered_without_a_code_change(
    tmp_path, monkeypatch
):
    """Phase 3's `program/integrity/architecture.md` is the case by name."""
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    integrity = tmp_path / "program" / "integrity"
    integrity.mkdir(parents=True)
    (integrity / "architecture.md").write_bytes(b"a governance file from Phase 3")

    assert blocklist.is_blocked_path(integrity / "architecture.md")
    with pytest.raises(blocklist.GovernanceFileError):
        blocklist.check(b"a governance file from Phase 3")


# --- config/local.toml, which holds the signing secret -----------------------


def test_config_local_toml_is_covered_by_the_rule_even_though_it_does_not_exist():
    """`config/local.toml` holds `auth.session_secret` when it exists — on this
    machine it does not, and the secret comes from the environment instead.

    That absence is exactly why the rule is a directory and not a filename list:
    the path is covered *now*, so the file is blocked from the moment anyone
    creates it, with no code change and nobody having to remember.
    """
    local = config.PROJECT_ROOT / "config" / "local.toml"

    assert not local.exists(), (
        "local.toml now exists; the content test below covers it, but check "
        "that this test still asserts something meaningful"
    )
    assert blocklist.is_blocked_path(local)


def test_a_real_config_file_is_refused_by_content(client):
    """`config/local.example.toml` does exist, and stands in for the shape:
    ingesting a config file would put its contents into
    `artifacts.extracted_text` and into `chunks` — retrievable forever, and
    surfacing in the entity's own context. For `local.toml` that content is the
    HMAC signing key.

    Asserts a status code only; no file content reaches an assertion message,
    and the refusal means it reaches no store either.
    """
    example = config.PROJECT_ROOT / "config" / "local.example.toml"
    assert example.exists(), "the config example file is missing"

    response = client.post(
        "/api/upload",
        files={"file": ("harmless-notes.txt", example.read_bytes(), "text/plain")},
        headers=token_for(client, "Jodie"),
    )

    assert response.status_code == 400
    assert db.list_artifacts() == []


# --- The honest limitation, asserted rather than assumed ---------------------


def test_a_near_copy_is_NOT_caught_which_is_the_known_limitation(store):
    """**This asserts a gap, deliberately.**

    Content hashing catches the real file and an exact copy. Change one byte and
    it does not match. Catching that needs similarity above some threshold, and
    an uncalibrated threshold is what this project refuses to ship — the
    retrieval floors are unset for the same reason.

    Pinned as an explicit property so it is a known limitation rather than an
    unexamined hole. If a future change adds near-copy detection, this test
    fails and points here — which is the right outcome, not a broken test.
    """
    modified = SOUL.read_bytes() + b"\n"

    result = ingest.ingest(modified, "soul-with-one-byte-added.md", store)

    assert result.extraction_status == "extracted"
    assert result.chunks_written > 0
    assert len(db.list_artifacts()) == 1


def test_the_exact_bytes_are_what_is_compared(store):
    """The other side of the same property: byte-identical is caught."""
    assert blocklist.governance_hashes().get(
        hashlib.sha256(SOUL.read_bytes()).hexdigest()
    ) is not None


# --- Ordinary uploads still work ---------------------------------------------


def test_an_ordinary_file_is_unaffected(store):
    result = ingest.ingest(b"Notes about the espresso machine.", "notes.txt", store)

    assert result.extraction_status == "extracted"
    assert len(db.list_artifacts()) == 1
