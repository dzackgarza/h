import click

from h.search import config
from h.search.index import BatchIndexer


@click.group()
def search():
    """Manage search index."""


@search.command("update-settings")
@click.pass_context
def update_settings(ctx):
    """
    Attempt to update mappings and settings in elasticsearch.

    Attempts to update mappings and index settings. This may fail if the
    pending changes to mappings are not compatible with the current index. In
    this case you will likely need to reindex.
    """
    request = ctx.obj["bootstrap"]()

    try:
        config.update_index_settings(request.es)
    except RuntimeError as exc:
        raise click.ClickException(str(exc))  # noqa: B904


@search.command("reindex")
@click.pass_context
def reindex(ctx):
    """Reindex every non-deleted annotation from PostgreSQL into Elasticsearch."""
    request = ctx.obj["bootstrap"]()
    errored_ids = BatchIndexer(request.db, request.es, request).index(None)
    request.es.conn.indices.refresh(index=request.es.index)

    if errored_ids:
        for annotation_id in sorted(errored_ids):
            click.echo(f"failed\t{annotation_id}", err=True)
        msg = f"{len(errored_ids)} annotation(s) failed to reindex"
        raise click.ClickException(msg)

    click.echo("reindexed every annotation")
