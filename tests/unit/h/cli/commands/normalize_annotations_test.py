from unittest import mock

import pytest

from h.cli.commands import normalize_annotations as normalize_annotations_cli
from h.services import NormalizationService
from h.services.normalization import ReconciliationResult


def test_command_reconciles_missing_normalized_quotes(cli, cliconfig, pyramid_request):
    service = mock.Mock()
    service.reconcile_missing.return_value = ReconciliationResult(
        normalized=3, failures=[]
    )
    pyramid_request.find_service = mock.Mock(return_value=service)

    result = cli.invoke(
        normalize_annotations_cli.normalize_annotations,
        ["--limit", "25"],
        obj=cliconfig,
    )

    assert result.exit_code == 0
    pyramid_request.find_service.assert_called_once_with(NormalizationService)
    service.reconcile_missing.assert_called_once_with(limit=25)
    pyramid_request.tm.commit.assert_called_once_with()
    assert result.output == "normalized 3 existing annotation(s)\n"


def test_command_commits_successes_and_reports_every_failure(
    cli, cliconfig, pyramid_request
):
    service = mock.Mock()
    service.reconcile_missing.return_value = ReconciliationResult(
        normalized=2,
        failures=[("ann-1", "source unavailable"), ("ann-2", "OCR timed out")],
    )
    pyramid_request.find_service = mock.Mock(return_value=service)

    result = cli.invoke(normalize_annotations_cli.normalize_annotations, obj=cliconfig)

    assert result.exit_code != 0
    pyramid_request.tm.commit.assert_called_once_with()
    assert "normalized 2 existing annotation(s)" in result.output
    assert "failed\tann-1\tsource unavailable" in result.output
    assert "failed\tann-2\tOCR timed out" in result.output
    assert "2 annotation(s) remain incorrectly normalized" in result.output


@pytest.fixture
def cliconfig(pyramid_request):
    pyramid_request.tm = mock.Mock()
    return {"bootstrap": mock.Mock(return_value=pyramid_request)}
