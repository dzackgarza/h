from unittest import mock

import pytest

from h.cli.commands import search

pytestmark = [
    pytest.mark.xdist_group("elasticsearch"),
    pytest.mark.usefixtures("init_elasticsearch"),
]


class TestUpdateSettingsCommand:
    def test_calls_update_index_settings(
        self, cli, cliconfig, pyramid_request, update_index_settings
    ):
        result = cli.invoke(search.update_settings, [], obj=cliconfig)

        assert not result.exit_code
        update_index_settings.assert_called_once_with(pyramid_request.es)

    def test_handles_runtimeerror(self, cli, cliconfig, update_index_settings):
        update_index_settings.side_effect = RuntimeError("asplode!")

        result = cli.invoke(search.update_settings, [], obj=cliconfig)

        assert result.exit_code == 1
        assert "asplode!" in result.output

    @pytest.fixture
    def update_index_settings(self, patch):
        return patch("h.cli.commands.search.config.update_index_settings")


class TestReindexCommand:
    def test_reindexes_every_annotation(self, cli, cliconfig, pyramid_request, patch):
        batch_indexer = patch("h.cli.commands.search.BatchIndexer").return_value
        batch_indexer.index.return_value = set()

        result = cli.invoke(search.reindex, [], obj=cliconfig)

        assert result.exit_code == 0
        batch_indexer.index.assert_called_once_with(None)
        assert result.output == "reindexed every annotation\n"

    def test_reports_failed_annotation_ids(
        self, cli, cliconfig, pyramid_request, patch
    ):
        batch_indexer = patch("h.cli.commands.search.BatchIndexer").return_value
        batch_indexer.index.return_value = {"ann-2", "ann-1"}

        result = cli.invoke(search.reindex, [], obj=cliconfig)

        assert result.exit_code == 1
        assert "failed\tann-1" in result.output
        assert "failed\tann-2" in result.output
        assert "2 annotation(s) failed to reindex" in result.output


@pytest.fixture
def cliconfig(pyramid_request, mock_es_client):
    pyramid_request.es = mock_es_client
    return {"bootstrap": mock.Mock(return_value=pyramid_request)}
