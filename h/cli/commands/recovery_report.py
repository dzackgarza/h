import click

from h.services import NormalizationService


@click.command("recovery-report")
@click.pass_context
def recovery_report(ctx):
    """Report how stored annotations were recovered, and what still needs repairing.

    Every recovery records the path it took. Source recovery is free; OCR is a paid call
    per annotation, so the share that took it is what moves when a page shape stops being
    recoverable from its own source.
    """
    request = ctx.obj["bootstrap"]()
    paths = request.find_service(NormalizationService).recovery_paths()

    for method, count in sorted(paths.counts.items(), key=lambda item: -item[1]):
        click.echo(f"{method}\t{count}")
    click.echo(f"ocr share\t{paths.ocr_share:.0%}")

    if paths.needs_reconciliation:
        click.echo(
            f"{len(paths.needs_reconciliation)} annotation(s) hold a recovery this "
            f"version would not produce; repair with `h recovery reconcile`:",
            err=True,
        )
        for annotation_id in paths.needs_reconciliation:
            click.echo(f"needs-reconciliation\t{annotation_id}", err=True)
