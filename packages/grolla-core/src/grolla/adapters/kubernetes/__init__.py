"""Clusters Kubernetes, só leitura: estado e mudanças do serviço em cada cluster ligado.

Contrato do cluster somente leitura: os corpos são do grolla-integracoes.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from grolla.application.investigation import ToolResult


@dataclass(frozen=True)
class ClusterTarget:
    name: str
    mode: Literal["kubeconfig", "in_cluster", "eks"]
    namespaces: tuple[str, ...]
    context: str | None = None  # kubeconfig
    eks_name: str | None = None  # eks
    region: str | None = None  # eks
    role_arn: str | None = None  # eks em outra conta
    service_labels: tuple[str, ...] = ("app.kubernetes.io/name", "app")
    workload_map: Mapping[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    show_values: bool = True  # False: só nomes de chaves e variáveis (clusters PCI)


class KubernetesClusterInventory:
    def __init__(
        self,
        clusters: Sequence[ClusterTarget],  # só os ligados; o bootstrap filtra `enabled`
        api_clients: Mapping[str, Any] | None = None,  # testes injetam um ApiClient por cluster
    ) -> None:
        self._clusters = tuple(clusters)
        self._api_clients = dict(api_clients or {})

    async def state(self, service: str, since: datetime) -> ToolResult:
        raise NotImplementedError

    async def changes(self, service: str, since: datetime) -> ToolResult:
        raise NotImplementedError
