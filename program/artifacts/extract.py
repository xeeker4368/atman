"""Getting text out of an uploaded file, and deciding when not to try.

Task 2.6, design of record ``docs/INGESTION_DESIGN.md`` (I5, I7). Decision #11
sets the scope: *"text files and PDFs get full content extraction and indexing.
Office documents, OCR, image, audio, video stay metadata-only / deferred."*

Three outcomes, and the middle one is the point
-----------------------------------------------
``ExtractionStatus`` mirrors the ``artifacts.extraction_status`` CHECK:

* ``extracted`` — text was read, and there is some.
* ``metadata_only`` — no attempt was made, or an attempt found nothing. The file
  is on record; its content is not.
* ``failed`` — an attempt was made and broke.

**A PDF with no text layer lands as ``metadata_only``, never as ``extracted``
with an empty string.** A scan is an image of a page; OCR is explicitly out of
scope. Recording it as extracted-but-empty would make a file that was never read
look read, which is the same failure shape as a tool result with no trace entry.
The note says which case it was.

Why pypdf
---------
Measured against pdfplumber on a real 15-page PDF rather than argued from
reputation: 6,022 words against 2,033, because pdfplumber's default extraction
collapses inter-word spacing on that document. Extracted text feeds FTS5 and the
embedding model, both of which tokenise on words, so glued-together text would
be indexed and unfindable. PyMuPDF was excluded before quality on licensing —
AGPL-3.0, against a public repository. The full comparison is in
``docs/INGESTION_DESIGN.md`` I7.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from enum import Enum

from program import config

logger = logging.getLogger(__name__)


class ExtractionStatus(str, Enum):
    EXTRACTED = "extracted"
    METADATA_ONLY = "metadata_only"
    FAILED = "failed"


#: Types whose bytes are text already. Decoded, not parsed.
TEXT_TYPES = (
    "text/plain", "text/markdown", "text/csv", "text/x-python", "text/html",
    "application/json", "application/xml", "text/xml", "application/x-ndjson",
    "application/yaml", "text/yaml",
)

PDF_TYPE = "application/pdf"


@dataclass(frozen=True)
class Extraction:
    """What came out of a file, and whether anything did."""

    status: ExtractionStatus
    text: str = ""
    note: str | None = None
    truncated: bool = False


def detect_content_type(data: bytes, filename: str) -> str:
    """The type a file actually is, from its bytes.

    **Never the client's ``Content-Type`` header** (INGESTION_DESIGN I6): it is
    supplied by whoever is uploading, and the treatment a file gets must not be
    theirs to choose. The filename extension is a hint of the same kind and is
    used only to separate text subtypes, which have no distinguishing magic
    bytes and cannot be misused to reach a different code path — every one of
    them is decoded as text.
    """
    if data.startswith(b"%PDF-"):
        return PDF_TYPE
    for magic, mime in (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"%!PS", "application/postscript"),
        (b"PK\x03\x04", "application/zip"),  # also .docx/.xlsx/.pptx
        (b"\x1f\x8b", "application/gzip"),
        (b"OggS", "audio/ogg"),
        (b"\x00\x00\x00\x18ftyp", "video/mp4"),
        (b"\x00\x00\x00\x20ftyp", "video/mp4"),
    ):
        if data.startswith(magic):
            return mime

    # No magic. If it decodes as UTF-8 and holds no NUL, treat it as text and
    # let the extension pick the subtype.
    sample = data[:8192]
    if b"\x00" not in sample:
        try:
            sample.decode("utf-8")
        except UnicodeDecodeError:
            return "application/octet-stream"
        suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return {
            "md": "text/markdown", "markdown": "text/markdown",
            "csv": "text/csv", "json": "application/json",
            "xml": "application/xml", "yaml": "text/yaml", "yml": "text/yaml",
            "py": "text/x-python", "html": "text/html", "htm": "text/html",
        }.get(suffix, "text/plain")
    return "application/octet-stream"


def _clip(text: str) -> tuple[str, bool]:
    """Apply the extracted-character ceiling. Returns ``(text, truncated)``."""
    limit = config.ingestion_max_extracted_chars()
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def extract_pdf(data: bytes) -> Extraction:
    """Text out of a PDF, or an honest account of why there is none."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is declared
        return Extraction(
            status=ExtractionStatus.FAILED,
            note=f"pypdf is not installed: {exc}",
        )

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # An empty-password decrypt covers the common "protected but not
            # really" case; anything else stays unread rather than guessed at.
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 - reported below, not raised
                return Extraction(
                    status=ExtractionStatus.METADATA_ONLY,
                    note="the PDF is encrypted and could not be opened.",
                )
        pages = len(reader.pages)
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - a broken file is not a crash
        logger.warning("PDF extraction failed: %s: %s", type(exc).__name__, exc)
        return Extraction(
            status=ExtractionStatus.FAILED,
            note=f"the PDF could not be read: {type(exc).__name__}: {exc}",
        )

    if not text.strip():
        return Extraction(
            status=ExtractionStatus.METADATA_ONLY,
            note=(
                f"the PDF has {pages} page(s) but no text layer — it is very "
                f"likely a scan. Reading it would need OCR, which this build "
                f"does not do."
            ),
        )

    clipped, truncated = _clip(text)
    return Extraction(
        status=ExtractionStatus.EXTRACTED,
        text=clipped,
        truncated=truncated,
        note=(
            f"read {pages} page(s); text truncated at "
            f"{config.ingestion_max_extracted_chars()} characters."
            if truncated else f"read {pages} page(s)."
        ),
    )


def extract_text_file(data: bytes) -> Extraction:
    """Decode a text file. Undecodable bytes are replaced, never dropped."""
    text = data.decode("utf-8", errors="replace")
    if not text.strip():
        return Extraction(
            status=ExtractionStatus.METADATA_ONLY,
            note="the file is empty.",
        )
    clipped, truncated = _clip(text)
    return Extraction(
        status=ExtractionStatus.EXTRACTED,
        text=clipped,
        truncated=truncated,
        note=(
            f"text truncated at {config.ingestion_max_extracted_chars()} "
            f"characters." if truncated else None
        ),
    )


def extract(data: bytes, content_type: str) -> Extraction:
    """Route by content type. Decision #11 decides what is in scope."""
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime == PDF_TYPE:
        return extract_pdf(data)
    if mime in TEXT_TYPES or mime.startswith("text/"):
        return extract_text_file(data)
    return Extraction(
        status=ExtractionStatus.METADATA_ONLY,
        note=(
            f"{mime or 'this file type'} is stored but not read. Decision #11 "
            f"puts office documents, images, audio and video out of scope for "
            f"content extraction in this build."
        ),
    )
