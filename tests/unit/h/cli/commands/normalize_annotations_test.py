from unittest import mock

import pytest

from h.cli.commands import normalize_annotations as normalize_annotations_cli
from h.services import NormalizationService


def test_command_reconciles_missing_normalized_quotes(cli, cliconfig, pyramid_request):
    service = mock.Mock()
    service.normalize_missing.return_value = 3
    pyramid_request.find_service.return_value = service

    result = cli.invoke(
        normalize_annotations_cli.normalize_annotations,
        ["--limit", "25"],
        obj=cliconfig,
    )

    assert result.exit_code == 0
    pyramid_request.find_service.assert_called_once_with(NormalizationService)
    service.normalize_missing.assert_called_once_with(limit=25)
    pyramid_request.tm.commit.assert_called_once_with()
    assert result.output == "normalized 3 existing annotation(s)\n"


@pytest.fixture
def cliconfig(pyramid_request):
    pyramid_request.tm = mock.Mock()
    return {"bootstrap": mock.Mock(return_value=pyramid_request)}
