"""Recover an annotation's display-ready quote synchronously, at intake.

In the create path -- inside the request transaction -- an annotation's flattened text-layer
quote is turned into a quote with the rendered math recovered and stored in
``annotation_normalized``; every view then reads that stored field (the API joins it), and
the raw capture is used only for anchoring. The annotation row is never touched.

Recovery is source-first, OCR-fallback, and fail-hard. An HTML page that exposes the math
source (arXiv LaTeXML, Pandoc ``<span class="math">``) yields the exact authored TeX via the
bundled Node/KaTeX extractor (``method='html'``); otherwise, and for every PDF region, the
rendered region is OCR'd with Mathpix (``method='ocr'``). ``method`` is never ``raw``: a
genuine failure (both source and OCR fail or come back empty) raises ``MathRecoveryError``,
which rolls the create back so no annotation -- and no raw quote -- is ever persisted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sqlalchemy import orm, select

from h.models import Annotation, AnnotationNormalized
from h.models.document import DocumentURI
from h.services.pdf_math import MathRecoveryError, clean_pdf_quote, recovery_timeout

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


def _html_source_extract(uri: str, exact: str) -> str:
    r"""Extract the selection's math from the page's own source via the Node (KaTeX) script.

    Returns the reconstructed quote (exact authored TeX in ``\(..\)`` / ``$$..$$``), or ``""``
    when the page exposes no recoverable math source for the selection -- the signal for the
    caller to fall back to OCR. Raises ``MathRecoveryError`` on a hard failure (page fetch,
    parse, a crashed script, or a timeout); KaTeX reproduces the page's MathJax rendering,
    which pure Python cannot.
    """
    try:
        result = subprocess.run(  # noqa: S603 - fixed script path, args are data
            ["node", str(_HTML_NORMALIZE), uri, exact],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=recovery_timeout(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        msg = f"html-normalize subprocess failed: {exc}"
        raise MathRecoveryError(msg) from exc
    if result.returncode != 0:
        reason = result.stderr.strip()[:500] or f"exit {result.returncode}"
        msg = f"html-normalize failed: {reason}"
        raise MathRecoveryError(msg)
    return result.stdout.strip()


class NormalizationService:
    def __init__(self, session: orm.Session) -> None:
        self._session = session

    def normalize(self, annotation: Annotation) -> AnnotationNormalized | None:
        """Recover ``annotation``'s display quote and add its normalized row to the session.

        Returns the row, or ``None`` for a quote-less annotation (a reply): there is nothing
        to recover, so no row is created. For a quote-bearing annotation the recovery is
        synchronous and fail-hard -- any genuine failure raises ``MathRecoveryError`` and no
        row is added, so the caller's transaction rolls back and nothing persists.
        """
        quote = annotation.quote
        if not quote:
            return None

        recovered, method = self._recover(annotation, quote)

        row = AnnotationNormalized(
            annotation=annotation, normalized_quote=recovered, method=method
        )
        self._session.add(row)
        return row

    def _recover(self, annotation: Annotation, quote: str) -> tuple[str, str]:
        """Recover ``(normalized_quote, method)`` for a quote-bearing annotation, or raise."""
        uri = annotation.target_uri or ""
        page = _page_index(annotation)
        if page is not None:  # PDF annotation
            return (self._recover_pdf(uri, page, quote), "ocr")
        return self._recover_html(uri, quote)

    def _recover_pdf(self, uri: str, page: int, quote: str) -> str:
        """OCR a PDF region into LaTeX, or raise if the PDF URL can't be resolved."""
        url = self._resolve_pdf_url(uri)
        if not url:
            msg = f"could not resolve a fetchable PDF URL for {uri!r}"
            raise MathRecoveryError(msg)
        return clean_pdf_quote(url, page, quote)

    def _recover_html(self, uri: str, quote: str) -> tuple[str, str]:
        """Reconstruct HTML math from the page source; fall back to OCR of the region."""
        source = _html_source_extract(uri, quote)
        if source:
            return (source, "html")
        return (self._ocr_html_region(uri, quote), "ocr")

    def _ocr_html_region(self, uri: str, quote: str) -> str:  # noqa: ARG002
        """OCR a source-less HTML selection's rendered pixels -- an undecided sub-decision.

        LaTeXML/Pandoc HTML exposes math source, so it takes the source path above; a
        source-less HTML page is rare. The pixel source for this fallback (client screenshot
        vs. backend headless render) is not yet decided, so rather than degrade to raw this
        raises -- keeping the never-raw invariant absolute until the mechanism is chosen.
        """
        msg = (
            "source-less HTML OCR fallback is not yet available: the pixel source "
            "(client screenshot vs. backend headless render) is an undecided sub-decision"
        )
        raise MathRecoveryError(msg)

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
