"""Owned-logic tests for the PDF math normalizer.

The OCR itself is Mathpix's correctness, proven out-of-band. What this module owns, and
what these tests prove, is: locating the annotation's region as the bounding box of the
selected text -- every line it spans, at the text column's width -- so the right slice of
the page is what gets OCR'd, and trimming the OCR back to the selection. On a locating
miss (or an out-of-range page or empty OCR) it raises rather than OCR the wrong region or
return raw. Region logic runs against a real PyMuPDF document with text at known
positions: a real boundary, no network, no OCR.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
import requests

from h.services import pdf_math
from h.services.pdf_math import MathRecoveryError


def _doc_with_lines(lines: list[tuple[float, str]]) -> fitz.Document:
    """Build a one-page PDF with each ``(baseline_y, text)`` drawn at the left margin."""
    doc = fitz.open()
    page = doc.new_page()
    for y, text in lines:
        page.insert_text((72, y), text, fontsize=11)
    return doc


def test_quote_rect_spans_every_line_the_selection_covers():
    # The bug this guards against: a three-line selection cropped to only its middle line,
    # dropping the line the math sits on. The box must cover all three.
    doc = _doc_with_lines(
        [
            (100, "The moduli space M of Enriques surfaces is an open subset of a 10-"),
            (120, "dimensional orthogonal modular variety, which was shown by Kondo"),
            (140, "to be rational. This description is obtained by considering"),
        ]
    )
    page = doc[0]
    exact = (
        "The moduli space M of Enriques surfaces is an open subset of a 10- "
        "dimensional orthogonal modular variety, which was shown by Kondo "
        "to be rational."
    )
    rect = pdf_math._quote_rect(page, exact)  # noqa: SLF001
    assert rect is not None
    captured = page.get_textbox(rect)
    assert "moduli space" in captured  # first line
    assert "orthogonal modular" in captured  # middle line
    assert "to be rational" in captured  # last line


def test_quote_rect_starts_at_the_first_occurrence_when_a_leading_word_repeats():
    # The bug this guards against: the first quote word ("Enriques") recurs near the end of
    # the selection, and picking its last occurrence collapsed the box to the tail. The box
    # must start at the true beginning, so the opening line is included.
    doc = _doc_with_lines(
        [
            (100, "Enriques surfaces are quotients of K3 surfaces by involutions."),
            (120, "They occupy a place between rational and other K3 surfaces. So"),
            (140, "there are finitely many polarized Enriques surfaces of each kind."),
        ]
    )
    page = doc[0]
    exact = (
        "Enriques surfaces are quotients of K3 surfaces by involutions. "
        "They occupy a place between rational and other K3 surfaces. So "
        "there are finitely many polarized Enriques surfaces of each kind."
    )
    captured = page.get_textbox(pdf_math._quote_rect(page, exact))  # noqa: SLF001
    assert (
        "are quotients" in captured
    )  # opening line kept, not skipped to the late repeat


def test_quote_rect_is_none_when_the_selection_is_not_on_the_page():
    page = _doc_with_lines([(100, "only unrelated prose on the page")])[0]
    assert pdf_math._quote_rect(page, "text that does not appear here at all") is None  # noqa: SLF001


def test_quote_rect_uses_context_when_the_exact_ends_are_truncated():
    doc = _doc_with_lines(
        [
            (100, "Enriques surfaces are quotients of K3 surfaces by involutions."),
            (120, "They satisfy 2K ~ 0 and occupy a place between other surfaces."),
            (140, "In this paper we consider the moduli space of these surfaces."),
        ]
    )
    page = doc[0]
    exact = (
        "s surfaces are quotients of K3 surfaces by involutions. "
        "They satisfy 2K ~ 0 and occupy a place between other surfaces. "
        "In this paper we con"
    )

    rect = pdf_math._quote_rect(  # noqa: SLF001
        page,
        exact,
        prefix="Introduction Enriques",
        suffix="sider the moduli space",
    )

    assert rect is not None
    captured = page.get_textbox(rect)
    assert "Enriques surfaces are quotients" in captured
    assert "In this paper we consider" in captured


def test_quote_rect_uses_prose_context_for_a_formula_only_exact_quote():
    doc = _doc_with_lines(
        [
            (100, "On an affine subset a nonvanishing form is given by"),
            (120, "omega equals the residue formula"),
            (140, "One has the following identity"),
        ]
    )
    page = doc[0]

    rect = pdf_math._quote_rect(  # noqa: SLF001
        page,
        "omega=ResXdxdy",
        prefix="a nonvanishing form is given by",
        suffix="One has",
    )

    assert rect is not None
    assert "omega equals the residue formula" in page.get_textbox(rect)


def test_trim_to_quote_cuts_trailing_overcapture():
    # The crop is full column width, so its last line runs past the selection; the trailing
    # prose of the quote marks where to cut, and the math before it is preserved.
    exact = "the residue is some finite data attached"
    ocr = (
        "the residue is $\\omega$ some finite data attached. "
        "Unrelated trailing text here."
    )
    assert (
        pdf_math._trim_to_quote(ocr, exact)  # noqa: SLF001
        == "the residue is $\\omega$ some finite data attached."
    )


def test_clean_pdf_quote_returns_the_ocr_latex(monkeypatch):
    # The located region is OCR'd; the trimmed OCR LaTeX is what's returned (never the raw
    # text-layer quote). OCR itself is Mathpix's job, mocked here.
    doc = _doc_with_lines([(100, "the residue is some finite data attached here")])
    uri = "http://test.invalid/ok.pdf"
    pdf_math._pdf_cache[uri] = doc.tobytes()  # noqa: SLF001 - seed fetch cache: real bytes, no network
    monkeypatch.setattr(
        pdf_math,
        "ocr_latex",
        lambda _png: r"the residue is $\omega$ some finite data attached",
    )

    result = pdf_math.clean_pdf_quote(
        uri, 0, "the residue is some finite data attached"
    )

    assert result == r"the residue is $\omega$ some finite data attached"


def test_clean_pdf_quote_raises_when_page_is_out_of_range():
    doc = _doc_with_lines([(100, "single page document")])
    uri = "http://test.invalid/a.pdf"
    pdf_math._pdf_cache[uri] = doc.tobytes()  # noqa: SLF001
    with pytest.raises(MathRecoveryError, match="out of range"):
        pdf_math.clean_pdf_quote(uri, 5, "the raw exact quote")


def test_clean_pdf_quote_raises_when_region_cannot_be_located():
    doc = _doc_with_lines([(100, "some unrelated prose")])
    uri = "http://test.invalid/b.pdf"
    pdf_math._pdf_cache[uri] = doc.tobytes()  # noqa: SLF001
    # A quote that isn't on the page -> no region -> raise, never an OCR of the wrong slice.
    with pytest.raises(MathRecoveryError, match="could not be located"):
        pdf_math.clean_pdf_quote(uri, 0, "a selection that does not occur on this page")


def test_clean_pdf_quote_raises_when_ocr_is_empty(monkeypatch):
    doc = _doc_with_lines([(100, "the residue is some finite data attached here")])
    uri = "http://test.invalid/empty.pdf"
    pdf_math._pdf_cache[uri] = doc.tobytes()  # noqa: SLF001
    monkeypatch.setattr(pdf_math, "ocr_latex", lambda _png: "")
    with pytest.raises(MathRecoveryError, match="empty"):
        pdf_math.clean_pdf_quote(uri, 0, "the residue is some finite data attached")


def test_ocr_latex_raises_math_recovery_error_when_key_missing(monkeypatch):
    # A missing key is a recovery failure (rolls the create back), not a leaked config error.
    monkeypatch.delenv("MATHPIX_API_KEY", raising=False)
    with pytest.raises(MathRecoveryError, match="MATHPIX_API_KEY"):
        pdf_math.ocr_latex(b"\x89PNG")


def test_ocr_latex_wraps_mathpix_request_failure(monkeypatch):
    # A Mathpix network error / timeout / non-2xx must surface as MathRecoveryError, so it
    # rolls back and logs uniformly rather than leaking a raw requests exception.
    monkeypatch.setenv("MATHPIX_API_KEY", "test-key")

    def _boom(*_args, **_kwargs):
        msg = "mathpix unreachable"
        raise requests.ConnectionError(msg)

    monkeypatch.setattr(pdf_math.requests, "post", _boom)
    with pytest.raises(MathRecoveryError, match="Mathpix OCR request failed"):
        pdf_math.ocr_latex(b"\x89PNG")
