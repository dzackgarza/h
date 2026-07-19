import pytest

from h.services.normalization import NormalizationService
from h.tasks.annotations import (
    normalize_annotation,
    publish_annotation_event_for_authority,
    sync_annotation_slim,
)


class TestSyncAnnotationSlim:
    AUTHORITY_1 = "AUTHORITY_1"
    AUTHORITY_2 = "AUTHORITY_2"

    USERNAME_1 = "USERNAME_1"
    USERNAME_2 = "USERNAME_2"

    def test_it(self, factories, annotation_write_service, queue_service):
        annotation = factories.Annotation()
        # Some deleted annotations that should not be processed
        factories.Annotation.create_batch(10, deleted=True)
        job = factories.SyncAnnotationJob(annotation=annotation, name="annotation_slim")

        queue_service.get.return_value = [job]

        sync_annotation_slim(1)

        queue_service.get.assert_called_once_with(name="annotation_slim", limit=1)
        annotation_write_service.upsert_annotation_slim.assert_called_once_with(
            annotation
        )
        queue_service.delete.assert_called_once_with([job])

    def test_job_for_missing_annotation(
        self, factories, annotation_write_service, queue_service, db_session
    ):
        annotation = factories.Annotation()
        job = factories.SyncAnnotationJob(annotation=annotation, name="annotation_slim")
        db_session.delete(annotation)
        db_session.commit()

        queue_service.get.return_value = [job]

        sync_annotation_slim(1)

        queue_service.get.assert_called_once_with(name="annotation_slim", limit=1)
        annotation_write_service.upsert_annotation_slim.assert_not_called()
        queue_service.delete.assert_called_once_with([job])

    def test_it_with_no_pending_jobs(self, queue_service, annotation_write_service):
        queue_service.get.return_value = []

        sync_annotation_slim(1)

        annotation_write_service.upsert_annotation_slim.assert_not_called()


class TestPublishAnnotationEventForAuthority:
    def test_it(self, annotation_authority_queue_service):
        publish_annotation_event_for_authority("create", "123")

        annotation_authority_queue_service.publish.assert_called_once_with(
            "create", "123"
        )


class TestNormalizeAnnotation:
    def test_it_normalizes_and_records_a_recovery(
        self, annotation_read_service, normalization_service, log, newrelic
    ):
        annotation = annotation_read_service.get_annotation_by_id.return_value
        row = normalization_service.normalize.return_value
        row.error = None
        row.method = "html"

        normalize_annotation("annotation_id")

        annotation_read_service.get_annotation_by_id.assert_called_once_with(
            "annotation_id"
        )
        normalization_service.normalize.assert_called_once_with(annotation)
        log.info.assert_called_once()
        newrelic.agent.record_custom_metrics.assert_called_once_with(
            [("Custom/NormalizeAnnotation/html", 1)]
        )

    @pytest.mark.usefixtures("annotation_read_service")
    def test_a_raw_result_records_its_metric_without_an_info_log(
        self, normalization_service, log, newrelic
    ):
        row = normalization_service.normalize.return_value
        row.error = None
        row.method = "raw"

        normalize_annotation("annotation_id")

        log.info.assert_not_called()  # raw is the boring default, not worth a line
        newrelic.agent.record_custom_metrics.assert_called_once_with(
            [("Custom/NormalizeAnnotation/raw", 1)]
        )

    @pytest.mark.usefixtures("annotation_read_service")
    def test_a_failure_is_warned_and_metered(
        self, normalization_service, log, newrelic
    ):
        row = normalization_service.normalize.return_value
        row.error = "RuntimeError: boom"

        normalize_annotation("annotation_id")

        log.warning.assert_called_once()
        newrelic.agent.record_custom_metrics.assert_called_once_with(
            [("Custom/NormalizeAnnotation/failed", 1)]
        )

    @pytest.mark.usefixtures("annotation_read_service")
    def test_a_quoteless_annotation_records_nothing(
        self, normalization_service, newrelic
    ):
        normalization_service.normalize.return_value = None

        normalize_annotation("annotation_id")

        newrelic.agent.record_custom_metrics.assert_not_called()

    def test_a_missing_annotation_is_logged_and_skipped(
        self, annotation_read_service, normalization_service, log
    ):
        annotation_read_service.get_annotation_by_id.return_value = None

        normalize_annotation("missing")

        normalization_service.normalize.assert_not_called()
        log.info.assert_called_once()

    @pytest.fixture
    def normalization_service(self, mock_service):
        return mock_service(NormalizationService)

    @pytest.fixture
    def log(self, patch):
        return patch("h.tasks.annotations.log")

    @pytest.fixture(autouse=True)
    def newrelic(self, patch):
        return patch("h.tasks.annotations.newrelic")


@pytest.fixture(autouse=True)
def celery(patch, pyramid_request):
    cel = patch("h.tasks.annotations.celery", autospec=False)
    cel.request = pyramid_request
    return cel
