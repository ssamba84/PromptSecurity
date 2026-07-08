import pathlib

import pytest

from app.pdf import PdfExtractionError, extract_text

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SECRET_KEY = "AKIAIOSFODNN7EXAMPLE"


def test_extracts_secret_from_pdf():
    text = extract_text((FIXTURES / "secret.pdf").read_bytes())
    assert SECRET_KEY in text


def test_clean_pdf_has_no_secret():
    text = extract_text((FIXTURES / "clean.pdf").read_bytes())
    assert SECRET_KEY not in text
    assert text.strip() != ""


def test_non_pdf_bytes_raise():
    with pytest.raises(PdfExtractionError):
        extract_text(b"this is definitely not a pdf")
