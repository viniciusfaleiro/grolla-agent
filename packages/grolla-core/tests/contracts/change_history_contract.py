"""Suíte de contrato de ChangeHistory.

O adapter sobrescreve:
- `changes`: o adapter com a fonte respondendo, que conhece o repositório de `service`;
- `unavailable_changes`: o mesmo adapter com a fonte fora do ar ou estourando o timeout;
- `service` e `unmapped_service`, se o cenário usar outros nomes. `unmapped_service` não tem
  repositório no mapa nem com o mesmo nome nos projetos configurados.
"""

from datetime import timedelta

import pytest
from grolla.application.investigation import (
    BINARY_FILE,
    NO_REPOSITORY,
    READ_LIMIT,
    TRUNCATED,
    ChangeHistory,
    ToolUnavailableError,
)
from grolla.testing.builders import T0

SINCE = T0 - timedelta(hours=6)


class ChangeHistoryContract:
    @pytest.fixture
    def changes(self) -> ChangeHistory:
        raise NotImplementedError

    @pytest.fixture
    def unavailable_changes(self) -> ChangeHistory:
        raise NotImplementedError

    @pytest.fixture
    def service(self) -> str:
        return "authorizer-api"

    @pytest.fixture
    def unmapped_service(self) -> str:
        return "servico-sem-repositorio"

    async def test_recent_deploys_returns_a_compact_summary(
        self, changes: ChangeHistory, service: str
    ) -> None:
        result = await changes.recent_deploys(service, SINCE)

        assert result.summary.strip()
        assert result.summary != NO_REPOSITORY
        assert result.url is None or result.url.startswith("http")

    async def test_service_without_repository_is_an_answer_not_an_error(
        self, changes: ChangeHistory, unmapped_service: str
    ) -> None:
        result = await changes.recent_deploys(unmapped_service, SINCE)

        assert NO_REPOSITORY in result.summary

    async def test_unavailable_source_raises_tool_unavailable(
        self, unavailable_changes: ChangeHistory, service: str
    ) -> None:
        with pytest.raises(ToolUnavailableError):
            await unavailable_changes.recent_deploys(service, SINCE)


class RepositoryFilesContract:
    """`read_file`: leitura do repositório do serviço no branch padrão.

    Fica fora de `ChangeHistoryContract` para o adapter ganhar a suíte quando implementar. O
    adapter sobrescreve `changes` (o repositório de `service` tem `README.md`, um arquivo
    `large_path` maior que READ_LIMIT e um binário `binary_path`) e `unavailable_changes`.
    """

    @pytest.fixture
    def changes(self) -> ChangeHistory:
        raise NotImplementedError

    @pytest.fixture
    def unavailable_changes(self) -> ChangeHistory:
        raise NotImplementedError

    @pytest.fixture
    def service(self) -> str:
        return "authorizer-api"

    @pytest.fixture
    def unmapped_service(self) -> str:
        return "servico-sem-repositorio"

    @pytest.fixture
    def large_path(self) -> str:
        return "docs/grande.md"

    @pytest.fixture
    def binary_path(self) -> str:
        return "logo.png"

    async def test_read_file_returns_the_content_and_a_link(
        self, changes: ChangeHistory, service: str
    ) -> None:
        result = await changes.read_file(service, "README.md")

        assert result.summary.strip()
        assert NO_REPOSITORY not in result.summary
        assert TRUNCATED not in result.summary
        assert result.url is not None
        assert result.url.startswith("http")

    async def test_empty_path_lists_the_root(self, changes: ChangeHistory, service: str) -> None:
        result = await changes.read_file(service, "")

        assert "README.md" in result.summary

    async def test_large_file_is_cut_and_says_so(
        self, changes: ChangeHistory, service: str, large_path: str
    ) -> None:
        result = await changes.read_file(service, large_path)

        assert TRUNCATED in result.summary
        assert len(result.summary) <= READ_LIMIT + 200

    async def test_binary_file_is_refused_without_error(
        self, changes: ChangeHistory, service: str, binary_path: str
    ) -> None:
        result = await changes.read_file(service, binary_path)

        assert BINARY_FILE in result.summary

    async def test_service_without_repository_is_an_answer_not_an_error(
        self, changes: ChangeHistory, unmapped_service: str
    ) -> None:
        result = await changes.read_file(unmapped_service, "README.md")

        assert NO_REPOSITORY in result.summary

    async def test_unavailable_source_raises_tool_unavailable(
        self, unavailable_changes: ChangeHistory, service: str
    ) -> None:
        with pytest.raises(ToolUnavailableError):
            await unavailable_changes.read_file(service, "README.md")
