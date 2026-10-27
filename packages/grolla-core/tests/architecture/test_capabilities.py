"""Capacidade de leitura conferida no código: o agente só lê o ambiente.

Três cercas, sem rede:
- as ferramentas que o `Toolbox` oferece ao modelo são uma lista fechada de leitura, mais
  `recall` e `remember` (que só escrevem na memória do próprio agente);
- o adapter da nuvem só chama operações `describe_*`, `list_*`, `get_*` e `lookup_*`;
- o adapter do cluster só faz requisição GET.

Ferramenta nova, operação nova da nuvem ou chamada nova ao cluster fazem este teste falhar: quem
adiciona precisa conferir que é leitura e atualizar a lista aqui, no mesmo PR.
"""

import ast
import inspect
import re
from datetime import timedelta
from pathlib import Path
from types import ModuleType

import grolla.adapters.aws as cloud
import grolla.adapters.kubernetes as cluster
from grolla.application.investigation import Toolbox
from grolla.domain.memory import Source, SourceKind
from grolla.testing.builders import T0
from grolla.testing.clock import FakeClock
from grolla.testing.investigation import (
    FakeChangeHistory,
    FakeCloudInventory,
    FakeClusterInventory,
    FakeTelemetry,
)
from grolla.testing.memory import FakeTextEmbedder, InMemoryMemoryStore
from kubernetes import client as k8s

# ---- Ferramentas do agente

READ_TOOLS = frozenset(
    {
        "query_metric",
        "search_logs",
        "find_traces",
        "recent_deploys",
        "read_file",
        "service_dependencies",
        "cloud_resources",
        "cloud_changes",
        "cloud_alarms",
        "cloud_health",
        "cloud_quotas",
        "cloud_overview",
        "cluster_state",
        "cluster_changes",
    }
)
MEMORY_TOOLS = frozenset({"recall", "remember"})  # escrevem só na memória do agente


def _everything_on(*, embedder: bool) -> Toolbox:
    """O toolbox com todos os ports ligados: oferece todas as ferramentas que existem."""
    return Toolbox(
        FakeTelemetry(),
        FakeChangeHistory(),
        FakeClock(T0),
        timedelta(hours=1),
        FakeCloudInventory(),
        memory=InMemoryMemoryStore(),
        remember_as=Source(SourceKind.CHAT, "Ana", "1"),
        embedder=FakeTextEmbedder() if embedder else None,
        cluster=FakeClusterInventory(),
    )


def test_the_agent_tools_are_the_closed_reading_list() -> None:
    for embedder in (False, True):
        names = [spec.name for spec in _everything_on(embedder=embedder).specs()]

        assert len(names) == len(set(names))
        assert set(names) == READ_TOOLS | MEMORY_TOOLS


# ---- Nuvem: só operações de leitura

READ_PREFIXES = ("describe_", "list_", "get_", "lookup_")
# Métodos de cliente boto3 que não têm prefixo de leitura e são inofensivos:
# - `assume_role` (STS): troca a credencial pela da role somente leitura da conta;
# - `generate_presigned_url`: assina localmente uma URL (o token do EKS, de
#   `get_caller_identity` com GET), sem chamar a API;
# - `get_paginator`: só monta o paginador de uma operação de leitura.
HARMLESS_CLIENT_METHODS = frozenset({"assume_role", "generate_presigned_url", "get_paginator"})
_CLIENT_FACTORIES = frozenset({"client", "_client"})  # `<sessão>.client(...)`, `self._client(...)`


def _tree(module: ModuleType) -> ast.Module:
    return ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))


def _functions(tree: ast.AST) -> list[ast.FunctionDef]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]


def _makes_client(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _CLIENT_FACTORIES
    )


def _client_methods(tree: ast.AST) -> set[tuple[str, int]]:
    """(método, linha) de cada chamada direta a um cliente boto3.

    Cliente é o que sai de `<sessão>.client(...)` ou `self._client(...)`: a própria expressão
    (`self._client(...).metodo(...)`) ou o nome a que ela foi atribuída na função
    (`sts = sessão.client("sts"); sts.metodo(...)`). Atributos do cliente (`sts.meta.events`)
    não são chamadas à API e ficam de fora.
    """
    found: set[tuple[str, int]] = set()
    for function in _functions(tree):
        clients: set[str] = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and _makes_client(node.value):
                clients |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and _makes_client(node.value)
                and isinstance(node.target, ast.Name)
            ):
                clients.add(node.target.id)
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            receiver = node.func.value
            if _makes_client(receiver) or (
                isinstance(receiver, ast.Name) and receiver.id in clients
            ):
                found.add((node.func.attr, node.lineno))
    return found


def _dynamic_dispatchers(tree: ast.AST) -> list[ast.FunctionDef]:
    """Funções com `getattr(<objeto criado>, operação)(...)`: a operação vem de uma variável."""
    return [
        function
        for function in _functions(tree)
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Call)
            and isinstance(node.func.func, ast.Name)
            and node.func.func.id == "getattr"
            and isinstance(node.func.args[0], ast.Call)
            for node in ast.walk(function)
        )
    ]


def _names_in(function: ast.FunctionDef) -> set[str]:
    return {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}


def _is_read(operation: str) -> bool:
    return operation.startswith(READ_PREFIXES)


def test_the_cloud_reading_list_has_only_reading_operations() -> None:
    for operations in (cloud.READ_OPERATIONS, cluster.CLOUD_OPERATIONS):
        writes = {
            (service, operation)
            for service, operation in operations
            if not _is_read(operation) and operation not in HARMLESS_CLIENT_METHODS
        }
        assert writes == set()


def test_every_cloud_call_site_names_an_operation_of_the_list() -> None:
    sites: list[tuple[str, str]] = []
    for node in ast.walk(_tree(cloud)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "call"
            and len(node.args) >= 3
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[2], ast.Constant)
        ):
            sites.append((str(node.args[0].value), str(node.args[2].value)))

    assert sites  # a varredura acha as chamadas: o teste não passa vazio
    assert {site for site in sites if site not in cloud.READ_OPERATIONS} == set()


def test_no_direct_client_call_outside_the_reading_prefixes() -> None:
    for module in (cloud, cluster):
        calls = _client_methods(_tree(module))

        assert calls  # a varredura acha os clientes: o teste não passa vazio
        outside = {
            (method, line)
            for method, line in calls
            if not _is_read(method) and method not in HARMLESS_CLIENT_METHODS
        }
        assert outside == set(), module.__name__


def test_the_only_dynamic_cloud_call_is_guarded_by_the_list() -> None:
    tree = _tree(cloud)

    [dispatcher] = _dynamic_dispatchers(tree)

    assert dispatcher.name == "call"
    assert "_allowed" in _names_in(dispatcher)


# ---- Cluster: só GET

_SERIALIZED_METHOD = re.compile(r"method='(\w+)'")
# O que o adapter pode usar do cliente oficial: a configuração, o cliente HTTP (só passado às
# APIs) e as classes de API da lista fechada.
_K8S_ALLOWED = frozenset({"Configuration", "ApiClient"})


def test_every_cluster_reading_is_an_http_get() -> None:
    for api, operation in cluster.READ_OPERATIONS:
        api_class = getattr(k8s, api)
        source = inspect.getsource(getattr(api_class, f"_{operation}_serialize"))

        assert _SERIALIZED_METHOD.findall(source) == ["GET"], f"{api}.{operation}"


def test_the_cluster_apis_are_only_reached_through_the_guarded_read() -> None:
    tree = _tree(cluster)
    apis = {api for api, _ in cluster.READ_OPERATIONS}
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "k8s"
    }

    assert used <= _K8S_ALLOWED | apis
    [dispatcher] = _dynamic_dispatchers(tree)
    assert dispatcher.name == "read"
    assert "READ_OPERATIONS" in _names_in(dispatcher)
    readers = {f.name for f in _functions(tree) if "_APIS" in _names_in(f)}
    assert readers == {"read"}
