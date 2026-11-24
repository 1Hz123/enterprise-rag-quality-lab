from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader

from app.knowledge.loader import DocumentLoadError, load_bytes


def _make_pdf(text: str) -> bytes:
    """Create a tiny one-page text PDF without an extra test dependency."""

    stream = f"BT\n/F1 18 Tf\n72 720 Td\n({text}) Tj\nET\n".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(output)


def test_text_pdf_is_loaded_with_page_metadata():
    pdf = _make_pdf("Annual leave is 10 days.")
    loaded = load_bytes(
        "policy.pdf",
        pdf,
        content_type="application/pdf",
        max_bytes=1,
        max_pdf_bytes=len(pdf) + 1,
    )

    assert "Annual leave is 10 days." in loaded.content
    assert loaded.content_type == "application/pdf"
    assert loaded.metadata["page_count"] == 1
    assert loaded.metadata["encoding"] == "pdf-text"
    assert loaded.source == "policy.pdf"


def test_pdf_upload_endpoint(client):
    pdf = _make_pdf("Annual leave is 10 days.")
    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("policy.pdf", pdf, "application/pdf")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["content_type"] == "application/pdf"
    assert "Annual leave is 10 days." in payload["content"]
    assert payload["metadata"]["page_count"] == 1


def test_scanned_pdf_without_text_is_rejected():
    pdf = _make_pdf("")
    # Confirm the fixture really is a parseable but textless PDF.
    assert len(PdfReader(BytesIO(pdf)).pages) == 1

    with pytest.raises(DocumentLoadError, match=r"OCR|no extractable text"):
        load_bytes("scan.pdf", pdf, content_type="application/pdf")
