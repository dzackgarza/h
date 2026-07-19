"""Recover clean LaTeX for PDF annotations that span rendered math.

A PDF carries no embedded LaTeX (unlike LaTeXML HTML): selecting an equation yields the
flattened text layer, which loses 2D structure and can silently corrupt it -- ``½`` read
as ``12``, a fraction shattered across lines. The only recovery is OCR of the rendered
region.

A Hypothesis PDF annotation carries the page (``PageSelector.index``) and the selected
text. We fetch the PDF, render that page, crop the bounding box of the selection --
located from the page's own words, so every line it spans is captured at the text
column's width -- and OCR the crop with Mathpix -> clean ``$…$`` LaTeX. The stored
selectors (anchoring) are never touched; only the normalized copy carries the recovery.
"""

from __future__ import annotations

import base64
import os
import re

import fitz  # pymupdf
import requests

# Greek letters and math operators survive a PDF text-layer selection as ordinary Unicode
# (not the Mathematical Alphanumeric block the HTML garble uses), so they -- not that block
# -- are the signal that a PDF quote spans math worth OCR'ing.
_PDF_MATH = re.compile(
    "["
    "Ͱ-Ͽ"  # Greek
    "∀-⋿"  # mathematical operators
    "⨀-⫿"  # supplemental math operators
    "⟀-⟿"  # misc math symbols-A
    "←-⇿"  # arrows
    "]"
)

_DPI = 220  # render resolution of the cropped region; reads cleanly for Mathpix


def pdf_has_math(quote: str) -> bool:
    """Report whether a PDF text-layer quote plausibly spans math worth OCR'ing.

    True when it contains a Greek letter or math operator -- the symbols a PDF selection
    keeps as ordinary Unicode.
    """
    return bool(_PDF_MATH.search(quote))


def _norm(text: str) -> str:
    """Reduce a word to its lowercase alphanumeric core, for word-matching.

    Matches the flattened text-layer quote against the page's own words: punctuation, case,
    and hyphenation differ between the two, but the alphanumeric core does not.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _match_end(forms: list[str], ngram: list[str]) -> int | None:
    """Index of the final word of the last contiguous occurrence of ``ngram`` in ``forms``."""
    needle = [w for w in ngram if w]
    if not needle:
        return None
    for i in range(len(forms) - len(needle), -1, -1):
        if forms[i : i + len(needle)] == needle:
            return i + len(needle) - 1
    return None


def _quote_rect(page: fitz.Page, exact: str) -> fitz.Rect | None:
    """Locate the annotated text's bounding box on ``page`` -- every line, at column width.

    Located from the quote's own words: its prose anchors the two ends (the math between
    need not match the flattened glyphs), so a multi-line selection is captured whole, not
    collapsed to the single middle strip a prefix/suffix bracket can produce, and the
    column-tight width excludes marginal ink (e.g. arXiv's vertical id stamp). ``None`` when
    the quote can't be located, so the caller keeps the raw text rather than OCR the wrong
    region.
    """
    entries = [(_norm(w[4]), fitz.Rect(w[:4])) for w in page.get_text("words")]
    forms = [form for form, _ in entries]
    qwords = [w for w in (_norm(t) for t in exact.split()) if w]
    if not entries or not qwords:
        return None
    # Bottom anchor: the quote's last prose words (math rarely sits at the very tail).
    end = next(
        (e for k in (4, 3, 2, 1) if (e := _match_end(forms, qwords[-k:])) is not None),
        None,
    )
    if end is None:
        return None
    # Top anchor: the earliest quote word that occurs at or before the tail. When it repeats
    # (both across the page and *within* the quote, e.g. "Enriques ... Enriques"), pick the
    # occurrence nearest where the start should fall given the tail and the quote's length --
    # the run of page words a contiguous selection covers is ~its word count.
    target = end - (len(qwords) - 1)
    start = next(
        (
            min(cands, key=lambda i: abs(i - target))
            for q in qwords[:8]
            if (cands := [i for i, w in enumerate(forms) if w == q and i <= end])
        ),
        None,
    )
    if start is None or start > end:
        return None
    rects = [entries[i][1] for i in range(start, end + 1)]
    pad = 2.0
    return fitz.Rect(
        max(page.rect.x0, min(r.x0 for r in rects) - pad),
        max(page.rect.y0, min(r.y0 for r in rects) - pad),
        min(page.rect.x1, max(r.x1 for r in rects) + pad),
        min(page.rect.y1, max(r.y1 for r in rects) + pad),
    )


def _trim_to_quote(ocr: str, exact: str) -> str:
    """Trim OCR output back to the annotated span.

    The crop is full column width, so its last line can run past the selection into the next
    sentence; cut after the quote's trailing prose, keeping the math before it. A no-op when
    those words can't be found (e.g. the tail is itself math), which errs toward keeping text
    rather than dropping it.
    """
    qtokens = [t for t in exact.split() if _norm(t)]
    if len(qtokens) < 3:
        return ocr.strip()
    core = [re.escape(t.strip(".,;:()[]-")) for t in qtokens[-3:]]
    tail = list(re.finditer(r"\W+".join(core) + r"[.,;:)\]]*", ocr, re.IGNORECASE))
    if tail:
        ocr = ocr[: tail[-1].end()]
    return ocr.strip()


def _ocr_latex(png: bytes) -> str:
    """Mathpix OCR of a PNG -> its text with math rendered as ``$…$`` LaTeX."""
    key = os.environ.get("MATHPIX_API_KEY")
    if not key:
        msg = "MATHPIX_API_KEY not set; cannot OCR PDF math"
        raise RuntimeError(msg)
    resp = requests.post(
        "https://api.mathpix.com/v3/text",
        headers={"app_key": key},
        json={
            "src": "data:image/png;base64," + base64.b64encode(png).decode(),
            "formats": ["text"],
            # Hypothesis's markdown renders math only in \(..\) (inline) or $$..$$ (block)
            # delimiters, never single $..$ -- so emit those, which agents read too.
            "math_inline_delimiters": ["\\(", "\\)"],
            "math_display_delimiters": ["$$", "$$"],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


_pdf_cache: dict[str, bytes] = {}


def _fetch_pdf(uri: str) -> bytes:
    """Fetch (and cache) the PDF bytes for ``uri`` (http(s)).

    Fetched once and cached, so a batch of annotations on one document downloads it a single
    time.
    """
    if uri not in _pdf_cache:
        _pdf_cache[uri] = requests.get(uri, timeout=30, allow_redirects=True).content
    return _pdf_cache[uri]


def clean_pdf_quote(uri: str, page_index: int, exact: str) -> str:
    """Recover clean LaTeX for a PDF annotation by OCR'ing the region it occupies.

    The region is the bounding box of ``exact`` located from the page's own words. Returns
    ``exact`` (the raw text-layer quote) unchanged when the page or region can't be resolved,
    so a locating miss degrades to honest raw text rather than wrong math.
    """
    doc = fitz.open(stream=_fetch_pdf(uri), filetype="pdf")
    if not 0 <= page_index < doc.page_count:
        return exact
    rect = _quote_rect(doc[page_index], exact)
    if rect is None:
        return exact
    png = doc[page_index].get_pixmap(dpi=_DPI, clip=rect).tobytes("png")
    return _trim_to_quote(_ocr_latex(png), exact) or exact
