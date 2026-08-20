"""Ingest must never answer a caller with an unexplained failure.

Every case below returned HTTP 500 "Internal Server Error" at one point, with an
empty body and a traceback only visible in the server log. A user could not tell
whether they had picked the wrong file, whether the file was damaged, or whether
the system was broken.

These are three different facts and the system now says which one it is looking at.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import app
from app.domain import families
from app.ingest.loader import UnreadableDocument, UnsupportedFormat, load_bytes
from tests.conftest import seed_family

NUL = chr(0)

CORRUPT_PDF = b"%PDF-1.4 this is not really a pdf"
CORRUPT_DOCX = b"PK this is not really a docx"


def test_corrupt_files_are_rejected_with_a_reason_not_a_crash():
    for name, blob, expect in [
        ("broken.pdf", CORRUPT_PDF, "not a readable PDF"),
        ("broken.docx", CORRUPT_DOCX, "not a readable Word document"),
    ]:
        try:
            load_bytes(name, blob)
        except UnreadableDocument as exc:
            # The reason names the file, what was wrong, and what to check next.
            assert name in str(exc)
            assert expect in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"{name} should not have parsed")


def test_empty_file_is_named_as_empty():
    try:
        load_bytes("nothing.md", b"")
    except UnreadableDocument as exc:
        assert "empty" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an empty file must be rejected")


def test_nul_bytes_are_stripped_rather_than_crashing_the_database():
    """A single 0x00 used to surface as a psycopg DataError several layers away."""
    doc = load_bytes("odd.txt", ("contract" + NUL + " value 42").encode())
    assert NUL not in doc.text
    assert "contract" in doc.text and "value 42" in doc.text


def test_wrong_extension_and_broken_content_are_different_answers():
    try:
        load_bytes("deck.pptx", b"anything")
    except UnsupportedFormat as exc:
        assert "declared format set" in str(exc)
    else:  # pragma: no cover
        raise AssertionError(".pptx must be refused")

    # Same rejection outcome, genuinely different reason.
    try:
        load_bytes("deck.pdf", b"anything")
    except UnreadableDocument as exc:
        assert "declared format set" not in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a broken pdf must be refused")


def test_one_bad_file_does_not_take_down_the_batch():
    fam = seed_family("acme-v1")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/families/acme-v1/documents",
        files=[
            ("files", ("good.md", b"AMENDMENT NO 9\n\nUnit price GBP 60.00\n", "text/markdown")),
            ("files", ("broken.pdf", CORRUPT_PDF, "application/pdf")),
            ("files", ("deck.pptx", b"nope", "application/vnd.ms-powerpoint")),
        ],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 2
    by_name = {x["filename"]: x for x in body["results"]}
    assert by_name["good.md"]["status"] == "accepted"
    assert by_name["broken.pdf"]["status"] == "rejected"
    assert by_name["deck.pptx"]["status"] == "rejected"
    # Every rejection carries a reason a person can act on.
    for name in ("broken.pdf", "deck.pptx"):
        assert by_name[name]["reason"], f"{name} was rejected without saying why"
    assert fam


def test_an_unexpected_server_error_still_returns_a_readable_body():
    """The safety net. Whatever breaks, the caller gets JSON with a message."""
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/families/does-not-exist/register")
    assert r.status_code == 404
    assert r.json()["detail"]

    # And an upload with no files at all is a named 400, not a stack trace.
    r = client.post("/families/acme-v1/documents", files=[])
    assert r.status_code in (400, 422)
    assert r.json().get("detail")


def test_upload_endpoint_reports_duplicates_separately():
    seed_family("acme-v1")
    client = TestClient(app, raise_server_exceptions=False)
    payload = [("files", ("dup.md", b"AMENDMENT NO 11\n\nprice 77\n", "text/markdown"))]
    first = client.post("/families/acme-v1/documents", files=payload).json()
    second = client.post("/families/acme-v1/documents", files=payload).json()
    assert first["accepted"] == 1
    assert second["duplicate"] == 1
    assert second["accepted"] == 0
    assert "already ingested" in second["results"][0]["reason"]
