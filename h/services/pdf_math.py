"""Recover clean LaTeX for PDF annotations that span rendered math.

A PDF carries no embedded LaTeX (unlike LaTeXML HTML): selecting an equation yields the
flattened text layer, which loses 2D structure and can silently corrupt it -- ``½`` read
as ``12``, a fraction shattered across lines. The only recovery is OCR of the rendered
region.

A Hypothesis PDF annotation carries the page (``PageSelector.index``) and the selected
text. We fetch the PDF, render that page, crop the bounding box of the selection --
located from the page's own words, so every line it spans is captured at the text
column's width -- blank what that box holds beyond the selection on its first and last
lines, and OCR the crop with Mathpix -> clean ``$…$`` LaTeX. The stored selectors
(anchoring) are never touched; only the normalized copy carries the recovery.
"""

from __future__ import annotations

import base64
import os
import re

import pymupdf as fitz  # `pymupdf` is the typed canonical name; `fitz` the legacy alias
import requests

_DPI = 220  # render resolution of the cropped region; reads cleanly for Mathpix


class MathRecoveryError(Exception):
    """A math recovery genuinely failed, so no display quote can be produced.

    Raised (never returning the raw text-layer capture) for every failure mode -- an
    out-of-range page, a region that can't be located, empty OCR, a Mathpix/subprocess
    error or timeout. In the create path this propagates to ``pyramid_tm``, which rolls the
    request back so nothing is persisted: a stored annotation never carries raw garble.
    """


class MissingRecoverySettingError(MathRecoveryError):
    """A required math-recovery setting is absent from the deployment environment."""

    def __init__(self, setting: str) -> None:
        self.setting = setting
        super().__init__(f"{setting} is not set; math recovery cannot run")


class MalformedRecoverySettingError(MathRecoveryError):
    """A required math-recovery setting holds something that is not a duration.

    A distinct type from :class:`MissingRecoverySettingError` because the two are different
    operator mistakes with different fixes: one deployment forgot the setting, the other
    got its value wrong.
    """

    def __init__(self, setting: str, value: str) -> None:
        self.setting = setting
        self.value = value
        super().__init__(
            f"{setting}={value!r} is not a positive number of seconds; "
            f"math recovery cannot run"
        )


# A duration setting is a plain positive number of seconds. Validated by shape at the
# point of read rather than by converting and catching: an invalid value is then never
# live, and each consumer is spared its own handling.
_SECONDS = re.compile(r"\d*\.?\d+")


def _seconds_setting(setting: str) -> float:
    """Read and validate a required duration setting, or raise a recovery failure.

    These settings are required configuration -- the declared values live in the
    deployment env / tox env, never as a code-level fallback that silently applies when
    the variable is unset or unusable (POLICY.NO_HIDDEN_CONFIG). A missing or malformed
    value fails the recovery loudly (which rolls the create back) with the service's own
    failure type, so it reaches the operator through the math-recovery error view --
    named, with a diagnostic id -- instead of as an opaque internal error.
    """
    raw = os.environ.get(setting, "")
    value = raw.strip()
    if not value:
        raise MissingRecoverySettingError(setting)
    if not _SECONDS.fullmatch(value) or float(value) <= 0:
        raise MalformedRecoverySettingError(setting, raw)
    return float(value)


def recovery_timeout() -> float:
    """Seconds before a recovery call (Mathpix OCR, the Node extractor) is abandoned."""
    return _seconds_setting("H_MATH_NORMALIZE_TIMEOUT")


def shutdown_grace() -> float:
    """Seconds a recovery subprocess gets *beyond* its own deadline before being killed.

    The Node wrappers are handed ``recovery_timeout()`` as the deadline for the work they
    supervise (a page load, a render). This is the headroom on top of it: without any, the
    wrapper is killed at the same instant its own work times out, so a browser that is
    merely slow to start -- cold cache, loaded hardware -- is reported as a recovery
    timeout. It is configurable precisely so an operator hitting spurious timeouts can
    raise it without inflating the recovery timeout, which is a different knob with
    different consequences.
    """
    return _seconds_setting("H_MATH_NORMALIZE_SHUTDOWN_GRACE")


def subprocess_timeout() -> float:
    """Wall-clock limit for a recovery subprocess: its work's deadline plus the headroom.

    The single place the two settings are combined, so no call site recomputes the
    relationship.
    """
    return recovery_timeout() + shutdown_grace()


def _norm(text: str) -> str:
    """Reduce a word to its lowercase alphanumeric core, for word-matching.

    Matches the flattened text-layer quote against the page's own words: punctuation, case,
    and hyphenation differ between the two, but the alphanumeric core does not.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _projected_bounds(forms: list[str], words: list[str]) -> tuple[int, int] | None:
    """Project a possibly truncated token sequence onto page-word indexes."""
    if not words:
        return None

    def anchor(window_start: int, window_end: int, *, project_end: bool) -> int | None:
        window = words[window_start:window_end]
        for size in range(min(6, len(window)), 0, -1):
            for offset in range(len(window) - size + 1):
                needle = window[offset : offset + size]
                for page_start in range(len(forms) - size + 1):
                    if forms[page_start : page_start + size] != needle:
                        continue
                    word_offset = window_start + offset
                    if project_end:
                        return page_start + size - 1 + len(words) - (word_offset + size)
                    return page_start - word_offset
        return None

    edge = min(10, len(words))
    start = anchor(0, edge, project_end=False)
    end = anchor(len(words) - edge, len(words), project_end=True)
    if start is None or end is None:
        return None
    return (max(0, start), min(len(forms) - 1, end))


def _selection_word_rects(
    page: fitz.Page, exact: str, prefix: str = "", suffix: str = ""
) -> list[fitz.Rect] | None:
    """Locate the page's own words that the selection covers, in reading order.

    Located from the quote's own words: its prose anchors the two ends (the math between
    need not match the flattened glyphs), so a multi-line selection is captured whole, not
    collapsed to the single middle strip a prefix/suffix bracket can produce. ``None`` when
    the quote can't be located, so the caller keeps the raw text rather than OCR the wrong
    region.
    """
    # Words with no alphanumeric core (bare "=", "·", ...) are dropped from the index:
    # quote tokenization drops them too, and a surviving empty-form entry shifts the
    # start/end projection by one word per glyph, cutting the crop short (found by the
    # live integrated proof). Their glyph area still lands inside the box, which spans
    # the kept words bracketing them.
    entries = [
        (form, fitz.Rect(w[:4]))
        for w in page.get_text("words")
        if (form := _norm(w[4]))
    ]
    forms = [form for form, _ in entries]
    qwords = [w for w in (_norm(t) for t in exact.split()) if w]
    if not entries or not qwords:
        return None
    bounds = _projected_bounds(forms, qwords)
    if bounds is None:
        prefix_words = [w for w in (_norm(t) for t in prefix.split()) if w]
        suffix_words = [w for w in (_norm(t) for t in suffix.split()) if w]
        prefix_bounds = _projected_bounds(forms, prefix_words)
        suffix_bounds = _projected_bounds(forms, suffix_words)
        if prefix_bounds is None or suffix_bounds is None:
            return None
        start = prefix_bounds[1] + 1
        end = suffix_bounds[0] - 1
    else:
        start, end = bounds
    if start > end:
        return None
    return [entries[i][1] for i in range(start, end + 1)]


# Breathing room around the crop, so ascenders and descenders are not shaved off the
# rendered region and the same margin is honoured when the line remainders are removed.
_PAD = 2.0


def _quote_rect(
    page: fitz.Page, exact: str, prefix: str = "", suffix: str = ""
) -> fitz.Rect | None:
    """Locate the annotated text on ``page`` -- every line it spans, at column width.

    The column-tight width excludes marginal ink (e.g. arXiv's vertical id stamp).
    """
    rects = _selection_word_rects(page, exact, prefix=prefix, suffix=suffix)
    if rects is None:
        return None
    return fitz.Rect(
        max(page.rect.x0, min(r.x0 for r in rects) - _PAD),
        max(page.rect.y0, min(r.y0 for r in rects) - _PAD),
        min(page.rect.x1, max(r.x1 for r in rects) + _PAD),
        min(page.rect.y1, max(r.y1 for r in rects) + _PAD),
    )


def _line_remainders(
    page: fitz.Page, crop: fitz.Rect, rects: list[fitz.Rect]
) -> list[fitz.Rect]:
    """Return the parts of each line inside ``crop`` that the selection does not cover.

    Measured per line of the page's own text, against every selection word on that line
    rather than the first and last in reading order. Reading order is not left to right
    where a formula carries sub- and superscripts: on arXiv 2312.03638 the last word of
    ``... polarization M = L ⊗ 2`` is the subscript ``Z``, which sits to the *left* of the
    ``⊗ 2`` it belongs to, so cutting at the last word's right edge would erase the end of
    the very formula being recovered.
    """
    # A word belongs to the line whose box holds its centre, not to every line box its own
    # box happens to touch: a subscript's box is tall enough to reach the neighbouring line,
    # and treating it as text on that line would place the cut in the middle of a line the
    # selection covers whole.
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            box = fitz.Rect(line["bbox"])
            on_line = [
                r
                for r in rects
                if box.y0 <= (r.y0 + r.y1) / 2 <= box.y1
                and box.x0 <= (r.x0 + r.x1) / 2 <= box.x1
            ]
            if on_line:
                lines.append((box, on_line))
    if not lines:
        return []
    # Only the outermost lines can carry text from outside the selection: the selection
    # covers every line between them whole.
    _, first_words = min(lines, key=lambda item: item[0].y0)
    _, last_words = max(lines, key=lambda item: item[0].y1)
    return [
        _band(first_words, crop.x0, min(r.x0 for r in first_words)),
        _band(last_words, max(r.x1 for r in last_words), crop.x1),
    ]


# Set text lines in a PDF overlap vertically: a tall glyph on one line -- an integral, a
# bracket, a subscript -- reaches into the box of the line below. A band spanning a line's
# full height therefore also covers ink belonging to its neighbour, so each band is inset
# to the line's own core. What that gives up is the tip of a neighbouring ascender or
# descender surviving at the band's edge, which OCR reads past; what it protects is the
# selected formula on the line above, which OCR cannot recover once erased.
_LINE_INSET = 0.2


def _band(words: list[fitz.Rect], x0: float, x1: float) -> fitz.Rect:
    """Return a strip over the core of the line ``words`` sit on, from ``x0`` to ``x1``."""
    top, bottom = min(r.y0 for r in words), max(r.y1 for r in words)
    inset = (bottom - top) * _LINE_INSET
    return fitz.Rect(x0, top + inset, x1, bottom - inset)


def _selection_png(
    page: fitz.Page, exact: str, prefix: str = "", suffix: str = ""
) -> bytes | None:
    """Render the image to OCR: the selection's box, with its line remainders blanked.

    The box spans whole lines at column width, so its first line can begin before the
    selection starts and its last can run past where it ends -- the neighbouring sentence,
    which must not reach the OCR and be stored as if it were the selection. Both remainders
    are bounded by the selection's own word rectangles, so they are painted out of the
    rendered region here rather than cut back out of the OCR afterwards.

    Cutting them out afterwards is what the recovery used to do, by searching the OCR for
    the quote's leading and trailing words. That cannot work when a selection begins or ends
    in math: those words are flattened text-layer glyphs, and the OCR renders the same
    region as LaTeX, so the anchor never matches and a correct recovery is rejected
    (dzackgarza/h#3). The rendered page is the right place to make the cut, because there
    the selection's extent is known exactly rather than inferred from its transcription.

    Painted, not redacted: PDF redaction drops every character whose box meets the
    rectangle, and a neighbouring line's tall glyph reaches into this one, so redacting a
    line remainder deletes formulas the selection does contain. Painting is a crop, and
    takes only the pixels it covers.
    """
    rects = _selection_word_rects(page, exact, prefix=prefix, suffix=suffix)
    if rects is None:
        return None
    crop = fitz.Rect(
        max(page.rect.x0, min(r.x0 for r in rects) - _PAD),
        max(page.rect.y0, min(r.y0 for r in rects) - _PAD),
        min(page.rect.x1, max(r.x1 for r in rects) + _PAD),
        min(page.rect.y1, max(r.y1 for r in rects) + _PAD),
    )
    pixmap = page.get_pixmap(dpi=_DPI, clip=crop)
    # A clipped pixmap keeps the page's device coordinates (``pixmap.irect`` is the clip,
    # not a 0-based box), so the bands are scaled into that same space rather than offset
    # against the crop; ``set_rect`` ignores whatever falls outside the pixmap.
    to_device = fitz.Matrix(_DPI / 72.0, _DPI / 72.0)
    for band in _line_remainders(page, crop, rects):
        if band.is_empty or band.width <= 0:
            continue
        pixmap.set_rect((band * to_device).round(), (255, 255, 255))
    return pixmap.tobytes("png")


def ocr_latex(png: bytes) -> str:
    """Mathpix OCR of a PNG -> its text with math rendered as ``$…$`` LaTeX.

    Every failure of the OCR round-trip -- a missing key, a network error, a non-2xx
    response, or a timeout -- is a ``MathRecoveryError`` (never a leaked ``requests`` /
    config error), so the caller rolls the create back and the outcome logs uniformly.
    """
    key = os.environ.get("MATHPIX_API_KEY")
    if not key:
        msg = "MATHPIX_API_KEY not set; cannot OCR PDF math"
        raise MathRecoveryError(msg)
    try:
        resp = requests.post(
            "https://api.mathpix.com/v3/text",
            headers={"app_key": key},
            json={
                "src": "data:image/png;base64," + base64.b64encode(png).decode(),
                "formats": ["text"],
                # Emit standard LaTeX: $..$ inline, $$..$$ display -- the recovered quote
                # is stored verbatim and must paste into a normal LaTeX document without
                # reformatting. The sidebar renders these with MathJax (full delimiter set).
                "math_inline_delimiters": ["$", "$"],
                "math_display_delimiters": ["$$", "$$"],
            },
            timeout=recovery_timeout(),
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        msg = f"Mathpix OCR request failed: {exc}"
        raise MathRecoveryError(msg) from exc
    return resp.json().get("text", "").strip()


# Insertion-ordered and bounded: enough to dedupe fetches within a burst of annotations
# on the same few documents, without accumulating PDF bytes for the life of a web process.
_PDF_CACHE_MAX = 8
_pdf_cache: dict[str, bytes] = {}


def _fetch_pdf(uri: str) -> bytes:
    """Fetch (and cache) the PDF bytes for ``uri`` (http(s)).

    Fetched once and cached, so a batch of annotations on one document downloads it a
    single time. The cache holds at most ``_PDF_CACHE_MAX`` documents; the oldest entry
    is evicted beyond that.
    """
    if uri not in _pdf_cache:
        try:
            resp = requests.get(uri, timeout=recovery_timeout(), allow_redirects=True)
            resp.raise_for_status()
        except requests.RequestException as exc:
            msg = f"could not fetch PDF {uri!r}: {exc}"
            raise MathRecoveryError(msg) from exc
        while len(_pdf_cache) >= _PDF_CACHE_MAX:
            del _pdf_cache[next(iter(_pdf_cache))]
        _pdf_cache[uri] = resp.content
    return _pdf_cache[uri]


def clean_pdf_quote(
    uri: str, page_index: int, exact: str, prefix: str = "", suffix: str = ""
) -> str:
    """Recover clean LaTeX for a PDF annotation by OCR'ing the region it occupies.

    The region is the bounding box of ``exact`` located from the page's own words. Raises
    ``MathRecoveryError`` -- never returns the raw text-layer quote -- when the page is out of
    range, the region can't be located, or OCR comes back empty, so a failure surfaces and
    rolls the create back rather than persisting wrong or raw math.
    """
    try:
        doc = fitz.open(stream=_fetch_pdf(uri), filetype="pdf")
    except fitz.FileDataError as exc:  # corrupt / non-PDF bytes
        msg = f"PDF at {uri!r} could not be opened: {exc}"
        raise MathRecoveryError(msg) from exc
    # The document is closed on every exit path (the context manager covers the raising
    # ones too); leaking handles would exhaust file descriptors in a long-lived worker.
    with doc:
        if not 0 <= page_index < doc.page_count:
            msg = f"PDF page {page_index} is out of range (0..{doc.page_count - 1})"
            raise MathRecoveryError(msg)
        png = _selection_png(doc[page_index], exact, prefix=prefix, suffix=suffix)
        if png is None:
            msg = "PDF region could not be located for the quote"
            raise MathRecoveryError(msg)
    ocr = ocr_latex(png)
    if not ocr.strip():
        msg = "OCR returned empty output for the PDF region"
        raise MathRecoveryError(msg)
    return ocr.strip()
