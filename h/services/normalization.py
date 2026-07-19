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

import subprocess
from pathlib import Path

from sqlalchemy import orm, select

from h.models import Annotation, AnnotationNormalized
from h.models.document import DocumentURI
from h.services.pdf_math import clean_pdf_quote

_HTML_NORMALIZE = (
    Path(__file__).resolve().parents[1] / "scripts" / "html-normalize" / "index.mjs"
)


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


def _html_normalize(uri: str, exact: str) -> str:
    """Reconstruct an HTML quote's math via the bundled Node (KaTeX) normalizer.

    Returns ``""`` on any failure. KaTeX reproduces the page's MathJax rendering, which pure
    Python cannot.
    """
    try:
        result = subprocess.run(  # noqa: S603 - fixed script path, args are data
            ["node", str(_HTML_NORMALIZE), uri, exact],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


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
        quote, method = recovered

        row = annotation.normalized or AnnotationNormalized(annotation=annotation)
        row.normalized_quote = quote
        row.method = method
        if row not in self._session:
            self._session.add(row)
        return row

    def _recover(self, annotation: Annotation) -> tuple[str, str] | None:
        """``(normalized_quote, method)`` for an annotation with a quote, else ``None``."""
        quote = annotation.quote or ""
        if not quote:
            return None
        uri = annotation.target_uri or ""

        page = _page_index(annotation)
        if page is not None:  # PDF annotation
            url = self._resolve_pdf_url(uri)
            clean = clean_pdf_quote(url, page, quote) if url else quote
            return (clean, "ocr") if clean != quote else (quote, "raw")

        if uri.startswith(("http://", "https://")):  # HTML annotation
            clean = _html_normalize(uri, quote)
            if clean and clean != quote:
                return (clean, "html")

        return (quote, "raw")

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
