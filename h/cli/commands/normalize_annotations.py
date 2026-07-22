import click

from h.services import NormalizationService


@click.command("normalize-annotations")
@click.option("--limit", type=click.IntRange(min=1), default=None)
@click.pass_context
def normalize_annotations(ctx, limit):
    """Backfill missing display quotes and repair invalid legacy normalizations."""
    request = ctx.obj["bootstrap"]()
    result = request.find_service(NormalizationService).reconcile_missing(limit=limit)
    request.tm.commit()
    click.echo(f"normalized {result.normalized} existing annotation(s)")
    if result.failures:
        for annotation_id, reason in result.failures:
            click.echo(f"failed\t{annotation_id}\t{reason}", err=True)
        msg = f"{len(result.failures)} annotation(s) remain incorrectly normalized"
        raise click.ClickException(msg)
