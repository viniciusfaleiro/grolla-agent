"""Suíte de contrato de CloudInventory.

O adapter sobrescreve:
- `cloud`: o adapter com a nuvem respondendo, que conhece os recursos de `service`;
- `unavailable_cloud`: o mesmo adapter com a nuvem falhando (credencial, permissão ou rede);
- `service` e `unknown_service`, se o cenário usar outros nomes. `unknown_service` não tem
  recurso com a tag nem no mapa.
"""

from datetime import timedelta

import pytest
from grolla.application.investigation import (
    NO_CLOUD_RESOURCES,
    CloudInventory,
    ToolResult,
    ToolUnavailableError,
)
from grolla.testing.builders import T0

SINCE = T0 - timedelta(hours=1)


def assert_compact(result: ToolResult) -> None:
    assert isinstance(result, ToolResult)
    assert result.summary.strip()
    assert result.url is None or result.url.startswith("http")


class CloudInventoryContract:
    @pytest.fixture
    def cloud(self) -> CloudInventory:
        raise NotImplementedError

    @pytest.fixture
    def unavailable_cloud(self) -> CloudInventory:
        raise NotImplementedError

    @pytest.fixture
    def service(self) -> str:
        return "pedidos-api"

    @pytest.fixture
    def unknown_service(self) -> str:
        return "servico-sem-recursos"

    async def test_resources_returns_a_compact_summary(
        self, cloud: CloudInventory, service: str
    ) -> None:
        result = await cloud.resources(service)

        assert_compact(result)
        assert NO_CLOUD_RESOURCES not in result.summary

    async def test_changes_returns_a_compact_summary(
        self, cloud: CloudInventory, service: str
    ) -> None:
        assert_compact(await cloud.changes(service, SINCE))

    async def test_alarms_returns_a_compact_summary(
        self, cloud: CloudInventory, service: str
    ) -> None:
        assert_compact(await cloud.alarms(service))

    async def test_service_without_resources_is_an_answer_not_an_error(
        self, cloud: CloudInventory, unknown_service: str
    ) -> None:
        result = await cloud.resources(unknown_service)

        assert NO_CLOUD_RESOURCES in result.summary

    async def test_unavailable_cloud_raises_tool_unavailable(
        self, unavailable_cloud: CloudInventory, service: str
    ) -> None:
        with pytest.raises(ToolUnavailableError):
            await unavailable_cloud.resources(service)
        with pytest.raises(ToolUnavailableError):
            await unavailable_cloud.changes(service, SINCE)
        with pytest.raises(ToolUnavailableError):
            await unavailable_cloud.alarms(service)
