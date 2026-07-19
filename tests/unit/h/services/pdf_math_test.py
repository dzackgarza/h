"""Owned-logic tests for the PDF math normalizer.

The OCR itself is Mathpix's correctness, proven out-of-band. What this module owns, and
what these tests prove, is: locating the annotation's region as the bounding box of the
selected text -- every line it spans, at the text column's width -- so the right slice of
the page is what gets OCR'd, and trimming the OCR back to the selection. On a locating
miss, the honest raw quote is kept instead of the wrong region. Region logic runs against
a real PyMuPDF document with text at known positions: a real boundary, no network, no OCR.
"""

from __future__ import annotations

import fitz

from h.services import pdf_math


def test_pdf_has_math_fires_on_operators_but_not_plain_prose():
    # Real quotes: an inline relation carries a math operator; a plain sentence does not.
    assert pdf_math.pdf_has_math(
        "They satisfy 2K ∼ 0 and q = 0"
    )  # ∼ is a math operator
    assert pdf_math.pdf_has_math("the involution ιdP on X")  # ι is Greek
    assert not pdf_math.pdf_has_math(
        "parameterizes the same surfaces, with finite data attached"
    )


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


def test_trim_to_quote_cuts_trailing_overcapture():
    # The crop is full column width, so its last line runs past the selection; the trailing
    # prose of the quote marks where to cut, and the math before it is preserved.
    exact = "the residue is some finite data attached"
    ocr = (
        "the residue is \\(\\omega\\) some finite data attached. "
        "Unrelated trailing text here."
    )
    assert (
        pdf_math._trim_to_quote(ocr, exact)  # noqa: SLF001
        == "the residue is \\(\\omega\\) some finite data attached."
    )


def test_clean_pdf_quote_keeps_raw_when_page_is_out_of_range():
    doc = _doc_with_lines([(100, "single page document")])
    uri = "http://test.invalid/a.pdf"
    pdf_math._pdf_cache[uri] = doc.tobytes()  # noqa: SLF001 - seed fetch cache: real bytes, no network
    assert (
        pdf_math.clean_pdf_quote(uri, 5, "the raw exact quote") == "the raw exact quote"
    )


def test_clean_pdf_quote_keeps_raw_when_region_cannot_be_located():
    doc = _doc_with_lines([(100, "some unrelated prose")])
    uri = "http://test.invalid/b.pdf"
    pdf_math._pdf_cache[uri] = doc.tobytes()  # noqa: SLF001
    # A quote that isn't on the page -> no region -> raw quote, never an OCR of the wrong slice.
    quote = "a selection that does not occur on this page"
    assert pdf_math.clean_pdf_quote(uri, 0, quote) == quote
