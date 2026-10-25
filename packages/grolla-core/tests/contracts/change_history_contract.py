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
    NO_REPOSITORY,
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
