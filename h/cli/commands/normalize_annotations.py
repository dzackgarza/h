import click

from h.services import NormalizationService


@click.command("normalize-annotations")
@click.option("--limit", type=click.IntRange(min=1), default=None)
@click.pass_context
def normalize_annotations(ctx, limit):
    """Backfill display-ready quotes for annotations created before normalization."""
    request = ctx.obj["bootstrap"]()
    count = request.find_service(NormalizationService).normalize_missing(limit=limit)
    request.tm.commit()
    click.echo(f"normalized {count} existing annotation(s)")
