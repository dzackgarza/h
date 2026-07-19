import pytest

from h.cli.commands import normalize_annotations


class TestEnqueueMissing:
    def test_it_enqueues_only_annotations_without_a_normalized_row(
        self, pyramid_request, factories, normalize_annotation
    ):
        needs_it = factories.Annotation()
        already_done = factories.Annotation()
        factories.AnnotationNormalized(annotation=already_done)
        pyramid_request.db.flush()

        count = normalize_annotations.enqueue_missing(pyramid_request)

        assert count == 1
        normalize_annotation.delay.assert_called_once_with(needs_it.id)

    def test_it_skips_deleted_annotations(
        self, pyramid_request, factories, normalize_annotation
    ):
        factories.Annotation(deleted=True)
        pyramid_request.db.flush()

        assert normalize_annotations.enqueue_missing(pyramid_request) == 0
        normalize_annotation.delay.assert_not_called()

    def test_it_honours_the_limit(self, pyramid_request, factories):
        factories.Annotation.create_batch(3)
        pyramid_request.db.flush()

        assert normalize_annotations.enqueue_missing(pyramid_request, limit=2) == 2

    @pytest.fixture(autouse=True)
    def normalize_annotation(self, patch):
        return patch("h.cli.commands.normalize_annotations.normalize_annotation")
