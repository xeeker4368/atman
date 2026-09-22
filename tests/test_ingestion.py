"""File/artifact ingestion. Task 2.6, design `docs/INGESTION_DESIGN.md`.

Real store, real migration, real chunking. Embeddings are deterministic rather
than from Ollama so the suite runs without it; one live test does the whole
thing with real embeddings and skips when Ollama is absent.

The PDFs here are **built in the test**, not fixtures — a few hundred bytes of
hand-written PDF syntax, so "a PDF with a text layer" and "a PDF without one"
are exactly what they claim to be rather than whatever a downloaded file
happens to contain.
"""

from __future__ import annotations

import hashlib

import pytest

from program import config
from program.artifacts import indexing, ingest
from program.engine import ollama
from program.memory import db, migrations, retrieval, vectors

live_only = pytest.mark.skipif(
    not ollama.is_available(), reason="Ollama is not reachable; live test skipped"
)


def _deterministic_embedding(text: str, **kwargs) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [(digest[i % len(digest)] / 255.0) for i in range(768)]


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    # The embedding call lives in `indexing` since A3 extracted the shared
    # pipeline out of `ingest._index_text`. Patched where it is made.
    monkeypatch.setattr(indexing.ollama, "embed", _deterministic_embedding)
    monkeypatch.setattr(retrieval.ollama, "embed", _deterministic_embedding)
    db.init_databases()
    return db.create_user("Lyle", role="admin")


def make_pdf(body_text: str | None) -> bytes:
    """A minimal single-page PDF. ``None`` gives a page with no text layer."""
    if body_text is None:
        content = b"q 100 0 0 100 100 600 cm /Im0 Do Q"     # an image, no text
    else:
        escaped = body_text.replace("(", r"\(").replace(")", r"\)")
        content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


# --- The migration -----------------------------------------------------------


def test_the_migration_creates_the_artifacts_table_and_the_chunk_link(store):
    with db.connection() as conn:
        # >= 2, not == 2: later migrations legitimately land on top, and this
        # test is about migration 2 having applied, not about it being the
        # latest. Pinning the number made this fail when migration 3 arrived.
        assert migrations.current_version(conn) >= 2
        names = {r["name"] for r in conn.execute("SELECT name FROM main.sqlite_master")}
        assert "artifacts" in names
        columns = {r[1] for r in conn.execute("PRAGMA table_info(chunks)")}
        assert "artifact_id" in columns


def test_the_migration_runs_on_a_fresh_store_not_only_an_upgraded_one(store):
    """working.sql stays at version 1, so every new database reaches version 2
    through the migration — which means it is exercised on every test run."""
    schema = (config.data_dir().parent / "program" / "memory" / "schema"
              / "working.sql")
    if schema.exists():
        assert "artifacts" not in schema.read_text(), (
            "the table was added to working.sql as well as the migration; it "
            "would then be created twice and ALTER TABLE would fail"
        )


def test_extraction_status_is_check_constrained(store):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.insert_artifact(
            artifact_id="x", user_id=store, filename="f", content_type="text/plain",
            size_bytes=1, sha256="d", storage_path="p", artifact_type="upload",
            extraction_status="probably_read",
        )


# --- Text ingestion, end to end ----------------------------------------------


def test_a_text_file_is_stored_extracted_chunked_and_retrievable(store):
    body = ("Espresso extraction notes. Grind size is the dominant variable; "
            "a finer grind raises resistance and slows the shot. ") * 30

    result = ingest.ingest(body.encode(), "coffee.txt", store)

    assert result.extraction_status == "extracted"
    assert result.chunks_written >= 2
    assert result.content_type == "text/plain"

    row = db.get_artifact(result.artifact_id)
    assert row["filename"] == "coffee.txt"
    assert row["extracted_text"].startswith("Espresso extraction notes.")
    assert row["sha256"] == hashlib.sha256(body.encode()).hexdigest()

    found = retrieval.search("what raises resistance in an espresso shot")
    assert found.results
    hit = found.results[0]
    assert hit.source_type == "file"
    assert hit.source_trust == "secondhand"
    assert hit.conversation_id is None


def test_document_chunks_are_sized_like_conversation_chunks(store):
    """They compete for the same retrieval slots; a document chunk twice the
    size is a bigger lexical target and a more diluted embedding."""
    result = ingest.ingest(("word " * 4000).encode(), "long.txt", store)

    sizes = [len(c["text"]) for c in db.get_artifact_chunks(result.artifact_id)]
    assert sizes
    assert max(sizes) <= config.chunk_target_chars()


def test_every_chunk_points_back_at_its_artifact(store):
    """Without the link a retrieved document chunk cannot say where it is from,
    and the message-id columns are meaningless for a document."""
    result = ingest.ingest(("paragraph. " * 900).encode(), "doc.txt", store)

    chunks = db.get_artifact_chunks(result.artifact_id)
    assert len(chunks) == result.chunks_written
    assert all(c["artifact_id"] == result.artifact_id for c in chunks)
    assert all(c["conversation_id"] is None for c in chunks)
    assert all(c["first_message_id"] is None for c in chunks)


def test_an_identical_reupload_is_recorded_not_indexed_twice(store):
    body = b"Notes about the fan noise on the Mac mini."
    first = ingest.ingest(body, "notes.txt", store)
    second = ingest.ingest(body, "notes-again.txt", store)

    assert second.artifact_id == first.artifact_id
    assert second.duplicate_of == first.artifact_id
    assert len(db.list_artifacts(store)) == 1


# --- PDFs --------------------------------------------------------------------


def test_a_pdf_with_a_text_layer_is_extracted(store):
    pdf = make_pdf("The quick brown fox jumps over the lazy dog")

    result = ingest.ingest(pdf, "doc.pdf", store)

    assert result.content_type == "application/pdf"
    assert result.extraction_status == "extracted"
    assert "quick brown fox" in db.get_artifact(result.artifact_id)["extracted_text"]
    assert result.chunks_written == 1


def test_a_pdf_with_no_text_layer_is_metadata_only_not_empty_extracted(store):
    """A scan is an image of a page. Recording it as extracted-but-empty would
    make a file that was never read look read."""
    pdf = make_pdf(None)

    result = ingest.ingest(pdf, "scan.pdf", store)

    assert result.extraction_status == "metadata_only"
    assert result.chunks_written == 0
    assert "no text layer" in result.extraction_note
    assert "OCR" in result.extraction_note
    row = db.get_artifact(result.artifact_id)
    assert row["extracted_text"] is None
    assert row["extraction_status"] == "metadata_only"


def test_a_corrupt_pdf_fails_rather_than_crashing(store):
    result = ingest.ingest(b"%PDF-1.4\nthis is not a pdf at all", "broken.pdf", store)

    assert result.extraction_status in ("failed", "metadata_only")
    assert result.extraction_note
    assert db.get_artifact(result.artifact_id) is not None, "the file is still recorded"


# --- Content-type routing (decision #11) -------------------------------------


@pytest.mark.parametrize(
    "data,expected",
    [
        (b"\x89PNG\r\n\x1a\n\x00rest", "image/png"),
        (b"\xff\xd8\xff\xe0stuff", "image/jpeg"),
        (b"PK\x03\x04zipcontents", "application/zip"),
        (b"\x00\x01\x02\x03binary", "application/octet-stream"),
    ],
)
def test_binary_types_are_stored_but_not_read(store, data, expected):
    result = ingest.ingest(data, "thing.bin", store)

    assert result.content_type == expected
    assert result.extraction_status == "metadata_only"
    assert result.chunks_written == 0
    assert "not read" in result.extraction_note


def test_the_content_type_comes_from_the_bytes_not_the_name(store):
    """The client controls the name and the upload header; neither may pick the
    code path a file gets."""
    result = ingest.ingest(make_pdf("real pdf content"), "notes.txt", store)
    assert result.content_type == "application/pdf"

    result = ingest.ingest(b"just words, no magic bytes", "photo.png", store)
    assert result.content_type == "text/plain"


def test_markdown_and_json_are_read_as_text(store):
    md = ingest.ingest(b"# Heading\n\nSome notes here.", "notes.md", store)
    js = ingest.ingest(b'{"setting": "value", "other": 2}', "conf.json", store)

    assert md.content_type == "text/markdown"
    assert js.content_type == "application/json"
    assert md.extraction_status == js.extraction_status == "extracted"


# --- Limits ------------------------------------------------------------------


def test_a_file_over_the_byte_limit_is_refused(store, monkeypatch):
    monkeypatch.setenv("ANAM_INGESTION_MAX_UPLOAD_BYTES", "100")
    config.reload()

    with pytest.raises(ingest.FileTooLargeError, match="limit"):
        ingest.ingest(b"x" * 500, "big.txt", store)

    assert db.list_artifacts(store) == []


def test_extraction_is_truncated_not_refused_over_the_character_limit(
    store, monkeypatch
):
    """A book-length PDF should be partly ingested with the truncation on the
    record, not rejected outright."""
    monkeypatch.setenv("ANAM_INGESTION_MAX_EXTRACTED_CHARS", "500")
    config.reload()

    result = ingest.ingest(("word " * 2000).encode(), "long.txt", store)

    assert result.extraction_status == "extracted"
    assert result.truncated is True
    assert result.chars_extracted == 500
    assert "truncated" in result.extraction_note
    assert len(db.get_artifact(result.artifact_id)["extracted_text"]) == 500


def test_an_empty_file_is_refused(store):
    with pytest.raises(ingest.IngestionError, match="empty"):
        ingest.ingest(b"", "nothing.txt", store)


# --- Security properties (I6) ------------------------------------------------


def test_the_client_filename_never_becomes_a_path(store):
    hostile = "../../program/integrity/soul.md"

    result = ingest.ingest(b"attempted traversal", hostile, store)

    row = db.get_artifact(result.artifact_id)
    assert row["filename"] == hostile, "the name is kept for display"
    assert ".." not in row["storage_path"]
    assert row["storage_path"].endswith(result.artifact_id)

    stored = config.artifact_dir() / row["storage_path"]
    assert stored.exists()
    assert config.artifact_dir().resolve() in stored.resolve().parents


def test_a_stored_file_lands_under_the_artifact_directory_only(store):
    result = ingest.ingest(b"contents", "ordinary.txt", store)
    row = db.get_artifact(result.artifact_id)

    assert not row["storage_path"].startswith("/")
    assert (config.artifact_dir() / row["storage_path"]).read_bytes() == b"contents"


def test_the_uploader_is_recorded_on_the_artifact_and_its_chunks(store):
    jodie = db.create_user("Jodie", role="user")

    result = ingest.ingest(("sentence. " * 400).encode(), "hers.txt", jodie)

    assert db.get_artifact(result.artifact_id)["user_id"] == jodie
    assert all(c["user_id"] == jodie for c in db.get_artifact_chunks(result.artifact_id))


def test_no_capability_is_registered_for_uploading(store):
    """Both household users may upload, so role draws no line and a capability
    would always return True — a gate mounted on nothing."""
    from program.settings import permissions

    names = {c.name for c in permissions.CAPABILITIES}
    assert not any("upload" in n or "artifact" in n or "ingest" in n for n in names)


# --- Failure leaves nothing half-indexed -------------------------------------


def test_an_unreachable_embedder_leaves_no_chunks(store, monkeypatch):
    """Embed-before-write: the model failing must not leave a half-indexed
    document behind."""
    def unreachable(text, **kwargs):
        raise ollama.OllamaUnreachable("nothing is listening")

    monkeypatch.setattr(indexing.ollama, "embed", unreachable)

    with pytest.raises(ollama.OllamaUnreachable):
        ingest.ingest(("paragraph. " * 900).encode(), "doc.txt", store)

    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 0


# --- Live --------------------------------------------------------------------


@live_only
def test_a_live_ingestion_with_real_embeddings(isolated_data_dir):
    vectors.reset_vector_store()
    db.init_databases()
    user_id = db.create_user("Lyle", role="admin")
    body = ("Pour-over coffee needs a cone, filters, a kettle you can pour "
            "slowly from, and a grinder. The grinder matters most. ") * 20

    result = ingest.ingest(body.encode(), "pourover.txt", user_id)
    found = retrieval.search("what equipment do I need for pour-over coffee")

    assert result.chunks_written > 0
    assert found.results
    assert found.vector.ran and found.vector.kept > 0
    assert any(c.source_type == "file" for c in found.results)
