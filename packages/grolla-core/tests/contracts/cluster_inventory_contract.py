"""Suíte de contrato de ClusterInventory.

O adapter sobrescreve:
- `cluster`: o adapter com o cluster respondendo, onde `service` tem ao menos um workload;
- `unavailable_cluster`: o mesmo adapter com o cluster falhando (credencial, permissão ou rede);
- `service` e `unknown_service`, se o cenário usar outros nomes. `unknown_service` não tem
  workload com o rótulo nem no mapa.
"""

from datetime import timedelta

import pytest
from grolla.application.investigation import (
    NO_WORKLOADS,
    ClusterInventory,
    ToolResult,
    ToolUnavailableError,
)
from grolla.testing.builders import T0

SINCE = T0 - timedelta(hours=1)


def assert_compact(result: ToolResult) -> None:
    assert isinstance(result, ToolResult)
    assert result.summary.strip()
    assert result.url is None or result.url.startswith("http")


class ClusterInventoryContract:
    @pytest.fixture
    def cluster(self) -> ClusterInventory:
        raise NotImplementedError

    @pytest.fixture
    def unavailable_cluster(self) -> ClusterInventory:
        raise NotImplementedError

    @pytest.fixture
    def service(self) -> str:
        return "pedidos-api"

    @pytest.fixture
    def unknown_service(self) -> str:
        return "servico-sem-workload"

    async def test_state_returns_a_compact_summary(
        self, cluster: ClusterInventory, service: str
    ) -> None:
        result = await cluster.state(service, SINCE)

        assert_compact(result)
        assert NO_WORKLOADS not in result.summary

    async def test_changes_returns_a_compact_summary(
        self, cluster: ClusterInventory, service: str
    ) -> None:
        assert_compact(await cluster.changes(service, SINCE))

    async def test_service_without_workloads_is_an_answer_not_an_error(
        self, cluster: ClusterInventory, unknown_service: str
    ) -> None:
        state = await cluster.state(unknown_service, SINCE)
        changes = await cluster.changes(unknown_service, SINCE)

        assert NO_WORKLOADS in state.summary
        assert_compact(changes)

    async def test_unavailable_cluster_raises_tool_unavailable(
        self, unavailable_cluster: ClusterInventory, service: str
    ) -> None:
        with pytest.raises(ToolUnavailableError):
            await unavailable_cluster.state(service, SINCE)
        with pytest.raises(ToolUnavailableError):
            await unavailable_cluster.changes(service, SINCE)
