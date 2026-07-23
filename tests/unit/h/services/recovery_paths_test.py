"""What the deployment can see about how its annotations were recovered.

Every recovery already records which path produced it -- ``annotation_normalized.method`` --
and nothing has ever read that column. It is the only place the cost and the health of the
recovery are visible: a page shape that stops being recoverable from source does not fail
anything, it just starts routing to the paid OCR path, and the column is where that shows.

The query runs against the real table. It reports on the whole deployment, so these assert
what each test itself put there -- what it added to the counts, and which of its own rows
the repair list picked up -- rather than the table's totals, which belong to whatever else
the suite has created.
"""

import uuid

import pytest

from h.services.normalization import NormalizationService, RecoveryPaths


# The report reads every annotation in the deployment. Against a database several workers
# are writing to at once, that read and their inserts deadlock each other, so this module's
# tests are pinned to one worker and run together rather than alongside the writes.
@pytest.mark.xdist_group("recovery-paths")
class TestRecoveryPaths:
    def test_it_counts_the_path_every_annotation_was_recovered_by(
        self, svc, factories, db_session
    ):
        before = svc.recovery_paths().counts
        self.recovered(factories, "html", "https://ex.com/ar5iv")
        self.recovered(factories, "html", "https://ex.com/mse")
        self.recovered(factories, "ocr", "https://ex.com/paper.pdf", page=3)
        self.recovered(factories, "identity", "https://ex.com/prose")
        db_session.flush()

        after = svc.recovery_paths().counts

        added = {
            method: after[method] - before.get(method, 0)
            for method in ("html", "ocr", "identity")
        }
        assert added == {"html": 2, "ocr": 1, "identity": 1}

    def test_it_lists_exactly_the_rows_reconciliation_would_repair(
        self, svc, factories, db_session
    ):
        # The report's second job: say which stored recoveries the current contract would
        # no longer produce. `raw` is the retired pre-h method, and `identity` on a PDF is
        # invalid because every PDF region goes through OCR. Both are exactly what
        # `reconcile_missing` selects -- the report must not drift from the repair.
        retired = self.recovered(factories, "raw", "https://ex.com/legacy")
        pdf_identity = self.recovered(
            factories, "identity", "https://ex.com/paper.pdf", page=2
        )
        html_recovered = self.recovered(factories, "html", "https://ex.com/ar5iv")
        prose = self.recovered(factories, "identity", "https://ex.com/prose")
        ocr_recovered = self.recovered(factories, "ocr", "https://ex.com/other.pdf", 1)
        db_session.flush()

        stale = set(svc.recovery_paths().needs_reconciliation)

        assert {retired.id, pdf_identity.id} <= stale
        assert stale.isdisjoint({html_recovered.id, prose.id, ocr_recovered.id})

    def test_a_quote_bearing_annotation_with_no_recovery_at_all_is_reported(
        self, svc, factories, db_session
    ):
        # A legacy row with no normalized companion shows the reader an error in the
        # sidebar. It is the same repair, so it belongs in the same list.
        orphan = self.annotation(
            factories, "an unrecovered quote", "https://ex.com/old"
        )
        db_session.flush()

        assert orphan.id in svc.recovery_paths().needs_reconciliation

    def test_the_paid_share_is_the_fraction_that_took_the_third_party_call(self):
        # The number an operator watches: source recovery is free, OCR is a call per
        # annotation, so a jump here is the first symptom of a page shape that stopped
        # being recoverable from its own source.
        paths = RecoveryPaths(counts={"ocr": 3, "html": 1}, needs_reconciliation=[])

        assert paths.ocr_share == 0.75

    def test_a_deployment_that_has_recovered_nothing_reports_no_paid_share(self):
        # Rather than dividing by zero on a fresh deployment.
        assert RecoveryPaths(counts={}, needs_reconciliation=[]).ocr_share == 0.0

    def test_a_deployment_that_has_never_paid_reports_a_zero_share(self):
        paths = RecoveryPaths(
            counts={"html": 4, "identity": 2}, needs_reconciliation=[]
        )

        assert paths.ocr_share == 0.0

    def annotation(self, factories, quote, uri, page=None):
        # Every URI is unique to its test: creating an annotation creates a `document_uri`
        # row, and two parallel workers inserting the same URI deadlock on its unique index.
        uri = f"{uri}#{uuid.uuid4().hex}"
        selectors = [{"type": "TextQuoteSelector", "exact": quote}]
        if page is not None:
            selectors.append({"type": "PageSelector", "index": page})
        return factories.Annotation(target_uri=uri, target_selectors=selectors)

    def recovered(self, factories, method, uri, page=None):
        annotation = self.annotation(factories, f"a quote from {uri}", uri, page=page)
        factories.AnnotationNormalized(
            annotation=annotation,
            normalized_quote=f"recovered by {method}",
            method=method,
        )
        return annotation

    @pytest.fixture
    def svc(self, db_session):
        return NormalizationService(db_session)
