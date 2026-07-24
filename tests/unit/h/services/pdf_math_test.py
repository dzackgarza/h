"""Owned-logic tests for the PDF math normalizer.

The OCR itself is Mathpix's correctness, proven out-of-band. What this module owns, and
what these tests prove, is: locating the annotation's region as the bounding box of the
selected text -- every line it spans, at the text column's width. On a locating miss (or an out-of-range page or empty OCR) it raises rather
than OCR the wrong region or return raw. Region logic runs against a real PyMuPDF document
with text at known positions: a real boundary, no network, no OCR.
"""

from __future__ import annotations

import http.server
import threading

import pymupdf as fitz
import pytest

from h.services import pdf_math
from h.services.pdf_math import MathRecoveryError
from tests.common import mathpix

#: What the real Mathpix API returned for the rendered regions below, replayed from
#: `tests/corpus/mathpix/` and copied here verbatim. Recorded, never hand-written: an
#: expectation typed by hand is a guess about the OCR, which is what the recording exists
#: to stop. Re-record with `bin/record_mathpix_fixtures.py` if a region changes.
RECORDED_RESIDUE = "the residue is some finite data attached here"
RECORDED_ENDS_IN_MATH = (
    r"surfaces ( $Z, M$ ) with a 2 -divisible polarization $M=L \cdot 2$"
)


@pytest.fixture
def mathpix_api(monkeypatch):
    """Serve the recovery's OCR from responses recorded off the real Mathpix API.

    Nothing here replaces `ocr_latex`: it runs whole, and its request has to satisfy the
    v3/text contract or the emulator answers 4xx and the test fails. See
    `tests/common/mathpix.py`.
    """
    emulator, server, thread = mathpix.serving()
    monkeypatch.setenv("MATHPIX_API_URL", emulator.url)
    monkeypatch.setenv("MATHPIX_API_KEY", "recorded-fixtures")
    try:
        yield emulator
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _doc_with_lines(
    lines: list[tuple[float, str]], color: tuple[float, float, float] | None = None
) -> fitz.Document:
    """Build a one-page PDF with each ``(baseline_y, text)`` drawn at the left margin."""
    doc = fitz.open()
    page = doc.new_page()
    for y, text in lines:
        page.insert_text((72, y), text, fontsize=11, color=color)
    return doc


def _png_of(line: str) -> bytes:
    """Render a page carrying ``line`` -- a real PNG to hand straight to the OCR."""
    dpi = pdf_math._DPI  # noqa: SLF001 - the render resolution the recovery itself uses
    return _doc_with_lines([(100, line)])[0].get_pixmap(dpi=dpi).tobytes("png")


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


@pytest.mark.usefixtures("mathpix_api")
def test_clean_pdf_quote_returns_the_ocr_latex():
    # The located region is OCR'd; the trimmed OCR text is what's returned (never the raw
    # text-layer quote). The answer below is what the real API returned for this exact
    # image, replayed; the OCR's own correctness is Mathpix's, proven out of band.
    doc = _doc_with_lines([(100, "the residue is some finite data attached here")])
    uri = "http://test.invalid/ok.pdf"
    pdf_math._pdf_cache[uri] = doc.tobytes()  # noqa: SLF001 - seed fetch cache: real bytes, no network

    result = pdf_math.clean_pdf_quote(
        uri, 0, "the residue is some finite data attached here"
    )

    assert result == RECORDED_RESIDUE


def test_the_ocr_request_is_one_mathpix_would_accept(mathpix_api):
    # What replacing `ocr_latex` with a lambda used to hide: 35 mutations of the request
    # -- the endpoint replaced with None, the formats corrupted -- survived, because no
    # test ever built one. The emulator rejects a request Mathpix would reject; this
    # pins the parts of it the recovery depends on being right.
    doc = _doc_with_lines([(100, "the residue is some finite data attached here")])
    uri = "http://test.invalid/request-shape.pdf"
    pdf_math._pdf_cache[uri] = doc.tobytes()  # noqa: SLF001

    pdf_math.clean_pdf_quote(uri, 0, "the residue is some finite data attached here")

    call = mathpix_api.last()
    assert call.path == "/v3/text"
    assert call.headers["app_key"] == "recorded-fixtures"
    assert call.body["formats"] == ["text"]
    # The stored quote must paste into a LaTeX document unchanged, and the sidebar
    # renders it with MathJax: standard delimiters, not Mathpix's default markers.
    assert call.body["math_inline_delimiters"] == ["$", "$"]
    assert call.body["math_display_delimiters"] == ["$$", "$$"]
    assert call.png.startswith(b"\x89PNG")


def test_an_unset_endpoint_fails_the_ocr_as_a_recovery_failure(monkeypatch):
    # Where the paid call goes is deployment configuration, like the timeouts. Unset, the
    # recovery fails by name rather than posting somewhere unintended.
    monkeypatch.setenv("MATHPIX_API_KEY", "test-key")
    monkeypatch.delenv("MATHPIX_API_URL", raising=False)

    with pytest.raises(pdf_math.MissingRecoverySettingError) as failure:
        pdf_math.ocr_latex(b"\x89PNG")

    # Which setting, from the exception's own field -- an operator has to be told what to
    # set, and the two required settings must not be confusable.
    assert failure.value.setting == "MATHPIX_API_URL"


def test_a_non_2xx_from_mathpix_becomes_a_recovery_failure(mathpix_api):
    # The emulator answers 409 for an image it has no recording of, which is a real
    # non-2xx round trip: it must surface as MathRecoveryError (rolling the create back),
    # not as a raw requests error.
    mathpix_api.expect_no_recording()

    with pytest.raises(MathRecoveryError):
        pdf_math.ocr_latex(_png_of("an image no fixture was ever recorded for"))


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


@pytest.mark.usefixtures("mathpix_api")
def test_clean_pdf_quote_raises_when_ocr_is_empty():
    # The words are in the text layer but drawn in white, so the region locates and the
    # render carries nothing to read: Mathpix answers with empty text, and an empty
    # recovery must fail rather than store a blank quote over the reader's selection.
    doc = _doc_with_lines(
        [(100, "the residue is some finite data attached here")], color=(1, 1, 1)
    )
    uri = "http://test.invalid/empty.pdf"
    pdf_math._pdf_cache[uri] = doc.tobytes()  # noqa: SLF001
    with pytest.raises(MathRecoveryError, match="empty"):
        pdf_math.clean_pdf_quote(uri, 0, "the residue is some finite data attached")


def test_ocr_latex_raises_math_recovery_error_when_key_missing(monkeypatch):
    # A missing key is a recovery failure (rolls the create back), not a leaked config error.
    monkeypatch.delenv("MATHPIX_API_KEY", raising=False)
    with pytest.raises(MathRecoveryError, match="MATHPIX_API_KEY"):
        pdf_math.ocr_latex(b"\x89PNG")


def test_ocr_latex_wraps_mathpix_request_failure(monkeypatch):
    # A Mathpix network error / timeout / non-2xx must surface as MathRecoveryError, so it
    # rolls back and logs uniformly rather than leaking a raw requests exception. The
    # endpoint below is a closed port on the loopback: a real failed connection, not an
    # exception planted in `requests`.
    monkeypatch.setenv("MATHPIX_API_KEY", "test-key")
    monkeypatch.setenv("MATHPIX_API_URL", "http://127.0.0.1:1/v3/text")

    with pytest.raises(MathRecoveryError, match="Mathpix OCR request failed"):
        pdf_math.ocr_latex(b"\x89PNG")


def test_fetch_pdf_evicts_the_oldest_entry_beyond_the_cache_bound():
    # The PDF byte cache exists to dedupe fetches within a burst of annotations on one
    # document; it must not grow without bound for the life of a web process.
    doc = _doc_with_lines([(100, "cached document")])
    payload = doc.tobytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            """Keep test output quiet; assertions cover the behavior."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        first = f"{base}/doc-0.pdf"
        pdf_math._fetch_pdf(first)  # noqa: SLF001
        for n in range(1, pdf_math._PDF_CACHE_MAX + 1):  # noqa: SLF001
            pdf_math._fetch_pdf(f"{base}/doc-{n}.pdf")  # noqa: SLF001

        assert len(pdf_math._pdf_cache) == pdf_math._PDF_CACHE_MAX  # noqa: SLF001
        assert first not in pdf_math._pdf_cache  # noqa: SLF001 - oldest evicted
        assert f"{base}/doc-{pdf_math._PDF_CACHE_MAX}.pdf" in pdf_math._pdf_cache  # noqa: SLF001
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_quote_rect_is_not_shifted_by_punctuation_only_page_words():
    # Found by the live Mathpix proof: a page word with no alphanumeric core (the "=" in
    # the formula) stays in the page-word index while quote tokenization drops it, so the
    # tail projection landed one word short and the crop cut the quote's final word.
    doc = _doc_with_lines(
        [(100, "The residue theorem gives X = 2 which completes the argument here.")]
    )
    page = doc[0]
    exact = "The residue theorem gives X = 2 which completes the argument"

    rect = pdf_math._quote_rect(page, exact)  # noqa: SLF001

    assert rect is not None
    assert "argument" in page.get_textbox(rect)  # the final word is inside the crop


@pytest.mark.usefixtures("mathpix_api")
def test_clean_pdf_quote_recovers_a_selection_that_ends_in_math():
    # The live failure this reproduces (dzackgarza/h#3): every PDF selection ending in math
    # was rejected with "the quote's trailing words were not found in the OCR output". The
    # quote, the page text and the OCR below are the real ones observed on arXiv 2312.03638
    # page 1. The trailing tokens of the flattened text layer are "L", "⊗", "2"; Mathpix
    # returns the same region as \mathcal{L}_{Z}^{\otimes 2}, so a trailing-prose anchor
    # cannot match a recovery that in fact succeeded.
    doc = _doc_with_lines(
        [(100, "surfaces ( Z, M ) with a 2-divisible polarization M = L ⊗ 2")]
    )
    uri = "http://test.invalid/ends-in-math.pdf"
    pdf_math._pdf_cache[uri] = doc.tobytes()  # noqa: SLF001

    result = pdf_math.clean_pdf_quote(
        uri,
        0,
        "surfaces ( Z,  M )   with   a   2-divisible   polarization   M  =  L ⊗ 2",
    )

    # What the real API returned for this region: math where the text layer had flattened
    # glyphs, which is exactly why a trailing-prose anchor could not confirm the recovery.
    assert result == RECORDED_ENDS_IN_MATH
    assert "$" in result


class TestRecoveryTimeoutSetting:
    """A misconfigured recovery timeout is an operator mistake, not an internal fault.

    ``H_MATH_NORMALIZE_TIMEOUT`` is required deployment configuration. When it holds
    something that is not a number, the recovery must fail with the service's own
    structured failure -- the one the API error view renders with a diagnostic id --
    rather than with a generic conversion error raised from wherever the value happened
    to be used. The two operator mistakes (nothing set, something wrong set) surface as
    different failures, because they call for different fixes.
    """

    @pytest.fixture(autouse=True)
    def endpoint_configured(self, monkeypatch):
        """Put the other required setting in place: these tests are about the timeout."""
        monkeypatch.setenv("MATHPIX_API_URL", "http://127.0.0.1:1/v3/text")

    def test_a_malformed_timeout_fails_the_ocr_path_as_a_recovery_failure(
        self, monkeypatch
    ):
        monkeypatch.setenv("MATHPIX_API_KEY", "test-key")
        monkeypatch.setenv("H_MATH_NORMALIZE_TIMEOUT", "half a minute")

        with pytest.raises(MathRecoveryError) as failure:
            pdf_math.ocr_latex(b"\x89PNG")

        assert "H_MATH_NORMALIZE_TIMEOUT" in str(failure.value)
        assert "half a minute" in str(failure.value)

    def test_a_malformed_timeout_fails_the_pdf_fetch_path_as_a_recovery_failure(
        self, monkeypatch
    ):
        # The fetch is the other consumer of the setting; it must be validated there too,
        # and before any network call is attempted with a nonsense timeout.
        monkeypatch.setenv("H_MATH_NORMALIZE_TIMEOUT", "-")

        with pytest.raises(MathRecoveryError) as failure:
            pdf_math.clean_pdf_quote(
                "http://test.invalid/never-fetched.pdf", 0, "a quote to recover"
            )

        assert "H_MATH_NORMALIZE_TIMEOUT" in str(failure.value)

    def test_an_absent_timeout_and_a_malformed_one_are_different_failures(
        self, monkeypatch
    ):
        monkeypatch.setenv("MATHPIX_API_KEY", "test-key")

        monkeypatch.delenv("H_MATH_NORMALIZE_TIMEOUT", raising=False)
        with pytest.raises(MathRecoveryError) as absent:
            pdf_math.ocr_latex(b"\x89PNG")

        monkeypatch.setenv("H_MATH_NORMALIZE_TIMEOUT", "thirty")
        with pytest.raises(MathRecoveryError) as malformed:
            pdf_math.ocr_latex(b"\x89PNG")

        assert type(absent.value) is not type(malformed.value)

    @pytest.mark.usefixtures("mathpix_api")
    def test_a_valid_timeout_leaves_the_recovery_working(self, monkeypatch):
        # The paired positive: a well-formed setting is accepted and the recovery runs
        # through to its normal result.
        monkeypatch.setenv("H_MATH_NORMALIZE_TIMEOUT", "12.5")
        doc = _doc_with_lines([(100, "the residue is some finite data attached here")])
        uri = "http://test.invalid/valid-timeout.pdf"
        pdf_math._pdf_cache[uri] = doc.tobytes()  # noqa: SLF001 - seeded bytes, no network

        result = pdf_math.clean_pdf_quote(
            uri, 0, "the residue is some finite data attached here"
        )

        assert result == RECORDED_RESIDUE
