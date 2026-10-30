"""Clusters Kubernetes, só leitura: estado e mudanças do serviço em cada cluster ligado.

O cliente oficial é síncrono: cada cluster roda numa thread (`asyncio.to_thread`) e os clusters
são consultados em paralelo. Só leituras (`list`, `read`) de uma lista fechada de recursos; as
respostas são lidas como JSON cru (`_preload_content=False`), sem os modelos do cliente. Secrets
nunca são lidos: uma variável que vem de Secret aparece só pelo nome da referência.

Workloads do serviço: Deployments, StatefulSets e DaemonSets dos `namespaces` com o primeiro
rótulo de `service_labels` que encontrar algum (`<rótulo>=<serviço>`), mais os do `workload_map`
("namespace/nome"). Os pods são os do seletor do workload.

Estado: réplicas prontas e desejadas, rollout em andamento, até 10 pods (os não prontos primeiro)
com fase, reinícios, último término, espera, probes falhando (eventos `Unhealthy`) e limites; o
HPA do workload ("no máximo (10 de 10)"); e os eventos de aviso desde `since` ligados ao
workload, aos ReplicaSets, aos pods ou ao HPA.

Mudanças desde `since`, do mais recente ao mais antigo (HH:MM UTC), com o gerenciador do
`managedFields` (a ferramenta: `kubectl-edit`, `kubectl-set`, `helm`, ...): as revisões do
Deployment (ReplicaSets com `deployment.kubernetes.io/revision`) com o diff do template para a
revisão anterior (imagem, variáveis, recursos); o template de StatefulSet e DaemonSet; as
réplicas; as chaves alteradas dos ConfigMaps que o template usa (com o valor atual); o HPA; e as
NetworkPolicies do namespace.

Com mais de um cluster, cada item diz "no cluster <nome>"; a falha de um vira a linha
"cluster <nome>: não foi possível consultar (<motivo>)" no fim, e só com todos falhando a
consulta levanta `ToolUnavailableError`. Num cluster com `show_values=False` nenhum valor de
ConfigMap nem de variável sai daqui: só os nomes, com "(valor oculto)".

Acesso: `kubeconfig` (o `context` ou o atual, do `KUBECONFIG`), `in_cluster` (a conta de serviço
do pod) ou `eks` (endpoint e CA do `eks:DescribeCluster`; token `k8s-aws-v1.` da URL pré-assinada
do `sts:GetCallerIdentity`, renovado antes de 14 min; com `role_arn`, a role é assumida antes).
"""

import asyncio
import base64
import json
import os
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

# O boto3 não traz stubs de tipo; só o acesso `eks` usa (credencial da nuvem do ambiente).
import boto3  # pyright: ignore[reportMissingTypeStubs] — sem stubs (ver acima)
from botocore.config import Config  # pyright: ignore[reportMissingTypeStubs] — sem stubs
from botocore.exceptions import (  # pyright: ignore[reportMissingTypeStubs] — sem stubs
    BotoCoreError,
    ClientError,
)
from kubernetes import client as k8s
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from urllib3.exceptions import HTTPError

from grolla.application.investigation import (
    NO_CLUSTER_CHANGES,
    NO_WORKLOADS,
    UNAVAILABLE,
    ToolResult,
    ToolUnavailableError,
)

# Lista fechada: só estas leituras saem daqui, mesmo que a credencial permita mais.
READ_OPERATIONS = frozenset(
    {
        ("AppsV1Api", "list_namespaced_deployment"),
        ("AppsV1Api", "list_namespaced_stateful_set"),
        ("AppsV1Api", "list_namespaced_daemon_set"),
        ("AppsV1Api", "list_namespaced_replica_set"),
        ("AppsV1Api", "read_namespaced_deployment"),
        ("AppsV1Api", "read_namespaced_stateful_set"),
        ("AppsV1Api", "read_namespaced_daemon_set"),
        ("CoreV1Api", "list_namespaced_pod"),
        ("CoreV1Api", "list_namespaced_event"),
        ("CoreV1Api", "read_namespaced_config_map"),
        ("AutoscalingV2Api", "list_namespaced_horizontal_pod_autoscaler"),
        ("NetworkingV1Api", "list_namespaced_network_policy"),
    }
)
# Na nuvem, só no acesso `eks`: o endpoint e o CA do cluster e a credencial da role. O token é
# uma URL pré-assinada do `sts:GetCallerIdentity`, gerada sem chamar a API.
CLOUD_OPERATIONS = frozenset({("eks", "describe_cluster"), ("sts", "assume_role")})

_APIS: Mapping[str, Callable[[Any], Any]] = {
    "AppsV1Api": k8s.AppsV1Api,
    "CoreV1Api": k8s.CoreV1Api,
    "AutoscalingV2Api": k8s.AutoscalingV2Api,
    "NetworkingV1Api": k8s.NetworkingV1Api,
}
# Tipo do workload -> (listagem, leitura por nome).
_KINDS = {
    "Deployment": ("list_namespaced_deployment", "read_namespaced_deployment"),
    "StatefulSet": ("list_namespaced_stateful_set", "read_namespaced_stateful_set"),
    "DaemonSet": ("list_namespaced_daemon_set", "read_namespaced_daemon_set"),
}
REVISION = "deployment.kubernetes.io/revision"
RESTARTED_AT = "kubectl.kubernetes.io/restartedAt"
TIMEOUT = (5, 10)  # conexão e leitura de cada requisição, em segundos
MAX_PODS = 10  # pods mostrados por workload, os não prontos primeiro
MAX_EVENTS = 10  # eventos de aviso por workload
MAX_CHANGES = 20
TEXT_CHARS = 200  # mensagem de evento
VALUE_CHARS = 80  # valor de chave de ConfigMap ou de variável
HIDDEN = "valor oculto"
NO_FIELD_RECORD = "sem registro de campo"  # managedFields desligado ou limpo
SESSION_NAME = "grolla-somente-leitura"
EKS_HEADER = "x-k8s-aws-id"
EKS_PREFIX = "k8s-aws-v1."
EKS_URL_SECONDS = 60
EKS_RENEW = timedelta(minutes=14)  # o token vale 15 min
_CONFIG = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})

_Json = Any  # JSON cru da API: cada leitura é conferida pelos testes, não pelo tipo
_NONE: _Json = {}  # objeto ausente no JSON (nunca alterado)
_NO_ITEMS: _Json = ()  # lista ausente no JSON
# O carregador de configuração do cliente não tem tipos completos.
_LOADER: Any = k8s_config


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
        api_clients: _Json = None,  # testes injetam um ApiClient por cluster
    ) -> None:
        injected = dict(api_clients or _NONE)
        self._clusters = [_Cluster(target, injected.get(target.name)) for target in clusters]

    async def state(self, service: str, since: datetime) -> ToolResult:
        answers, failures = await self._across(lambda cluster: cluster.state(service, since))
        blocks = [block for blocks, _ in answers for block in blocks]
        notes = [note for _, found in answers for note in found] + failures
        if not blocks:
            return _noted(NO_WORKLOADS, notes)
        summary = f"{len(blocks)} workload(s) de {service} no cluster:\n" + "\n".join(blocks)
        return _noted(summary, notes)

    async def changes(self, service: str, since: datetime) -> ToolResult:
        answers, failures = await self._across(lambda cluster: cluster.changes(service, since))
        notes = [note for answer in answers for note in answer.notes] + failures
        if not any(answer.found for answer in answers):
            return _noted(NO_WORKLOADS, notes)
        found = {line: moment for answer in answers for moment, line in answer.changes}
        if not found:
            return _noted(NO_CLUSTER_CHANGES, notes)
        recent = sorted(found, key=lambda line: found[line], reverse=True)[:MAX_CHANGES]
        summary = (
            f"{len(recent)} mudança(s) no cluster para {service} desde {_hhmm(since)} UTC, "
            "da mais recente:\n" + "\n".join(recent)
        )
        return _noted(summary, notes)

    async def _across[T](self, query: Callable[["_Cluster"], T]) -> tuple[list[T], list[str]]:
        """Consulta todos os clusters em paralelo; devolve as respostas e uma linha por falha."""
        results = await asyncio.gather(
            *(asyncio.to_thread(cluster.attempt, query) for cluster in self._clusters)
        )
        answers = [result for result in results if not isinstance(result, _Failure)]
        failures = [result for result in results if isinstance(result, _Failure)]
        lines = [
            f"cluster {failure.cluster}: {UNAVAILABLE} ({failure.reason})" for failure in failures
        ]
        if not answers:
            raise ToolUnavailableError(
                failures[0].reason if len(failures) == 1 else "; ".join(lines)
            )
        return answers, lines


@dataclass(frozen=True)
class _Failure:
    cluster: str
    reason: str


@dataclass(frozen=True)
class _Changes:
    found: bool  # o serviço tem workload neste cluster
    changes: list[tuple[datetime, str]]
    notes: list[str]


@dataclass(frozen=True)
class _Workload:
    kind: str  # Deployment | StatefulSet | DaemonSet
    body: _Json

    @property
    def name(self) -> str:
        return str(self.body["metadata"]["name"])

    @property
    def namespace(self) -> str:
        return str(self.body["metadata"]["namespace"])

    @property
    def selector(self) -> str:
        labels: _Json = _at(self.body, "spec", "selector", "matchLabels") or _NONE
        return ",".join(f"{key}={value}" for key, value in sorted(labels.items()))

    @property
    def template(self) -> _Json:
        return _at(self.body, "spec", "template") or _NONE


class _Cluster:
    """Um cluster: o acesso dele, as leituras e o que ele responde sobre o serviço."""

    def __init__(self, target: ClusterTarget, api_client: Any) -> None:
        self.target = target
        self._client: Any = api_client
        self._lock = threading.Lock()
        self._on = f" no cluster {target.name}"  # sempre: o comando sugerido precisa do lugar

    def attempt[T](self, query: Callable[["_Cluster"], T]) -> T | _Failure:
        try:
            return query(self)
        except _CLUSTER_ERRORS as error:
            return _Failure(self.target.name, _reason(error))

    def read(self, api: str, operation: str, **arguments: Any) -> _Json:
        """Uma leitura da lista fechada, como JSON cru (fora de READ_OPERATIONS: recusa)."""
        if (api, operation) not in READ_OPERATIONS:
            raise ToolUnavailableError(f"leitura fora da lista: {api}.{operation}")
        response: Any = getattr(_APIS[api](self._api()), operation)(
            **arguments, _preload_content=False, _request_timeout=TIMEOUT
        )
        answer: _Json = json.loads(response.data)
        return answer

    def items(self, api: str, operation: str, **arguments: Any) -> list[_Json]:
        found: list[_Json] = self.read(api, operation, **arguments).get("items") or []
        return found

    def _api(self) -> Any:
        with self._lock:
            if self._client is None:
                self._client = _connect(self.target)
            return self._client

    # ---- Workloads do serviço

    def workloads(self, service: str) -> list[_Workload]:
        found: dict[tuple[str, str, str], _Workload] = {}
        for label in self.target.service_labels:
            for namespace in self.target.namespaces:
                for kind, (listing, _) in _KINDS.items():
                    for body in self.items(
                        "AppsV1Api",
                        listing,
                        namespace=namespace,
                        label_selector=f"{label}={service}",
                    ):
                        workload = _Workload(kind, body)
                        found[(kind, workload.namespace, workload.name)] = workload
            if found:
                break
        for item in self.target.workload_map.get(service, ()):
            namespace, _, name = item.partition("/")
            workload = self._read_workload(namespace, name)
            if workload is not None:
                found[(workload.kind, namespace, name)] = workload
        return list(found.values())

    def _read_workload(self, namespace: str, name: str) -> _Workload | None:
        for kind, (_, reading) in _KINDS.items():
            try:
                return _Workload(
                    kind, self.read("AppsV1Api", reading, name=name, namespace=namespace)
                )
            except ApiException as error:
                if _status(error) != 404:  # noqa: PLR2004 — Not Found: tenta o próximo tipo
                    raise
        return None

    def _replica_sets(self, workload: _Workload) -> list[_Json]:
        if workload.kind != "Deployment":
            return []
        uid = workload.body["metadata"].get("uid")
        return [
            item
            for item in self.items(
                "AppsV1Api",
                "list_namespaced_replica_set",
                namespace=workload.namespace,
                label_selector=workload.selector,
            )
            if any(owner.get("uid") == uid for owner in item["metadata"].get("ownerReferences", []))
        ]

    def _optional[A](
        self, label: str, notes: list[str], query: Callable[[A], list[_Json]], argument: A
    ) -> list[_Json]:
        """Leitura que pode faltar sem derrubar o cluster (permissão negada para eventos, ...)."""
        try:
            return query(argument)
        except _CLUSTER_ERRORS as error:
            note = f"{label}{self._on}: {UNAVAILABLE} ({_reason(error)})"
            if note not in notes:
                notes.append(note)
            return []

    # ---- Estado

    def state(self, service: str, since: datetime) -> tuple[list[str], list[str]]:
        notes: list[str] = []
        blocks: list[str] = []
        hpas: dict[str, list[_Json]] = {}
        events: dict[str, list[_Json]] = {}
        for workload in self.workloads(service):
            namespace = workload.namespace
            if namespace not in hpas:
                hpas[namespace] = self._optional("HPA", notes, self._hpas, namespace)
                events[namespace] = self._optional("eventos", notes, self._warnings, namespace)
            blocks.append(
                self._workload_state(workload, since, hpas[namespace], events[namespace], notes)
            )
        return blocks, notes

    def _hpas(self, namespace: str) -> list[_Json]:
        return self.items(
            "AutoscalingV2Api", "list_namespaced_horizontal_pod_autoscaler", namespace=namespace
        )

    def _warnings(self, namespace: str) -> list[_Json]:
        return self.items(
            "CoreV1Api", "list_namespaced_event", namespace=namespace, field_selector="type=Warning"
        )

    def _workload_state(
        self,
        workload: _Workload,
        since: datetime,
        hpas: list[_Json],
        warnings: list[_Json],
        notes: list[str],
    ) -> str:
        pods = self.items(
            "CoreV1Api",
            "list_namespaced_pod",
            namespace=workload.namespace,
            label_selector=workload.selector,
        )
        replica_sets = self._optional("ReplicaSets", notes, self._replica_sets, workload)
        hpa = _hpa_of(workload, hpas)
        names = {workload.name, *(_name(item) for item in pods + replica_sets)}
        names |= {_name(hpa)} if hpa else set()
        related = sorted(
            (
                (moment, event)
                for event in warnings
                if (event.get("involvedObject") or _NONE).get("name") in names
                and (moment := _event_time(event)) is not None
                and moment >= since
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        probes = _failing_probes(event for _, event in related)
        where = f"{workload.kind} {workload.name} em {workload.namespace}{self._on}"
        parts = [f"{where}: {_replicas(workload)}"]
        ordered = sorted(pods, key=lambda pod: (_ready(pod), _name(pod)))
        shown = ordered[:MAX_PODS]
        more = f" (mostrando {MAX_PODS} de {len(pods)})" if len(pods) > MAX_PODS else ""
        if shown:
            parts.append(
                f"pods{more}: "
                + "; ".join(_pod_line(pod, probes.get(_name(pod), ())) for pod in shown)
            )
        else:
            parts.append("nenhum pod")
        if hpa:
            parts.append(_hpa_line(hpa))
        if related:
            lines = [_event_line(moment, event) for moment, event in related[:MAX_EVENTS]]
            parts.append(f"eventos de aviso desde {_hhmm(since)} UTC: " + "; ".join(lines))
        return ". ".join(parts)

    # ---- Mudanças

    def changes(self, service: str, since: datetime) -> _Changes:
        notes: list[str] = []
        found: list[tuple[datetime, str]] = []
        workloads = self.workloads(service)
        hpas: dict[str, list[_Json]] = {}
        for workload in workloads:
            namespace = workload.namespace
            if namespace not in hpas:
                hpas[namespace] = self._optional("HPA", notes, self._hpas, namespace)
                found += self._policy_changes(namespace, since, notes)
            found += self._workload_changes(workload, since, notes)
            found += self._config_changes(workload, since, notes)
            hpa = _hpa_of(workload, hpas[namespace])
            if hpa:
                found += self._hpa_changes(hpa, since)
        return _Changes(bool(workloads), found, notes)

    def _workload_changes(
        self, workload: _Workload, since: datetime, notes: list[str]
    ) -> list[tuple[datetime, str]]:
        found: list[tuple[datetime, str]] = []
        entries = _entries(workload.body)
        what = f"{workload.kind} {workload.name}"
        for entry in entries:
            moment = _time(entry.get("time"))
            if moment is None or moment < since:
                continue
            spec: _Json = (entry.get("fieldsV1") or _NONE).get("f:spec") or _NONE
            scaled = entry.get("subresource") == "scale" or "f:template" not in spec
            who = f"{_manager(entry)}{self._on}"
            if "f:replicas" in spec and scaled:
                replicas = _at(workload.body, "spec", "replicas")
                found.append(
                    (moment, f"{_hhmm(moment)} {what} réplicas por {who}: agora {replicas}")
                )
            if "f:template" in spec and workload.kind != "Deployment":
                found.append(
                    (moment, f"{_hhmm(moment)} {what} alterado por {who}: template do pod")
                )
        if workload.kind == "Deployment":
            replica_sets = self._optional("ReplicaSets", notes, self._replica_sets, workload)
            found += self._revisions(workload, replica_sets, entries, since)
        return found

    def _revisions(
        self, workload: _Workload, replica_sets: list[_Json], entries: list[_Json], since: datetime
    ) -> list[tuple[datetime, str]]:
        revisions = sorted(
            (
                (int(revision), item)
                for item in replica_sets
                if (
                    revision := (item["metadata"].get("annotations") or _NONE).get(REVISION, "")
                ).isdigit()
            ),
            key=lambda pair: pair[0],
        )
        template_entries = [
            entry
            for entry in entries
            if "f:template" in ((entry.get("fieldsV1") or _NONE).get("f:spec") or _NONE)
            and _time(entry.get("time")) is not None
        ]
        found: list[tuple[datetime, str]] = []
        previous: _Json | None = None
        for revision, item in revisions:
            created = _time(item["metadata"].get("creationTimestamp"))
            template: _Json = _at(item, "spec", "template") or _NONE
            if created is not None and created >= since:
                manager = _nearest_manager(template_entries, created)
                diff = (
                    _template_diff(previous, template, show_values=self.target.show_values)
                    if previous is not None
                    else ["primeira revisão"]
                )
                line = (
                    f"{_hhmm(created)} Deployment {workload.name} revisão {revision} por "
                    f"{manager}{self._on}: " + ", ".join(diff)
                )
                found.append((created, line))
            previous = template
        return found

    def _config_changes(
        self, workload: _Workload, since: datetime, notes: list[str]
    ) -> list[tuple[datetime, str]]:
        found: list[tuple[datetime, str]] = []
        for name in _config_maps(workload.template):
            config_map = self._config_map(workload.namespace, name, notes)
            if config_map is None:
                continue
            data: _Json = config_map.get("data") or _NONE
            for entry in _entries(config_map):
                moment = _time(entry.get("time"))
                keys = [
                    key[2:]
                    for key in (entry.get("fieldsV1") or _NONE).get("f:data") or _NONE
                    if key.startswith("f:")
                ]
                if moment is None or moment < since or not keys:
                    continue
                values = ", ".join(
                    _value(key, data.get(key), show_values=self.target.show_values) for key in keys
                )
                line = (
                    f"{_hhmm(moment)} ConfigMap {name} alterado por {_manager(entry)}{self._on}: "
                    + values
                )
                found.append((moment, line))
        return found

    def _config_map(self, namespace: str, name: str, notes: list[str]) -> _Json | None:
        try:
            return self.read(
                "CoreV1Api", "read_namespaced_config_map", name=name, namespace=namespace
            )
        except ApiException as error:
            if _status(error) == 404:  # noqa: PLR2004 — Not Found: o template cita um que não existe
                return None
            notes.append(f"ConfigMap {name}{self._on}: {UNAVAILABLE} ({_reason(error)})")
            return None

    def _hpa_changes(self, hpa: _Json, since: datetime) -> list[tuple[datetime, str]]:
        spec: _Json = hpa.get("spec") or _NONE
        limits = f"mínimo {spec.get('minReplicas', 1)}, máximo {spec.get('maxReplicas')}"
        what = f"HPA {_name(hpa)} alterado por"
        return [
            (moment, f"{_hhmm(moment)} {what} {_manager(entry)}{self._on}: {limits}")
            for entry in _entries(hpa)
            if (moment := _time(entry.get("time"))) is not None
            and moment >= since
            and "f:spec" in (entry.get("fieldsV1") or _NONE)
        ]

    def _policies(self, namespace: str) -> list[_Json]:
        return self.items("NetworkingV1Api", "list_namespaced_network_policy", namespace=namespace)

    def _policy_changes(
        self, namespace: str, since: datetime, notes: list[str]
    ) -> list[tuple[datetime, str]]:
        policies = self._optional("NetworkPolicies", notes, self._policies, namespace)
        return [
            (
                moment,
                f"{_hhmm(moment)} NetworkPolicy {_name(policy)} em {namespace} alterada por "
                f"{_manager(entry)}{self._on}",
            )
            for policy in policies
            for entry in _entries(policy)
            if (moment := _time(entry.get("time"))) is not None
            and moment >= since
            and "f:spec" in (entry.get("fieldsV1") or _NONE)
        ]


# ---- Acesso


def _connect(target: ClusterTarget) -> Any:
    if target.mode == "eks":
        return connect_eks(target, boto3.Session())
    if target.mode == "in_cluster":
        configuration = k8s.Configuration()
        _LOADER.load_incluster_config(client_configuration=configuration)
        return k8s.ApiClient(configuration)
    # O KUBECONFIG é lido agora (o padrão do cliente é fixado quando ele é importado).
    return _LOADER.new_client_from_config(
        config_file=os.environ.get("KUBECONFIG"), context=target.context
    )


def connect_eks(target: ClusterTarget, session: Any) -> Any:
    """ApiClient do EKS: endpoint e CA pelo DescribeCluster, token renovado antes de expirar."""
    region, name = target.region or "", target.eks_name or ""
    token = _EksToken(session, region, name, target.role_arn)
    _cloud_allowed("eks", "describe_cluster")
    eks = session.client("eks", region_name=region, config=_CONFIG, **token.credentials())
    cluster: Any = eks.describe_cluster(name=name)["cluster"]
    with tempfile.NamedTemporaryFile("wb", prefix="grolla-eks-", suffix=".crt", delete=False) as ca:
        ca.write(base64.b64decode(cluster["certificateAuthority"]["data"]))
    configuration: Any = k8s.Configuration(host=cluster["endpoint"])
    configuration.ssl_ca_cert = ca.name
    configuration.api_key_prefix = {"authorization": "Bearer"}
    configuration.api_key = {"authorization": token.value()}
    configuration.refresh_api_key_hook = token.refresh
    return k8s.ApiClient(configuration)


class _EksToken:
    """Token do EKS: URL pré-assinada do GetCallerIdentity com o nome do cluster no header."""

    def __init__(self, session: Any, region: str, name: str, role_arn: str | None) -> None:
        self._session = session
        self._region = region
        self._name = name
        self._role_arn = role_arn
        self._token = ""
        self._made: datetime | None = None
        self._lock = threading.Lock()

    def credentials(self) -> dict[str, str]:
        """A credencial da role do cluster, assumida agora; vazio sem `role_arn`."""
        if not self._role_arn:
            return {}
        _cloud_allowed("sts", "assume_role")
        sts = self._session.client("sts", region_name=self._region, config=_CONFIG)
        answer: Any = sts.assume_role(RoleArn=self._role_arn, RoleSessionName=SESSION_NAME)
        credentials = answer["Credentials"]
        return {
            "aws_access_key_id": credentials["AccessKeyId"],
            "aws_secret_access_key": credentials["SecretAccessKey"],
            "aws_session_token": credentials["SessionToken"],
        }

    def value(self) -> str:
        with self._lock:
            now = datetime.now(UTC)
            if self._made is None or now - self._made >= EKS_RENEW:
                self._token = self._sign()
                self._made = now
            return self._token

    def refresh(self, configuration: Any) -> None:
        """Gancho do cliente antes de cada requisição: troca o token perto de expirar."""
        configuration.api_key["authorization"] = self.value()

    def _sign(self) -> str:
        sts: Any = self._session.client(
            "sts",
            region_name=self._region,
            endpoint_url=f"https://sts.{self._region}.amazonaws.com",
            config=_CONFIG,
            **self.credentials(),
        )

        def header(request: Any, **_: Any) -> None:
            request.headers[EKS_HEADER] = self._name

        sts.meta.events.register("before-sign.sts.GetCallerIdentity", header)
        url: str = sts.generate_presigned_url(
            "get_caller_identity", Params={}, ExpiresIn=EKS_URL_SECONDS, HttpMethod="GET"
        )
        return EKS_PREFIX + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def _cloud_allowed(service: str, operation: str) -> None:
    if (service, operation) not in CLOUD_OPERATIONS:  # pragma: no cover — guarda da lista
        raise ToolUnavailableError(f"operação fora da lista de leitura: {service}.{operation}")


_CLUSTER_ERRORS = (
    ApiException,
    HTTPError,
    OSError,
    ConfigException,
    ClientError,
    BotoCoreError,
    ToolUnavailableError,
)


def _reason(error: Exception) -> str:
    """Motivo curto da falha, sem o corpo da resposta."""
    if isinstance(error, ApiException):
        reason: Any = getattr(error, "reason", "")
        refused = f"o cluster recusou: {_status(error)} {reason}"
        return refused if _status(error) else "cluster indisponível: erro de TLS"
    if isinstance(error, ConfigException):
        return f"acesso mal configurado: {_short(str(error), VALUE_CHARS)}"
    if isinstance(error, ClientError):
        details: Any = getattr(error, "response", {})
        return f"a nuvem recusou: {details.get('Error', {}).get('Code', 'erro')}"
    if isinstance(error, BotoCoreError):
        return f"nuvem indisponível: {type(error).__name__}"
    if isinstance(error, ToolUnavailableError):
        return str(error)
    return f"cluster indisponível: {type(error).__name__}"  # rede, timeout, DNS


def _status(error: ApiException) -> int:
    """O status HTTP da recusa (0: sem resposta, como erro de TLS)."""
    status: Any = getattr(error, "status", 0)
    return int(status or 0)


# ---- Leitura dos objetos (JSON da API)


def _at(body: _Json, *path: str) -> Any:
    current: Any = body
    for key in path:
        current = cast(_Json, current).get(key) if isinstance(current, dict) else None
    return current


def _name(body: _Json) -> str:
    return str(_at(body, "metadata", "name"))


def _entries(body: _Json) -> list[_Json]:
    """As entradas do `managedFields`, sem as do status (o controlador, não uma mudança)."""
    found: list[_Json] = _at(body, "metadata", "managedFields") or []
    return [entry for entry in found if entry.get("subresource") != "status"]


def _manager(entry: _Json) -> str:
    return str(entry.get("manager") or NO_FIELD_RECORD)


def _nearest_manager(entries: list[_Json], moment: datetime) -> str:
    """O gerenciador que mexeu no template mais perto do horário da revisão."""
    timed = [(entry, _time(entry.get("time")) or moment) for entry in entries]
    if not timed:
        return NO_FIELD_RECORD
    entry, _ = min(timed, key=lambda pair: abs((pair[1] - moment).total_seconds()))
    return _manager(entry)


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


def _event_time(event: _Json) -> datetime | None:
    return (
        _time(event.get("lastTimestamp"))
        or _time(event.get("eventTime"))
        or _time(_at(event, "metadata", "creationTimestamp"))
    )


def _replicas(workload: _Workload) -> str:
    status: _Json = workload.body.get("status") or _NONE
    spec: _Json = workload.body.get("spec") or _NONE
    if workload.kind == "DaemonSet":
        desired = status.get("desiredNumberScheduled", 0)
        ready = status.get("numberReady", 0)
        rolling = status.get("updatedNumberScheduled", desired) < desired
    else:
        desired = spec.get("replicas", 1)
        ready = status.get("readyReplicas", 0)
        rolling = (
            status.get("updateRevision", "") != status.get("currentRevision", "")
            if workload.kind == "StatefulSet"
            # Deployment: pods da revisão nova faltando ou pods da antiga ainda de pé
            else status.get("updatedReplicas", 0) < desired or status.get("replicas", 0) > desired
        )
    generation = _at(workload.body, "metadata", "generation") or 0
    rolling = rolling or (status.get("observedGeneration") or 0) < generation
    text = f"{ready} de {desired} réplicas prontas"
    applied = _applied_replicas(workload.body)
    if applied is not None and applied != desired and workload.kind != "DaemonSet":
        text += f" ({applied} no último apply)"  # o kubectl scale não deixa o valor anterior
    return text + (", rollout em andamento" if rolling else "")


def _applied_replicas(body: _Json) -> int | None:
    """As réplicas do manifesto do último `apply`, quando a anotação existe e traz o campo."""
    raw = _at(body, "metadata", "annotations", "kubectl.kubernetes.io/last-applied-configuration")
    try:
        replicas = _at(json.loads(raw), "spec", "replicas") if isinstance(raw, str) else None
    except ValueError:
        return None
    return replicas if isinstance(replicas, int) else None


def _ready(pod: _Json) -> bool:
    conditions: list[_Json] = _at(pod, "status", "conditions") or []
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)


def _pod_line(pod: _Json, probes: Iterable[str]) -> str:
    statuses: list[_Json] = _at(pod, "status", "containerStatuses") or []
    containers: list[_Json] = _at(pod, "spec", "containers") or []
    many = len(containers) > 1
    restarts = sum(int(status.get("restartCount", 0)) for status in statuses)
    parts = [
        str(_at(pod, "status", "phase") or "Unknown"),
        "pronto" if _ready(pod) else "não pronto",
    ]
    if restarts:
        parts.append(f"{restarts} reinício(s)")
    for status in statuses:
        prefix = f"{status.get('name')}: " if many else ""
        ended = _at(status, "lastState", "terminated") or _at(status, "state", "terminated")
        if ended:
            finished = _time(ended.get("finishedAt"))
            when = f" às {_hhmm(finished)}" if finished else ""
            parts.append(
                f"{prefix}último término {ended.get('reason', 'sem motivo')} "
                f"(exit {ended.get('exitCode')}){when}"
            )
        waiting = _at(status, "state", "waiting")
        if waiting:
            parts.append(f"{prefix}esperando {waiting.get('reason', 'sem motivo')}")
    failing = sorted(set(probes))
    if failing:
        parts.append("probe falhando: " + ", ".join(failing))
    for container in containers:
        prefix = f"{container.get('name')}: " if many else ""
        limits: _Json = _at(container, "resources", "limits") or _NONE
        if limits:
            shown = ", ".join(
                f"{_RESOURCES.get(key, key)} {value}" for key, value in sorted(limits.items())
            )
            parts.append(f"{prefix}limites {shown}")
        else:
            parts.append(f"{prefix}sem limites")
    return f"pod {_name(pod)}: " + ", ".join(parts)


def _failing_probes(events: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Pod -> probes falhando, pelos eventos `Unhealthy` ("Readiness probe failed: ...")."""
    found: dict[str, list[str]] = {}
    for event in events:
        message = str(event.get("message") or "")
        if event.get("reason") == "Unhealthy" and " probe " in message:
            probe = message.split(" probe ", 1)[0].strip().lower()
            pod = str(_at(event, "involvedObject", "name"))
            found.setdefault(pod, []).append(probe)
    return found


def _event_line(moment: datetime, event: _Json) -> str:
    involved: _Json = event.get("involvedObject") or _NONE
    count = int(event.get("count") or 1)
    times = f" (x{count})" if count > 1 else ""
    message = _short(" ".join(str(event.get("message") or "").split()), TEXT_CHARS)
    kind = str(involved.get("kind", "")).lower()
    return f"{_hhmm(moment)} {event.get('reason')} {kind}/{involved.get('name')}: {message}{times}"


def _hpa_of(workload: _Workload, hpas: list[_Json]) -> _Json | None:
    for hpa in hpas:
        target: _Json = _at(hpa, "spec", "scaleTargetRef") or _NONE
        if target.get("kind") == workload.kind and target.get("name") == workload.name:
            return hpa
    return None


def _hpa_line(hpa: _Json) -> str:
    current = _at(hpa, "status", "currentReplicas") or 0
    desired = _at(hpa, "status", "desiredReplicas") or 0
    maximum = _at(hpa, "spec", "maxReplicas") or 0
    if maximum and current >= maximum:
        return f"HPA {_name(hpa)}: no máximo ({current} de {maximum}), {desired} desejadas"
    return f"HPA {_name(hpa)}: {current} atuais, {desired} desejadas, máximo {maximum}"


def _config_maps(template: _Json) -> list[str]:
    """Os ConfigMaps que o pod usa: variáveis, `envFrom` e volumes (inclusive projetados)."""
    names: list[Any] = []
    spec: _Json = template.get("spec") or _NONE
    containers: list[_Json] = [
        *(spec.get("initContainers") or _NO_ITEMS),
        *(spec.get("containers") or _NO_ITEMS),
    ]
    for container in containers:
        for variable in container.get("env") or _NO_ITEMS:
            names.append(_at(variable, "valueFrom", "configMapKeyRef", "name"))
        for source in container.get("envFrom") or _NO_ITEMS:
            names.append(_at(source, "configMapRef", "name"))
    for volume in spec.get("volumes") or _NO_ITEMS:
        names.append(_at(volume, "configMap", "name"))
        for source in _at(volume, "projected", "sources") or _NO_ITEMS:
            names.append(_at(source, "configMap", "name"))
    return list(dict.fromkeys(name for name in names if isinstance(name, str)))


_RESOURCES = {"cpu": "cpu", "memory": "memória"}
_BOUNDS = {"limits": "limite", "requests": "pedido"}


def _template_diff(old: _Json, new: _Json, *, show_values: bool) -> list[str]:
    """O que mudou no template do pod: imagem, variáveis, recursos e o pedido de reinício."""
    before = {c.get("name"): c for c in _at(old, "spec", "containers") or _NO_ITEMS}
    after = {c.get("name"): c for c in _at(new, "spec", "containers") or _NO_ITEMS}
    many = len(after) > 1
    found: list[str] = []
    for name in before.keys() - after.keys():
        found.append(f"contêiner {name} removido")
    for name, container in after.items():
        prefix = f"{name}: " if many else ""
        previous = before.get(name)
        if previous is None:
            found.append(f"contêiner {name} adicionado")
            continue
        if previous.get("image") != container.get("image"):
            found.append(f"{prefix}imagem {previous.get('image')} → {container.get('image')}")
        found += [prefix + line for line in _env_diff(previous, container, show_values=show_values)]
        for bound, bound_label in _BOUNDS.items():
            for resource, resource_label in _RESOURCES.items():
                was = _at(previous, "resources", bound, resource)
                now = _at(container, "resources", bound, resource)
                if was != now:
                    change = f"{was or 'nenhum'} → {now or 'nenhum'}"
                    found.append(f"{prefix}{bound_label} de {resource_label} {change}")
    restarted = (_at(old, "metadata", "annotations") or _NONE).get(RESTARTED_AT)
    if (_at(new, "metadata", "annotations") or _NONE).get(RESTARTED_AT) != restarted:
        found.append("reinício pedido (rollout restart)")
    return found or ["template alterado"]


def _env_diff(old: _Json, new: _Json, *, show_values: bool) -> list[str]:
    before = {v.get("name"): v for v in old.get("env") or _NO_ITEMS}
    after = {v.get("name"): v for v in new.get("env") or _NO_ITEMS}
    found: list[str] = [f"env {name} removida" for name in before.keys() - after.keys()]
    for name, variable in after.items():
        previous = before.get(name)
        if previous == variable:
            continue
        literal = "valueFrom" not in variable and (previous is None or "valueFrom" not in previous)
        if previous is None:
            shown = _env_source(variable, show_values=show_values)
            found.append(
                f"env {name}={shown} adicionada"
                if literal and show_values
                else f"env {name} adicionada ({shown})"
            )
        elif literal and not show_values:
            found.append(f"env {name} alterado ({HIDDEN})")
        else:
            was = _env_source(previous, show_values=show_values)
            now = _env_source(variable, show_values=show_values)
            found.append(f"env {name}: {was} → {now}")
    return found


def _env_source(variable: _Json, *, show_values: bool) -> str:
    """O valor literal (ou "valor oculto") ou só o nome da referência, nunca o valor dela."""
    source: _Json = variable.get("valueFrom")
    if source is None:
        return _short(str(variable.get("value", "")), VALUE_CHARS) if show_values else HIDDEN
    if "secretKeyRef" in source:
        return f"do Secret {_at(source, 'secretKeyRef', 'name')}"
    if "configMapKeyRef" in source:
        return f"do ConfigMap {_at(source, 'configMapKeyRef', 'name')}"
    return "de um campo do pod"


def _value(key: str, value: Any, *, show_values: bool) -> str:
    if value is None:
        return f"{key} removida"
    if not show_values:
        return f"{key} ({HIDDEN})"
    return f"{key}={_short(' '.join(str(value).split()), VALUE_CHARS)}"


def _noted(summary: str, notes: list[str]) -> ToolResult:
    """Acrescenta as leituras e os clusters que falharam, no fim, sem esconder o resto."""
    return ToolResult("\n".join([summary, *notes]), None)


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


def _hhmm(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%H:%M")
