"""AWS: recursos do serviço, mudanças no CloudTrail e alarmes do CloudWatch, só leitura.

O boto3 é síncrono: cada consulta roda em `asyncio.to_thread`. Só chamadas de leitura de uma
lista fechada; as credenciais vêm da cadeia padrão do SDK (ambiente ou role da máquina).
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from grolla.application.investigation import ToolResult


class AwsCloudInventory:
    def __init__(
        self,
        regions: Sequence[str],
        resource_map: Mapping[str, Sequence[str]],
        session: Any = None,  # boto3.Session; testes injetam uma sessão com Stubber
    ) -> None:
        self._regions = tuple(regions)
        self._resource_map = {k: tuple(v) for k, v in resource_map.items()}
        self._session = session

    async def resources(self, service: str) -> ToolResult:
        raise NotImplementedError

    async def changes(self, service: str, since: datetime) -> ToolResult:
        raise NotImplementedError

    async def alarms(self, service: str) -> ToolResult:
        raise NotImplementedError
