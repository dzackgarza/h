from unittest import mock

import pytest

from h.cli.commands import recovery_report as recovery_report_cli
from h.services import NormalizationService
from h.services.normalization import RecoveryPaths


def test_it_reports_the_distribution_and_the_paid_share(
    cli, cliconfig, pyramid_request
):
    service = mock.Mock()
    service.recovery_paths.return_value = RecoveryPaths(
        counts={"html": 9, "ocr": 3}, needs_reconciliation=[]
    )
    pyramid_request.find_service = mock.Mock(return_value=service)

    result = cli.invoke(recovery_report_cli.recovery_report, obj=cliconfig)

    assert result.exit_code == 0
    pyramid_request.find_service.assert_called_once_with(NormalizationService)
    assert result.output == "html\t9\nocr\t3\nocr share\t25%\n"


def test_it_names_every_annotation_whose_recovery_this_version_would_not_produce(
    cli, cliconfig, pyramid_request
):
    # The repair list is the actionable half: each id is an annotation whose stored quote
    # was produced by a method the current contract no longer writes.
    service = mock.Mock()
    service.recovery_paths.return_value = RecoveryPaths(
        counts={"raw": 2}, needs_reconciliation=["ann-1", "ann-2"]
    )
    pyramid_request.find_service = mock.Mock(return_value=service)

    result = cli.invoke(recovery_report_cli.recovery_report, obj=cliconfig)

    assert "needs-reconciliation\tann-1" in result.output
    assert "needs-reconciliation\tann-2" in result.output


@pytest.fixture
def cliconfig(pyramid_config, pyramid_request):  # noqa: ARG001
    pyramid_request.bootstrap = mock.Mock(return_value=pyramid_request)
    return {"bootstrap": mock.Mock(return_value=pyramid_request)}
