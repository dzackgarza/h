import click
from sqlalchemy import select

from h.models import Annotation, AnnotationNormalized
from h.tasks.annotations import normalize_annotation


@click.command("normalize-annotations")
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Max annotations to enqueue (default: all that lack a row).",
)
@click.pass_context
def normalize_annotations(ctx, limit):
    """Backfill math-normalization for annotations that have no normalized row yet.

    New annotations are enriched at intake off the annotation event; this catches any that
    predate that trigger (or were missed while the worker was down) by enqueuing them onto
    the same task. Idempotent: an annotation that already has a row is skipped, and
    re-running one just replaces its row.
    """
    request = ctx.obj["bootstrap"]()
    count = enqueue_missing(request, limit)
    click.echo(f"Enqueued {count} annotation(s) for normalization.")


def enqueue_missing(request, limit=None):
    """Enqueue every not-deleted annotation with no normalized row; return the count."""
    query = (
        select(Annotation.id)
        .outerjoin(
            AnnotationNormalized,
            AnnotationNormalized.annotation_id == Annotation.id,
        )
        .where(AnnotationNormalized.id.is_(None), Annotation.deleted.is_(False))
    )
    if limit:
        query = query.limit(limit)

    annotation_ids = request.db.scalars(query).all()
    for annotation_id in annotation_ids:
        normalize_annotation.delay(annotation_id)
    return len(annotation_ids)
