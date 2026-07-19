from unittest import mock
from unittest.mock import sentinel

import pytest

from h.models import AnnotationNormalized
from h.services.normalization import NormalizationService, factory


class TestNormalize:
    def test_html_annotation_recovers_math_via_the_node_normalizer(
        self, svc, html_normalize, factories
    ):
        html_normalize.return_value = r"the moduli \(\mathcal{M}\) here"
        annotation = self.annotation(factories, "the moduli M here", "https://ex.com/p")

        row = svc.normalize(annotation)

        html_normalize.assert_called_once_with("https://ex.com/p", "the moduli M here")
        assert row.normalized_quote == r"the moduli \(\mathcal{M}\) here"
        assert row.method == "html"

    def test_html_annotation_with_no_recoverable_math_stores_raw(
        self, svc, html_normalize, factories
    ):
        html_normalize.return_value = ""  # the Node normalizer found no math
        annotation = self.annotation(factories, "plain prose", "https://ex.com/p")

        row = svc.normalize(annotation)

        assert row.normalized_quote == "plain prose"
        assert row.method == "raw"

    def test_pdf_annotation_is_ocred(self, svc, clean_pdf_quote, factories):
        clean_pdf_quote.return_value = r"2K \(\sim\) 0"
        annotation = self.annotation(
            factories, "2K ~ 0", "https://ex.com/paper.pdf", page=3
        )

        row = svc.normalize(annotation)

        clean_pdf_quote.assert_called_once_with("https://ex.com/paper.pdf", 3, "2K ~ 0")
        assert row.normalized_quote == r"2K \(\sim\) 0"
        assert row.method == "ocr"

    def test_pdf_annotation_with_no_recovery_stores_raw(
        self, svc, clean_pdf_quote, factories
    ):
        clean_pdf_quote.return_value = "2K ~ 0"  # OCR recovered nothing new
        annotation = self.annotation(
            factories, "2K ~ 0", "https://ex.com/paper.pdf", page=0
        )

        row = svc.normalize(annotation)

        assert row.method == "raw"

    def test_an_annotation_with_no_quote_gets_no_row(self, svc, factories):
        annotation = factories.Annotation(
            target_uri="https://ex.com/p", target_selectors=[]
        )

        assert svc.normalize(annotation) is None

    def test_it_replaces_a_previous_result(
        self, svc, html_normalize, factories, db_session
    ):
        annotation = self.annotation(factories, "the M here", "https://ex.com/p")
        factories.AnnotationNormalized(
            annotation=annotation, normalized_quote="the M here", method="raw"
        )
        html_normalize.return_value = r"the \(\mathcal{M}\) here"

        svc.normalize(annotation)
        db_session.flush()

        rows = (
            db_session.query(AnnotationNormalized)
            .filter_by(annotation_id=annotation.id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].method == "html"

    def annotation(self, factories, quote, uri, page=None):
        selectors = [{"type": "TextQuoteSelector", "exact": quote}]
        if page is not None:
            selectors.append({"type": "PageSelector", "index": page})
        return factories.Annotation(target_uri=uri, target_selectors=selectors)

    @pytest.fixture
    def svc(self, db_session):
        return NormalizationService(db_session)

    @pytest.fixture
    def html_normalize(self, patch):
        return patch("h.services.normalization._html_normalize")

    @pytest.fixture
    def clean_pdf_quote(self, patch):
        return patch("h.services.normalization.clean_pdf_quote")


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
