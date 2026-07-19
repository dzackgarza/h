"""Enrich a new annotation's flattened quote into a display-ready, stored one.

At intake -- not at display -- an annotation's flattened quote is turned into a quote with
rendered math recovered and stored in ``annotation_normalized``; every view then reads that
stored field (the API joins it), and the raw capture is used only for anchoring. The
annotation row is never touched.

Routing mirrors the source document. A PDF region (``PageSelector``) is OCR'd with Mathpix
(``method='ocr'``); an HTML quote is reconstructed against the page's math source by the
bundled Node/KaTeX normalizer (``method='html'``), because KaTeX reproduces the page's
MathJax rendering and pure Python does not; anything with no recoverable math stores its raw
quote (``method='raw'``).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from sqlalchemy import orm, select

from h.models import Annotation, AnnotationNormalized
from h.models.document import DocumentURI
from h.services.pdf_math import clean_pdf_quote, pdf_has_math

_HTML_NORMALIZE = (
    Path(__file__).resolve().parents[1] / "scripts" / "html-normalize" / "index.mjs"
)

# Mathematical Alphanumeric Symbols, invisible math operators, or LaTeXML accessibility
# markers -- signals that an HTML quote spans rendered math (so an empty reconstruction means
# the recovery missed, not that there was no math). Mirrors the PDF signal in pdf_math.
_HTML_MATH_SIGNAL = re.compile("[\U0001d400-\U0001d7ff⁡-⁤]|start_POST(?:SUB|SUPER)SCRIPT")


def _html_has_math(quote: str) -> bool:
    """Whether an HTML quote plausibly spans rendered math worth reconstructing."""
    return bool(_HTML_MATH_SIGNAL.search(quote))


def _page_index(annotation: Annotation) -> int | None:
    """Read the 0-based page from a ``PageSelector``, or ``None`` for a non-PDF annotation.

    This is what routes an annotation to PDF OCR rather than HTML math recovery.
    """
    for selector in annotation.target_selectors or []:
        if isinstance(selector, dict) and selector.get("type") == "PageSelector":
            index = selector.get("index")
            if isinstance(index, int):
                return index
    return None


def _html_normalize(uri: str, exact: str) -> tuple[str, str | None]:
    """Reconstruct an HTML quote's math via the bundled Node (KaTeX) normalizer.

    Returns ``(reconstructed_quote, error)``. On success ``error`` is ``None`` and the quote
    is the reconstruction (``""`` when the selection spans no math -- a legitimate no-op). On
    a hard failure (page fetch, parse, a crashed script) the quote is ``""`` and ``error`` is
    the reason, so the caller records a failure rather than a silent raw result. KaTeX
    reproduces the page's MathJax rendering, which pure Python cannot.
    """
    try:
        result = subprocess.run(  # noqa: S603 - fixed script path, args are data
            ["node", str(_HTML_NORMALIZE), uri, exact],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ("", f"html-normalize subprocess failed: {exc}")
    if result.returncode != 0:
        reason = result.stderr.strip()[:500] or f"exit {result.returncode}"
        return ("", f"html-normalize failed: {reason}")
    return (result.stdout.strip(), None)


def _recover_html(uri: str, quote: str) -> tuple[str, str, str | None]:
    """Reconstruct HTML math, or record why it couldn't.

    A normalizer error, or a math signal that came back with an empty reconstruction, is
    returned as the raw quote with an ``error``.
    """
    clean, error = _html_normalize(uri, quote)
    if error:
        return (quote, "raw", error)
    if clean and clean != quote:
        return (clean, "html", None)
    if _html_has_math(quote):  # had math, but reconstruction came back empty
        return (quote, "raw", "HTML math could not be reconstructed")
    return (quote, "raw", None)


class NormalizationService:
    def __init__(self, session: orm.Session) -> None:
        self._session = session

    def normalize(self, annotation: Annotation) -> AnnotationNormalized | None:
        """Recover ``annotation``'s display-ready quote and upsert its normalized row.

        Returns the row, or ``None`` for an annotation with no quote (nothing to normalize;
        no row). One row per annotation (unique FK); re-running replaces the previous result.
        """
        recovered = self._recover(annotation)
        if recovered is None:
            return None
        quote, method, error = recovered

        row = annotation.normalized or AnnotationNormalized(annotation=annotation)
        row.normalized_quote = quote
        row.method = method
        row.error = error
        if row not in self._session:
            self._session.add(row)
        return row

    def reset(self, annotation: Annotation) -> None:
        """Drop ``annotation``'s normalized row so its status returns to pending.

        The retry endpoint calls this before re-enqueuing, so a failed annotation flips to
        pending immediately rather than reporting the stale failure until the task re-runs.
        """
        if annotation.normalized is not None:
            self._session.delete(annotation.normalized)
            annotation.normalized = None

    def _recover(self, annotation: Annotation) -> tuple[str, str, str | None] | None:
        """Recover ``(normalized_quote, method, error)``, or ``None`` for a quote-less one.

        Every failure is recorded, never swallowed: a raised error (PDF fetch/OCR, page parse)
        is caught, and a *soft* miss -- the quote carries a math signal but recovery came back
        empty -- is also returned with an ``error``, not a silent ``raw``. A quote with no math
        signal is a legitimate ``raw`` (``error`` NULL). One bad document neither crashes the
        enrichment task nor loses the annotation.
        """
        quote = annotation.quote or ""
        if not quote:
            return None
        uri = annotation.target_uri or ""
        page = _page_index(annotation)
        try:
            if page is not None:  # PDF annotation
                return self._recover_pdf(uri, page, quote)
            if uri.startswith(("http://", "https://")):  # HTML annotation
                return _recover_html(uri, quote)
        except Exception as exc:  # noqa: BLE001 - recorded on the row and logged, never swallowed
            return (quote, "raw", f"{type(exc).__name__}: {exc}")
        return (quote, "raw", None)

    def _recover_pdf(
        self, uri: str, page: int, quote: str
    ) -> tuple[str, str, str | None]:
        """OCR the PDF math region, or record why it couldn't (a math signal but no result)."""
        url = self._resolve_pdf_url(uri)
        clean = clean_pdf_quote(url, page, quote) if url else quote
        if clean != quote:
            return (clean, "ocr", None)
        if pdf_has_math(quote):  # had math, but the region couldn't be located/OCR'd
            return (quote, "raw", "PDF math region could not be located for OCR")
        return (quote, "raw", None)

    def _resolve_pdf_url(self, uri: str) -> str | None:
        """Resolve a PDF annotation's document to a fetchable http(s) URL, or ``None``.

        An http URI is itself fetchable; a ``urn:x-pdf:`` fingerprint (what a locally-opened
        PDF anchors to) is resolved to a sibling http URL via the ``document_uri`` table -- a
        PDF downloaded from the web keeps its source URL alongside the fingerprint.
        """
        if uri.startswith(("http://", "https://")):
            return uri
        if not uri.startswith("urn:x-pdf:"):
            return None
        sibling = orm.aliased(DocumentURI)
        return self._session.scalars(
            select(sibling._uri)  # noqa: SLF001 - the ORM-mapped uri column
            .join(DocumentURI, DocumentURI.document_id == sibling.document_id)
            .where(DocumentURI._uri == uri, sibling._uri.like("http%"))  # noqa: SLF001
            .limit(1)
        ).first()


def factory(_context, request) -> NormalizationService:
    return NormalizationService(request.db)
