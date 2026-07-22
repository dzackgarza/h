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

import base64
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from sqlalchemy import or_, orm, select

from h.models import Annotation, AnnotationNormalized
from h.models.document import DocumentURI
from h.services.pdf_math import (
    MathRecoveryError,
    clean_pdf_quote,
    ocr_latex,
    recovery_timeout,
)

log = logging.getLogger(__name__)

_HTML_NORMALIZE = (
    Path(__file__).resolve().parents[1] / "scripts" / "html-normalize" / "index.mjs"
)
_HTML_RENDER = (
    Path(__file__).resolve().parents[1] / "scripts" / "html-normalize" / "ocr.mjs"
)


@dataclass(frozen=True)
class ReconciliationResult:
    normalized: int
    failures: list[tuple[str, str]]


def _node_path() -> str:
    """Absolute path of the ``node`` executable, or a loud recovery failure.

    Node runs both recovery scripts; a deployment without it cannot normalize anything,
    so its absence is a ``MathRecoveryError`` (rolling the create back), never a silent
    skip.
    """
    node = shutil.which("node")
    if not node:
        msg = "node executable not found; HTML math recovery is unavailable"
        raise MathRecoveryError(msg)
    return node


def _selectors(annotation: Annotation) -> list[dict]:
    """Return the annotation's target selectors as a list (legacy ``Column`` typing)."""
    return cast("list[dict]", annotation.target_selectors or [])


def _page_index(annotation: Annotation) -> int | None:
    """Read the 0-based page from a ``PageSelector``, or ``None`` for a non-PDF annotation.

    This is what routes an annotation to PDF OCR rather than HTML math recovery.
    """
    for selector in _selectors(annotation):
        if isinstance(selector, dict) and selector.get("type") == "PageSelector":
            index = selector.get("index")
            if isinstance(index, int):
                return index
    return None


def _quote_context(annotation: Annotation) -> tuple[str, str]:
    for selector in _selectors(annotation):
        if isinstance(selector, dict) and selector.get("type") == "TextQuoteSelector":
            return (selector.get("prefix", ""), selector.get("suffix", ""))
    return ("", "")


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
            [_node_path(), str(_HTML_NORMALIZE), uri, exact],
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


def _render_html_quote(uri: str, exact: str) -> bytes:
    """Render the selected HTML range in headless Chromium and return its PNG."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed script path, args are data
            [
                _node_path(),
                str(_HTML_RENDER),
                uri,
                exact,
                str(int(recovery_timeout() * 1000)),
            ],
            capture_output=True,
            text=True,
            timeout=recovery_timeout() + 5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        msg = f"HTML rendered-region capture failed: {exc}"
        raise MathRecoveryError(msg) from exc
    if result.returncode != 0:
        reason = result.stderr.strip()[:500] or f"exit {result.returncode}"
        msg = f"HTML rendered-region capture failed: {reason}"
        raise MathRecoveryError(msg)
    try:
        return base64.b64decode(result.stdout, validate=True)
    except ValueError as exc:
        msg = "HTML rendered-region capture returned invalid PNG data"
        raise MathRecoveryError(msg) from exc


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

        started = time.perf_counter()
        try:
            recovered, method = self._recover(annotation, quote)
        except MathRecoveryError as exc:
            # The outcome of every recovery is logged for observability; a failure is the
            # loud half of that (it also rolls the create back, so nothing persists).
            log.warning(
                "math normalization failed for annotation %s after %.0fms: %s",
                annotation.id,
                (time.perf_counter() - started) * 1000,
                exc,
            )
            raise

        latency_ms = (time.perf_counter() - started) * 1000
        log.info(
            "normalized annotation %s via %s in %.0fms",
            annotation.id,
            method,
            latency_ms,
        )
        row = AnnotationNormalized(
            annotation=annotation, normalized_quote=recovered, method=method
        )
        self._session.add(row)
        return row

    def normalize_missing(self, limit: int | None = None) -> int:
        """Normalize existing quote-bearing annotations that have no normalized row.

        This is the reconciliation path for annotations created before synchronous
        normalization moved into ``h``. A recovery failure is not downgraded or skipped: it
        aborts the caller's transaction with the same ``MathRecoveryError`` as a new create.
        """
        normalized = 0
        for annotation in self._missing_annotations(limit):
            if self.normalize(annotation) is not None:
                normalized += 1
        return normalized

    def reconcile_missing(self, limit: int | None = None) -> ReconciliationResult:
        """Normalize missing rows, and repair rows produced by invalid old methods."""
        normalized = 0
        failures = []
        for annotation in self._reconciliation_candidates(limit):
            try:
                with self._session.begin_nested():
                    row = self._reconcile(annotation)
            except MathRecoveryError as exc:
                failures.append((annotation.id, str(exc)))
            else:
                if row is not None:
                    normalized += 1
        return ReconciliationResult(normalized=normalized, failures=failures)

    def _reconcile(self, annotation: Annotation) -> AnnotationNormalized | None:
        if annotation.normalized is None:
            return self.normalize(annotation)

        quote = annotation.quote
        if not quote:
            return None
        recovered, method = self._recover(annotation, quote)
        annotation.normalized.normalized_quote = recovered
        annotation.normalized.method = method
        return annotation.normalized

    def _reconciliation_candidates(self, limit: int | None):
        statement = (
            select(Annotation)
            .outerjoin(AnnotationNormalized)
            .where(
                or_(
                    AnnotationNormalized.id.is_(None),
                    AnnotationNormalized.method.in_(("raw", "identity")),
                )
            )
            .order_by(Annotation.created)
        )
        count = 0
        for annotation in self._session.scalars(statement):
            normalized = annotation.normalized
            if (
                normalized is not None
                and normalized.method == "identity"
                and _page_index(annotation) is None
            ):
                continue
            yield annotation
            count += 1
            if limit is not None and count >= limit:
                return

    def _missing_annotations(self, limit: int | None):
        statement = (
            select(Annotation)
            .outerjoin(AnnotationNormalized)
            .where(AnnotationNormalized.id.is_(None))
            .order_by(Annotation.created)
        )
        if limit is not None:
            statement = statement.limit(limit)
        return self._session.scalars(statement)

    def _recover(self, annotation: Annotation, quote: str) -> tuple[str, str]:
        """Recover ``(normalized_quote, method)`` for a quote-bearing annotation, or raise."""
        uri = annotation.target_uri or ""
        page = _page_index(annotation)
        if page is not None:  # PDF annotation
            return (self._recover_pdf(annotation, uri, page, quote), "ocr")
        return self._recover_html(uri, quote)

    def _recover_pdf(
        self, annotation: Annotation, uri: str, page: int, quote: str
    ) -> str:
        """OCR a PDF region into LaTeX, or raise if the PDF URL can't be resolved."""
        url = self._resolve_pdf_url(uri)
        if not url:
            msg = f"could not resolve a fetchable PDF URL for {uri!r}"
            raise MathRecoveryError(msg)
        prefix, suffix = _quote_context(annotation)
        return clean_pdf_quote(url, page, quote, prefix=prefix, suffix=suffix)

    def _recover_html(self, uri: str, quote: str) -> tuple[str, str]:
        """Reconstruct HTML math from the page source; fall back to OCR of the region."""
        source = _html_source_extract(uri, quote)
        if source:
            if source == quote:
                return (quote, "identity")
            return (source, "html")
        return (self._ocr_html_region(uri, quote), "ocr")

    def _ocr_html_region(self, uri: str, quote: str) -> str:
        """OCR a source-less HTML selection from a backend Chromium rendering."""
        recovered = ocr_latex(_render_html_quote(uri, quote))
        if not recovered:
            msg = "OCR returned empty output for the rendered HTML selection"
            raise MathRecoveryError(msg)
        return recovered

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
