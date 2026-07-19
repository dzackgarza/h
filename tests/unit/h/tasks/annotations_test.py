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
    def test_it(self, annotation_read_service, normalization_service):
        annotation = annotation_read_service.get_annotation_by_id.return_value

        normalize_annotation("annotation_id")

        annotation_read_service.get_annotation_by_id.assert_called_once_with(
            "annotation_id"
        )
        normalization_service.normalize.assert_called_once_with(annotation)

    def test_it_does_nothing_for_a_missing_annotation(
        self, annotation_read_service, normalization_service
    ):
        annotation_read_service.get_annotation_by_id.return_value = None

        normalize_annotation("missing")

        normalization_service.normalize.assert_not_called()

    @pytest.fixture
    def normalization_service(self, mock_service):
        return mock_service(NormalizationService)


@pytest.fixture(autouse=True)
def celery(patch, pyramid_request):
    cel = patch("h.tasks.annotations.celery", autospec=False)
    cel.request = pyramid_request
    return cel
