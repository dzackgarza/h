from datetime import datetime
from unittest import mock
from unittest.mock import sentinel
from urllib.parse import quote

import pytest

from h.models import AnnotationNormalized
from h.services.normalization import NormalizationService, factory
from h.services.pdf_math import MathRecoveryError


class TestNormalize:
    def test_normalize_missing_backfills_only_quote_bearing_rows_without_one(
        self, svc, html_source_extract, factories, db_session
    ):
        html_source_extract.side_effect = lambda _uri, exact: exact
        missing = self.annotation(factories, "plain existing prose", "https://ex.com/a")
        existing = self.annotation(factories, "already normalized", "https://ex.com/b")
        factories.AnnotationNormalized(
            annotation=existing,
            normalized_quote="already normalized",
            method="identity",
        )
        factories.Annotation(
            target_uri="https://ex.com/reply", target_selectors=[]
        )
        db_session.flush()

        assert svc.normalize_missing() == 1
        db_session.flush()

        assert missing.normalized.normalized_quote == "plain existing prose"
        assert missing.normalized.method == "identity"
        assert existing.normalized.method == "identity"

    def test_reconcile_missing_commits_recoverable_rows_and_reports_failures(
        self, svc, html_source_extract, factories, db_session
    ):
        failed = self.annotation(factories, "unrecoverable math", "https://ex.com/a")
        failed.created = datetime(2026, 1, 1)
        recovered = self.annotation(factories, "plain existing prose", "https://ex.com/b")
        recovered.created = datetime(2026, 1, 2)
        db_session.flush()
        html_source_extract.side_effect = [
            MathRecoveryError("source unavailable"),
            "plain existing prose",
        ]

        result = svc.reconcile_missing()
        db_session.flush()

        assert result.normalized == 1
        assert result.failures == [(failed.id, "source unavailable")]
        assert failed.normalized is None
        assert recovered.normalized.method == "identity"

    def test_reconcile_repairs_invalid_existing_normalizations(
        self,
        svc,
        html_source_extract,
        clean_pdf_quote,
        factories,
        db_session,
    ):
        raw_html = self.annotation(
            factories, "the moduli M here", "https://ex.com/math"
        )
        factories.AnnotationNormalized(
            annotation=raw_html,
            normalized_quote="the moduli M here",
            method="raw",
        )
        pdf_identity = self.annotation(
            factories,
            "Let OK have field of fractions K",
            "https://ex.com/paper.pdf",
            page=1,
        )
        factories.AnnotationNormalized(
            annotation=pdf_identity,
            normalized_quote="Let OK have field of fractions K",
            method="identity",
        )
        valid_html_identity = self.annotation(
            factories, "ordinary prose", "https://ex.com/prose"
        )
        factories.AnnotationNormalized(
            annotation=valid_html_identity,
            normalized_quote="ordinary prose",
            method="identity",
        )
        db_session.flush()
        html_source_extract.return_value = r"the moduli $\mathcal M$ here"
        clean_pdf_quote.return_value = r"Let $O_K$ have field of fractions $K$"

        result = svc.reconcile_missing()
        db_session.flush()

        assert result.normalized == 2
        assert result.failures == []
        assert raw_html.normalized.normalized_quote == r"the moduli $\mathcal M$ here"
        assert raw_html.normalized.method == "html"
        assert (
            pdf_identity.normalized.normalized_quote
            == r"Let $O_K$ have field of fractions $K$"
        )
        assert pdf_identity.normalized.method == "ocr"
        assert valid_html_identity.normalized.method == "identity"

    def test_mathless_html_annotation_is_its_own_normalized_quote(
        self, svc, factories, db_session
    ):
        exact = "A higher category is modeled by a quasicategory"
        html = f"<html><body><main><p>{exact}</p></main></body></html>"
        uri = f"data:text/html,{quote(html)}"
        annotation = self.annotation(factories, exact, uri)

        row = svc.normalize(annotation)
        db_session.flush()

        assert row.normalized_quote == exact
        assert row.method == "identity"

    def test_html_annotation_recovers_math_from_the_page_source(
        self, svc, html_source_extract, factories, db_session
    ):
        html_source_extract.return_value = r"the moduli $\mathcal{M}$ here"
        annotation = self.annotation(factories, "the moduli M here", "https://ex.com/p")

        row = svc.normalize(annotation)
        db_session.flush()

        html_source_extract.assert_called_once_with(
            "https://ex.com/p", "the moduli M here"
        )
        assert row.normalized_quote == r"the moduli $\mathcal{M}$ here"
        assert row.method == "html"
        assert row in db_session

    def test_source_less_html_falls_back_to_rendered_region_ocr(
        self,
        svc,
        html_source_extract,
        render_html_quote,
        ocr_latex,
        factories,
        db_session,
    ):
        html_source_extract.return_value = ""
        render_html_quote.return_value = b"rendered selection PNG"
        ocr_latex.return_value = r"the space $\mathcal{M}$ here"
        annotation = self.annotation(factories, "the space M here", "https://ex.com/p")

        row = svc.normalize(annotation)
        db_session.flush()

        render_html_quote.assert_called_once_with("https://ex.com/p", "the space M here")
        ocr_latex.assert_called_once_with(b"rendered selection PNG")
        assert row.normalized_quote == r"the space $\mathcal{M}$ here"
        assert row.method == "ocr"

    def test_html_subprocess_failure_propagates_and_adds_no_row(
        self, svc, html_source_extract, factories, db_session
    ):
        html_source_extract.side_effect = MathRecoveryError(
            "html-normalize failed: fetch failed"
        )
        annotation = self.annotation(factories, "the moduli M here", "https://ex.com/p")

        with pytest.raises(MathRecoveryError, match="fetch failed"):
            svc.normalize(annotation)

        assert self.rows(db_session, annotation) == 0

    def test_pdf_annotation_is_ocred(self, svc, clean_pdf_quote, factories, db_session):
        clean_pdf_quote.return_value = r"2K $\sim$ 0"
        annotation = self.annotation(
            factories, "2K ∼ 0", "https://ex.com/paper.pdf", page=3
        )
        annotation.target_selectors[0].update(
            {"prefix": "They satisfy", "suffix": "and continue"}
        )

        row = svc.normalize(annotation)
        db_session.flush()

        clean_pdf_quote.assert_called_once_with(
            "https://ex.com/paper.pdf",
            3,
            "2K ∼ 0",
            prefix="They satisfy",
            suffix="and continue",
        )
        assert row.normalized_quote == r"2K $\sim$ 0"
        assert row.method == "ocr"
        assert row in db_session

    def test_every_pdf_annotation_is_ocred_even_if_selected_glyphs_look_like_prose(
        self, svc, clean_pdf_quote, factories, db_session
    ):
        exact = "Let OK be a DVR with field of fractions K and residue field k"
        clean_pdf_quote.return_value = (
            r"Let $O_K$ be a DVR with field of fractions $K$ and residue field $k$"
        )
        annotation = self.annotation(
            factories, exact, "https://ex.com/paper.pdf", page=3
        )

        row = svc.normalize(annotation)
        db_session.flush()

        clean_pdf_quote.assert_called_once_with(
            "https://ex.com/paper.pdf", 3, exact, prefix="", suffix=""
        )
        assert (
            row.normalized_quote
            == r"Let $O_K$ be a DVR with field of fractions $K$ and residue field $k$"
        )
        assert row.method == "ocr"
        assert row in db_session

    def test_pdf_recovery_failure_propagates_and_adds_no_row(
        self, svc, clean_pdf_quote, factories, db_session
    ):
        clean_pdf_quote.side_effect = MathRecoveryError("OCR returned empty output")
        annotation = self.annotation(
            factories, "2K ∼ 0", "https://ex.com/paper.pdf", page=1
        )

        with pytest.raises(MathRecoveryError, match="empty output"):
            svc.normalize(annotation)

        assert self.rows(db_session, annotation) == 0

    def test_a_pdf_whose_url_cannot_be_resolved_raises(
        self, svc, factories, db_session
    ):
        annotation = self.annotation(factories, "2K ∼ 0", "urn:x-pdf:NOSUCHDOC", page=0)

        with pytest.raises(MathRecoveryError, match="resolve a fetchable PDF URL"):
            svc.normalize(annotation)

        assert self.rows(db_session, annotation) == 0

    def test_a_successful_recovery_logs_the_method_and_latency(
        self, svc, html_source_extract, factories, caplog
    ):
        html_source_extract.return_value = r"the moduli $\mathcal{M}$ here"
        annotation = self.annotation(factories, "the moduli M here", "https://ex.com/p")

        with caplog.at_level("INFO", logger="h.services.normalization"):
            svc.normalize(annotation)

        assert any(
            "via html" in r.message and "ms" in r.message for r in caplog.records
        )

    def test_a_failed_recovery_logs_the_reason(
        self, svc, clean_pdf_quote, factories, caplog
    ):
        clean_pdf_quote.side_effect = MathRecoveryError("OCR returned empty output")
        annotation = self.annotation(
            factories, "2K ∼ 0", "https://ex.com/paper.pdf", page=1
        )

        with caplog.at_level("WARNING", logger="h.services.normalization"):  # noqa: SIM117
            with pytest.raises(MathRecoveryError):
                svc.normalize(annotation)

        assert any(
            "failed" in r.message and "OCR returned empty output" in r.message
            for r in caplog.records
        )

    def test_a_quote_less_annotation_gets_no_row(self, svc, factories):
        # A reply carries no selection; there is nothing to recover, so no row (no raise).
        annotation = factories.Annotation(
            target_uri="https://ex.com/p", target_selectors=[]
        )

        assert svc.normalize(annotation) is None

    def annotation(self, factories, quote, uri, page=None):
        selectors = [{"type": "TextQuoteSelector", "exact": quote}]
        if page is not None:
            selectors.append({"type": "PageSelector", "index": page})
        return factories.Annotation(target_uri=uri, target_selectors=selectors)

    def rows(self, db_session, annotation) -> int:
        db_session.flush()
        return (
            db_session.query(AnnotationNormalized)
            .filter_by(annotation_id=annotation.id)
            .count()
        )

    @pytest.fixture
    def svc(self, db_session):
        return NormalizationService(db_session)

    @pytest.fixture
    def html_source_extract(self, patch):
        return patch("h.services.normalization._html_source_extract")

    @pytest.fixture
    def clean_pdf_quote(self, patch):
        return patch("h.services.normalization.clean_pdf_quote")

    @pytest.fixture
    def render_html_quote(self, patch):
        return patch("h.services.normalization._render_html_quote")

    @pytest.fixture
    def ocr_latex(self, patch):
        return patch("h.services.normalization._ocr_latex")


class TestResolvePdfUrl:
    def test_http_uri_is_returned_directly(self, svc):
        assert svc._resolve_pdf_url("https://ex.com/a.pdf") == "https://ex.com/a.pdf"  # noqa: SLF001

    def test_a_non_pdf_urn_resolves_to_nothing(self, svc):
        assert svc._resolve_pdf_url("urn:x-something:else") is None  # noqa: SLF001

    def test_a_pdf_fingerprint_resolves_to_its_http_sibling(self, svc, factories):
        document = factories.Document()
        factories.DocumentURI(document=document, uri="urn:x-pdf:THEFINGERPRINT")
        factories.DocumentURI(document=document, uri="https://ex.com/source.pdf")

        resolved = svc._resolve_pdf_url("urn:x-pdf:THEFINGERPRINT")  # noqa: SLF001

        assert resolved == "https://ex.com/source.pdf"

    def test_an_unknown_fingerprint_resolves_to_nothing(self, svc):
        assert svc._resolve_pdf_url("urn:x-pdf:NOSUCHDOC") is None  # noqa: SLF001

    @pytest.fixture
    def svc(self, db_session):
        return NormalizationService(db_session)


class TestFactory:
    def test_it(self):
        request = mock.Mock()

        svc = factory(sentinel.context, request)

        assert isinstance(svc, NormalizationService)
        assert svc._session == request.db  # noqa: SLF001
