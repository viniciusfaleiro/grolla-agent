"""AWS: recursos, mudanças, alarmes, eventos do AWS Health e cotas, só leitura, em várias contas.

O boto3 é síncrono: cada consulta roda em `asyncio.to_thread`. Só chamadas de leitura de uma
lista fechada; as credenciais vêm da cadeia padrão do SDK (ambiente ou role da máquina).

Contas: a "principal" é a das credenciais do ambiente, nas `regions`. Cada `CloudAccount` é lida
por uma role somente leitura assumida pelo STS (sessão em cache até perto de expirar). Toda
consulta percorre todas as contas; com mais de uma, cada item diz a conta ("na conta dados"). A
falha de uma conta vira a linha "conta <nome>: <erro>" sem esconder as outras; só quando todas
falham a consulta levanta `ToolUnavailableError`.

Os recursos do serviço são os marcados com a tag `service=<nome>` (Resource Groups Tagging API)
mais os do `resource_map` (um ARN vai para a conta do account id dele; o resto, para a
principal). O estado vem de describes por tipo: banco RDS, máquina EC2, balanceador ELBv2,
serviço e cluster ECS (tarefas desejadas, rodando e pendentes, deployment, tarefas paradas
recentemente) e cluster EKS com os nodegroups. Outros tipos aparecem só com o ARN.

A visão geral (`overview`) junta, em cada região, todos os recursos com tag (Tagging API,
paginada) e todos os bancos, máquinas, balanceadores, clusters e serviços ECS e clusters EKS dos
describes, mesmo os sem nenhuma tag, agrupados pela tag `service` (ou "sem serviço") e pelo tipo.

Eventos (`health`): AWS Health (endpoint global em us-east-1), eventos abertos e programados nas
regiões da conta; os eventos com entidades afetadas só entram se citam recursos do serviço. Sem
o plano de suporte que libera a API, a resposta é HEALTH_UNAVAILABLE.

Cotas (`quotas`): lista fechada das que costumam derrubar serviço, por conta e região: RDS
(instâncias e armazenamento), vCPUs sob demanda padrão do EC2 (Service Quotas, uso pela métrica
do CloudWatch que a própria cota indica) e balanceadores de aplicação (limite e contagem).
"""

import asyncio
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

# O boto3 não traz stubs de tipo: os clientes são tratados como Any, e os testes conferem as
# chamadas contra o modelo de cada serviço pelo Stubber.
import boto3  # pyright: ignore[reportMissingTypeStubs] — sem stubs (ver acima)
from botocore.config import Config  # pyright: ignore[reportMissingTypeStubs] — sem stubs
from botocore.exceptions import (  # pyright: ignore[reportMissingTypeStubs] — sem stubs
    BotoCoreError,
    ClientError,
)

from grolla.application.investigation import (
    HEALTH_UNAVAILABLE,
    NO_CLOUD_RESOURCES,
    SHARED_RESOURCES,
    ToolResult,
    ToolUnavailableError,
)

# Lista fechada: só estas operações saem daqui, mesmo que a credencial permita mais. Todas são
# leitura; `assume_role` só obtém a credencial temporária da role somente leitura de outra conta.
READ_OPERATIONS = frozenset(
    {
        ("resourcegroupstaggingapi", "get_resources"),
        ("rds", "describe_db_instances"),
        ("rds", "describe_account_attributes"),
        ("ec2", "describe_instances"),
        ("elbv2", "describe_load_balancers"),
        ("elbv2", "describe_account_limits"),
        ("ecs", "list_clusters"),
        ("ecs", "describe_clusters"),
        ("ecs", "list_services"),
        ("ecs", "describe_services"),
        ("ecs", "list_tasks"),
        ("ecs", "describe_tasks"),
        ("eks", "list_clusters"),
        ("eks", "describe_cluster"),
        ("eks", "list_nodegroups"),
        ("eks", "describe_nodegroup"),
        ("health", "describe_events"),
        ("health", "describe_affected_entities"),
        ("service-quotas", "get_service_quota"),
        ("cloudtrail", "lookup_events"),
        ("cloudwatch", "describe_alarms"),
        ("cloudwatch", "get_metric_statistics"),
        ("sts", "assume_role"),
    }
)

SERVICE_TAG = "service"
ARN_PARTS = 6  # arn:partição:serviço:região:conta:recurso
PRINCIPAL = "principal"  # a conta das credenciais do ambiente
SESSION_NAME = "grolla-somente-leitura"
RENEW_BEFORE = timedelta(minutes=5)  # renova a credencial assumida antes de expirar
HEALTH_REGION = "us-east-1"  # o AWS Health responde num endpoint global, em us-east-1
HEALTH_URL = "https://health.aws.amazon.com/health/home#/account/dashboard/open-issues"
MAX_OVERVIEW = 200  # recursos da visão geral que vão para o modelo
PAGE_SIZE = 100  # máximo da Tagging API por página
LIST_PAGE = 100  # clusters, serviços, tarefas e nodegroups por listagem
ECS_BATCH = 10  # máximo de serviços por describe_services
ENTITY_BATCH = 10  # máximo de eventos por describe_affected_entities
MAX_STOPPED = 3  # tarefas paradas recentemente mostradas por serviço ECS
MAX_CHANGES = 20
MAX_EVENTS = 20
EVENTS_PER_RESOURCE = 50
MAX_ALARMS = 100
REASON_CHARS = 200
NEAR_LIMIT = 0.8  # cota acima disso é destacada
EC2_VCPU_QUOTA = "L-1216C47A"  # Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances
USAGE_WINDOW = timedelta(hours=1)
AUDIT_DELAY = "Eventos de auditoria podem levar até 5 min para aparecer."
_READ_PREFIXES = ("Describe", "List", "Get")
_CONFIG = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Tipo de recurso -> como aparece para o modelo.
_KINDS = {
    "rds": "banco RDS",
    "ec2": "máquina EC2",
    "elb": "balanceador ELB",
    "ecs": "serviço ECS",
    "ecs-cluster": "cluster ECS",
    "eks": "cluster EKS",
}
_RDS_QUOTAS = {
    "DBInstances": "instâncias de banco",
    "AllocatedStorage": "armazenamento de banco (GiB)",
}


@dataclass(frozen=True)
class CloudAccount:
    """Outra conta, lida por uma role somente leitura assumida (STS AssumeRole)."""

    name: str
    role_arn: str
    regions: tuple[str, ...]


@dataclass(frozen=True)
class _Resource:
    region: str
    kind: str  # rds | ec2 | elb | ecs | ecs-cluster | eks | outro
    name: str  # identificador curto: grolla-lab-db, i-0abc…, <cluster>/<serviço> no ECS
    arn: str | None
    dimension: str  # valor da dimensão do CloudWatch que cita o recurso
    account: str | None  # nome da conta, só quando há mais de uma

    def label(self) -> str:
        return f"{_KINDS.get(self.kind, 'recurso')} {self.name}"


@dataclass(frozen=True)
class _State:
    state: str
    detail: str  # motor, tipo da máquina ou do balanceador, versão do Kubernetes


_Key = tuple[str, str, str, str]  # conta, região, tipo e identificador curto


@dataclass(frozen=True)
class _Seen:
    """Um recurso da visão geral: com a tag `service` (ou sem) e o estado, se o tipo tem."""

    resource: _Resource
    service: str | None
    state: _State | None


@dataclass(frozen=True)
class _Quota:
    label: str
    used: float | None  # None: sem dado de uso
    limit: float
    region: str
    account: str | None

    def share(self) -> float:
        return self.used / self.limit if self.used is not None and self.limit else -1.0

    def line(self) -> str:
        where = f"em {self.region}{_in(self.account)}"
        if self.used is None:
            return f"{self.label}: uso sem dado, limite {self.limit:.0f} {where}"
        percent = round(100 * self.share()) if self.limit else 0
        return f"{self.label}: {self.used:.0f} de {self.limit:.0f} ({percent}%) {where}"


class AwsCloudInventory:
    def __init__(
        self,
        regions: Sequence[str],
        resource_map: Mapping[str, Sequence[str]],
        session: Any = None,  # boto3.Session; testes injetam uma sessão com Stubber
        accounts: Sequence[CloudAccount] = (),  # contas além da principal
    ) -> None:
        self._regions = tuple(regions)
        base: Any = session or boto3.Session()
        lock = threading.Lock()  # criar cliente a partir da sessão não é thread-safe
        owners = {_account_id(account.role_arn): account.name for account in accounts}
        maps: dict[str, dict[str, list[str]]] = {}
        for service, items in resource_map.items():
            for item in items:
                owner = owners.get(_account_id(item) or "", PRINCIPAL)
                maps.setdefault(owner, {}).setdefault(service, []).append(item)
        everyone = [
            (PRINCIPAL, self._regions, None),
            *((a.name, a.regions, a.role_arn) for a in accounts),
        ]
        self._accounts = [
            _Account(
                name,
                regions=account_regions,
                role_arn=role_arn,
                label=name if accounts else None,
                session=base,
                lock=lock,
                resource_map=maps.get(name, {}),
            )
            for name, account_regions, role_arn in everyone
        ]

    async def resources(self, service: str) -> ToolResult:
        return await asyncio.to_thread(self._resources, service)

    async def changes(self, service: str, since: datetime) -> ToolResult:
        return await asyncio.to_thread(self._changes, service, since)

    async def alarms(self, service: str) -> ToolResult:
        return await asyncio.to_thread(self._alarms, service)

    async def overview(self) -> ToolResult:
        return await asyncio.to_thread(self._overview)

    async def health(self, service: str) -> ToolResult:
        return await asyncio.to_thread(self._health, service)

    async def quotas(self, service: str) -> ToolResult:  # noqa: ARG002 — cotas são da conta
        return await asyncio.to_thread(self._quotas)

    def call(self, service: str, region: str, operation: str, **arguments: Any) -> Any:
        """Chama uma operação de leitura na conta principal (fora de READ_OPERATIONS: recusa)."""
        return self._accounts[0].call(service, region, operation, **arguments)

    def _across[T](
        self, query: Callable[["_Account"], T]
    ) -> tuple[list[tuple["_Account", T]], list[str]]:
        """Roda a consulta em cada conta; devolve as respostas e uma linha por conta que falhou."""
        answers: list[tuple[_Account, T]] = []
        failures: list[tuple[_Account, str]] = []
        for account in self._accounts:
            try:
                answers.append((account, query(account)))
            except (ClientError, BotoCoreError) as error:
                failures.append((account, _reason(error)))
        notes = [f"conta {account.name}: {reason}" for account, reason in failures]
        if not answers:
            raise ToolUnavailableError(failures[0][1] if len(failures) == 1 else "; ".join(notes))
        return answers, notes

    # ---- Consultas (síncronas, numa thread)

    def _resources(self, service: str) -> ToolResult:
        answers, notes = self._across(lambda account: account.resources(service))
        found = [item for _, items in answers for item in items]
        if not found:
            return _noted(_none_found(service), notes)
        lines = [_resource_line(resource, state) for resource, state in found]
        summary = f"{len(found)} recurso(s) de nuvem de {service}: " + "; ".join(lines)
        return _noted(ToolResult(summary, _console(found[0][0])), notes)

    def _changes(self, service: str, since: datetime) -> ToolResult:
        answers, notes = self._across(lambda account: account.changes(service, since))
        regions = [region for _, (region, _) in answers if region]
        if not regions:
            return _noted(_none_found(service), notes)
        events = [event for _, (_, found) in answers for event in found]
        region = regions[0]
        url = f"https://{region}.console.aws.amazon.com/cloudtrail/home?region={region}#/events"
        where = f"nos recursos de {service} desde {_hhmm(since)} UTC"
        if not events:
            return _noted(ToolResult(f"nenhuma mudança {where}. {AUDIT_DELAY}", url), notes)
        recent = sorted(events, key=lambda e: e[0], reverse=True)[:MAX_CHANGES]
        lines = "; ".join(line for _, line in recent)
        summary = f"{len(recent)} mudança(s) {where} (UTC): {lines}. {AUDIT_DELAY}"
        return _noted(ToolResult(summary, url), notes)

    def _alarms(self, service: str) -> ToolResult:
        answers, notes = self._across(lambda account: account.alarms(service))
        regions = [region for _, (region, _) in answers if region]
        if not regions:
            return _noted(_none_found(service), notes)
        lines = [line for _, (_, found) in answers for line in found]
        region = regions[0]
        url = f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#alarmsV2:"
        if not lines:
            return _noted(ToolResult(f"nenhum alarme ativo nos recursos de {service}", url), notes)
        summary = f"{len(lines)} alarme(s) ativo(s) de {service}: " + "; ".join(lines)
        return _noted(ToolResult(summary, url), notes)

    def _overview(self) -> ToolResult:
        answers, notes = self._across(lambda account: account.overview())
        found: dict[_Key, _Seen] = {}
        for _, seen in answers:
            found |= seen
        regions = ", ".join(dict.fromkeys(r for account, _ in answers for r in account.regions))
        if not found:
            return _noted(
                ToolResult(f"nenhum recurso de nuvem encontrado em {regions}", None), notes
            )
        groups: dict[str, list[_Seen]] = {}
        for seen in found.values():
            groups.setdefault(seen.service or SHARED_RESOURCES, []).append(seen)
        order = sorted(groups, key=lambda name: (name == SHARED_RESOURCES, name))
        shown = 0
        parts: list[str] = []
        for name in order:
            items = sorted(
                groups[name],
                key=lambda s: (s.resource.account or "", s.resource.kind, s.resource.name),
            )
            items = items[: max(MAX_OVERVIEW - shown, 0)]
            shown += len(items)
            if items:
                parts.append(
                    f"{name}: " + "; ".join(_state_line(s.resource, s.state) for s in items)
                )
        more = f" (mostrando {MAX_OVERVIEW})" if len(found) > MAX_OVERVIEW else ""
        region = self._regions[0]
        url = (
            f"https://{region}.console.aws.amazon.com/resource-groups/tag-editor/"
            f"find-resources?region={region}"
        )
        summary = (
            f"{len(found)} recurso(s) de nuvem em {regions}{more}, pela tag {SERVICE_TAG}. "
            + ". ".join(parts)
        )
        return _noted(ToolResult(summary, url), notes)

    def _health(self, service: str) -> ToolResult:
        answers, notes = self._across(lambda account: account.health(service))
        answered = [events for _, events in answers if events is not None]
        if not answered:
            return _noted(ToolResult(HEALTH_UNAVAILABLE, None), notes)
        notes = [
            f"conta {account.name}: {HEALTH_UNAVAILABLE}"
            for account, events in answers
            if events is None
        ] + notes
        events = sorted(
            (event for found in answered for event in found), key=lambda e: e[0], reverse=True
        )[:MAX_EVENTS]
        regions = ", ".join(dict.fromkeys(r for account, _ in answers for r in account.regions))
        if not events:
            summary = f"nenhum evento aberto da nuvem em {regions} que afete {service}"
            return _noted(ToolResult(summary, HEALTH_URL), notes)
        lines = "; ".join(line for _, line in events)
        summary = f"{len(events)} evento(s) aberto(s) da nuvem em {regions} (UTC): {lines}"
        return _noted(ToolResult(summary, HEALTH_URL), notes)

    def _quotas(self) -> ToolResult:
        answers, notes = self._across(lambda account: account.quotas())
        rows = sorted(
            (row for _, found in answers for row in found), key=lambda q: q.share(), reverse=True
        )
        region = self._regions[0]
        url = f"https://{region}.console.aws.amazon.com/servicequotas/home?region={region}"
        if not rows:
            return _noted(ToolResult("nenhuma cota de nuvem lida", url), notes)
        near = sum(1 for row in rows if row.share() > NEAR_LIMIT)
        alert = f"{near} acima de {NEAR_LIMIT:.0%} do limite. " if near else ""
        summary = f"{alert}{len(rows)} cota(s) de nuvem, da mais perto do limite: " + "; ".join(
            row.line() for row in rows
        )
        return _noted(ToolResult(summary, url), notes)


class _Account:
    """Uma conta: a das credenciais do ambiente ou uma role assumida, com os clientes dela."""

    def __init__(  # noqa: PLR0913 — tudo que define a conta; montada só pelo inventário
        self,
        name: str,
        *,
        regions: tuple[str, ...],
        role_arn: str | None,
        label: str | None,  # nome mostrado em cada item; None com uma conta só
        session: Any,
        lock: threading.Lock,
        resource_map: Mapping[str, Sequence[str]],
    ) -> None:
        self.name = name
        self.regions = regions
        self._role_arn = role_arn
        self._label = label
        self._session = session
        self._lock = lock
        self._resource_map = resource_map
        self._clients: dict[tuple[str, str], Any] = {}
        self._credentials: dict[str, str] = {}
        self._expires: datetime | None = None

    def call(self, service: str, region: str, operation: str, **arguments: Any) -> Any:
        _allowed(service, operation)
        return getattr(self._client(service, region), operation)(**arguments)

    def _client(self, service: str, region: str) -> Any:
        with self._lock:
            if self._role_arn and (
                self._expires is None or datetime.now(UTC) >= self._expires - RENEW_BEFORE
            ):
                self._assume(self._role_arn)
            key = (service, region)
            if key not in self._clients:
                self._clients[key] = self._session.client(
                    service, region_name=region, config=_CONFIG, **self._credentials
                )
            return self._clients[key]

    def _assume(self, role_arn: str) -> None:
        """Credencial temporária da role; os clientes da credencial anterior são descartados."""
        _allowed("sts", "assume_role")
        sts = self._session.client("sts", region_name=self.regions[0], config=_CONFIG)
        answer = sts.assume_role(RoleArn=role_arn, RoleSessionName=SESSION_NAME)
        credentials = answer["Credentials"]
        self._credentials = {
            "aws_access_key_id": credentials["AccessKeyId"],
            "aws_secret_access_key": credentials["SecretAccessKey"],
            "aws_session_token": credentials["SessionToken"],
        }
        self._expires = credentials["Expiration"]
        self._clients.clear()

    # ---- Consultas de uma conta

    def resources(self, service: str) -> list[tuple[_Resource, _State | None]]:
        found = self.discover(service)
        states = self.states(found, detailed=True)
        return [(resource, states.get(resource)) for resource in found]

    def changes(
        self, service: str, since: datetime
    ) -> tuple[str | None, list[tuple[datetime, str]]]:
        """Região do primeiro recurso (None: sem recursos) e as mudanças, sem as leituras."""
        found = self.discover(service)
        events: dict[str, tuple[datetime, str]] = {}
        for resource in found:
            answer = self.call(
                "cloudtrail",
                resource.region,
                "lookup_events",
                LookupAttributes=[
                    {"AttributeKey": "ResourceName", "AttributeValue": resource.name}
                ],
                StartTime=since,
                MaxResults=EVENTS_PER_RESOURCE,
            )
            for event in answer.get("Events", []):
                name = str(event.get("EventName", "?"))
                if event.get("ReadOnly") == "true" or name.startswith(_READ_PREFIXES):
                    continue
                when: datetime = event["EventTime"]
                author = event.get("Username") or "?"
                target = f"{resource.name}{_in(resource.account)}"
                line = f"{_hhmm(when)} {name} em {target} por {author}"
                events[str(event.get("EventId", line))] = (when, line)
        return (found[0].region if found else None), list(events.values())

    def alarms(self, service: str) -> tuple[str | None, list[str]]:
        """Região do primeiro recurso (None: sem recursos) e os alarmes que citam os recursos."""
        found = self.discover(service)
        lines: list[str] = []
        for region in dict.fromkeys(r.region for r in found):
            cited = {r.dimension for r in found if r.region == region}
            answer = self.call(
                "cloudwatch",
                region,
                "describe_alarms",
                StateValue="ALARM",
                MaxRecords=MAX_ALARMS,
            )
            for alarm in answer.get("MetricAlarms", []):
                dimensions = {str(d.get("Value")) for d in alarm.get("Dimensions", [])}
                if dimensions & cited:
                    since: datetime = alarm["StateUpdatedTimestamp"]
                    reason = _clip(str(alarm.get("StateReason", "")))
                    name = f"{alarm['AlarmName']}{_in(self._label)}"
                    lines.append(f"{name} desde {_hhmm(since)} UTC: {reason}")
        return (found[0].region if found else None), lines

    def overview(self) -> dict[_Key, _Seen]:
        found: dict[_Key, _Seen] = {}
        for region in self.regions:
            tagged = self._all_tagged(region)
            found |= self._described(region)
            for resource, service in tagged:
                key = (self.name, resource.region, resource.kind, resource.name)
                described = found.get(key)
                if described is None:
                    found[key] = _Seen(resource, service, None)
                else:  # o describe já trouxe o estado; a tag vale da Tagging API
                    found[key] = _Seen(
                        described.resource, service or described.service, described.state
                    )
        return found

    def health(self, service: str) -> list[tuple[datetime, str]] | None:
        """Eventos abertos e programados nas regiões da conta; None: conta sem o plano."""
        try:
            answer = self.call(
                "health",
                HEALTH_REGION,
                "describe_events",
                filter={"regions": list(self.regions), "eventStatusCodes": ["open", "upcoming"]},
                maxResults=100,
            )
        except ClientError as error:
            if _code(error) == "SubscriptionRequiredException":
                return None
            raise
        events: list[Any] = answer.get("events", [])
        specific = [e["arn"] for e in events if e.get("eventScopeCode") == "ACCOUNT_SPECIFIC"]
        entities: dict[str, list[Any]] = {}
        for start in range(0, len(specific), ENTITY_BATCH):
            page = self.call(
                "health",
                HEALTH_REGION,
                "describe_affected_entities",
                filter={"eventArns": specific[start : start + ENTITY_BATCH]},
            )
            for entity in page.get("entities", []):
                entities.setdefault(str(entity.get("eventArn")), []).append(entity)
        cited: set[str] = set()
        if entities:
            for resource in self.discover(service):
                cited |= {resource.name, resource.dimension, resource.arn or resource.name}
        lines: list[tuple[datetime, str]] = []
        for event in events:
            affected = entities.get(str(event.get("arn")), [])
            if affected:
                mine = [
                    str(e.get("entityValue") or e.get("entityArn"))
                    for e in affected
                    if e.get("entityValue") in cited or e.get("entityArn") in cited
                ]
                if not mine:
                    continue
                reach = "afeta " + ", ".join(mine)
            elif event.get("eventScopeCode") == "ACCOUNT_SPECIFIC":
                reach = "afeta a conta"
            else:
                reach = "afeta a região"
            began_at: datetime | None = event.get("startTime")
            began = f", início {_when(began_at)}" if began_at else ""
            line = (
                f"{event.get('service', '?')} {event.get('eventTypeCode', '?')} "
                f"em {event.get('region', '?')}{_in(self._label)}: "
                f"{event.get('statusCode', '?')}{began}, {reach}"
            )
            lines.append((began_at or _EPOCH, line))
        return lines

    def quotas(self) -> list[_Quota]:
        rows: list[_Quota] = []
        for region in self.regions:
            for quota in self.call("rds", region, "describe_account_attributes")["AccountQuotas"]:
                label = _RDS_QUOTAS.get(quota["AccountQuotaName"])
                if label:
                    rows.append(_Quota(label, quota["Used"], quota["Max"], region, self._label))
            vcpus = self.call(
                "service-quotas",
                region,
                "get_service_quota",
                ServiceCode="ec2",
                QuotaCode=EC2_VCPU_QUOTA,
            )["Quota"]
            used = self._usage(region, vcpus.get("UsageMetric"))
            rows.append(
                _Quota("vCPUs de máquinas sob demanda", used, vcpus["Value"], region, self._label)
            )
            limits = self.call("elbv2", region, "describe_account_limits")["Limits"]
            albs = next(
                (float(x["Max"]) for x in limits if x.get("Name") == "application-load-balancers"),
                None,
            )
            if albs is not None:
                balancers = self.call("elbv2", region, "describe_load_balancers", PageSize=400)
                count = sum(
                    1 for lb in balancers["LoadBalancers"] if lb.get("Type") == "application"
                )
                rows.append(_Quota("balanceadores de aplicação", count, albs, region, self._label))
        return rows

    def _usage(self, region: str, metric: Any) -> float | None:
        """Último valor da métrica de uso que a cota indica (namespace AWS/Usage)."""
        if not metric:
            return None
        now = datetime.now(UTC)
        answer = self.call(
            "cloudwatch",
            region,
            "get_metric_statistics",
            Namespace=metric["MetricNamespace"],
            MetricName=metric["MetricName"],
            Dimensions=[
                {"Name": name, "Value": value}
                for name, value in metric.get("MetricDimensions", {}).items()
            ],
            StartTime=now - USAGE_WINDOW,
            EndTime=now,
            Period=300,
            Statistics=["Maximum"],
        )
        points: list[Any] = answer.get("Datapoints", [])
        if not points:
            return None
        return float(max(points, key=lambda p: p["Timestamp"])["Maximum"])

    def _all_tagged(self, region: str) -> list[tuple[_Resource, str | None]]:
        """Todos os recursos com alguma tag na região e o valor da tag `service`, se houver."""
        tagged: list[tuple[_Resource, str | None]] = []
        token = ""
        while True:
            arguments: dict[str, Any] = {"ResourcesPerPage": PAGE_SIZE}
            if token:
                arguments["PaginationToken"] = token
            answer = self.call("resourcegroupstaggingapi", region, "get_resources", **arguments)
            for mapping in answer["ResourceTagMappingList"]:
                tags = {t["Key"]: t["Value"] for t in mapping.get("Tags", [])}
                resource = _parse(mapping["ResourceARN"], region, self._label)
                tagged.append((resource, tags.get(SERVICE_TAG)))
            token = answer.get("PaginationToken") or ""
            if not token:
                return tagged

    def _described(self, region: str) -> dict[_Key, _Seen]:
        """Todos os recursos dos tipos conhecidos na região com o estado, inclusive os sem tag."""
        seen: dict[_Key, _Seen] = {}

        def add(resource: _Resource, state: _State | None, tags: Any = ()) -> None:
            service = next((t["Value"] for t in tags if t.get("Key") == SERVICE_TAG), None)
            seen[(self.name, region, resource.kind, resource.name)] = _Seen(
                resource, service, state
            )

        def plain(kind: str, name: str, arn: str | None) -> _Resource:
            return _Resource(region, kind, name, arn, name, self._label)

        for db in self.call("rds", region, "describe_db_instances")["DBInstances"]:
            state = _State(db["DBInstanceStatus"], db.get("Engine", ""))
            resource = plain("rds", db["DBInstanceIdentifier"], db.get("DBInstanceArn"))
            add(resource, state, db.get("TagList", []))
        for reservation in self.call("ec2", region, "describe_instances")["Reservations"]:
            for i in reservation["Instances"]:
                state = _State(i["State"]["Name"], i.get("InstanceType", ""))
                add(plain("ec2", i["InstanceId"], None), state, i.get("Tags", []))
        for lb in self.call("elbv2", region, "describe_load_balancers")["LoadBalancers"]:
            state = _State(lb["State"]["Code"], lb.get("Type", ""))
            add(plain("elb", lb["LoadBalancerName"], lb.get("LoadBalancerArn")), state)
        containers: list[_Resource] = []
        for arn in self.call("ecs", region, "list_clusters", maxResults=LIST_PAGE)["clusterArns"]:
            cluster = _parse(arn, region, self._label)
            containers.append(cluster)
            services = self.call(
                "ecs", region, "list_services", cluster=cluster.name, maxResults=LIST_PAGE
            )["serviceArns"]
            containers += [_ecs_service(s, cluster.name, region, self._label) for s in services]
        for name in self.call("eks", region, "list_clusters", maxResults=LIST_PAGE)["clusters"]:
            containers.append(plain("eks", name, None))
        states = self.states(containers, detailed=False)
        for resource in containers:
            add(resource, states.get(resource))
        return seen

    # ---- Descoberta e estado

    def discover(self, service: str) -> list[_Resource]:
        arns: dict[str, None] = {}
        for region in self.regions:
            answer = self.call(
                "resourcegroupstaggingapi",
                region,
                "get_resources",
                TagFilters=[{"Key": SERVICE_TAG, "Values": [service]}],
            )
            arns.update(dict.fromkeys(m["ResourceARN"] for m in answer["ResourceTagMappingList"]))
        arns.update(dict.fromkeys(self._resource_map.get(service, ())))
        # Nodegroup do EKS vira o cluster dele: o mesmo cluster aparece uma vez só.
        return list(dict.fromkeys(_parse(item, self.regions[0], self._label) for item in arns))

    def states(self, found: Iterable[_Resource], *, detailed: bool) -> dict[_Resource, _State]:
        """Estado por recurso; `detailed` busca as tarefas paradas dos serviços ECS."""
        states: dict[_Resource, _State] = {}
        for (region, kind), group in _grouped(found).items():
            names = [r.name for r in group]
            if kind == "rds":
                answer = self.call(
                    "rds",
                    region,
                    "describe_db_instances",
                    Filters=[{"Name": "db-instance-id", "Values": names}],
                )
                known = {
                    db["DBInstanceIdentifier"]: _State(db["DBInstanceStatus"], db.get("Engine", ""))
                    for db in answer["DBInstances"]
                }
            elif kind == "ec2":
                answer = self.call(
                    "ec2",
                    region,
                    "describe_instances",
                    Filters=[{"Name": "instance-id", "Values": names}],
                )
                known = {
                    i["InstanceId"]: _State(i["State"]["Name"], i.get("InstanceType", ""))
                    for reservation in answer["Reservations"]
                    for i in reservation["Instances"]
                }
            elif kind == "elb":
                answer = self.call(
                    "elbv2",
                    region,
                    "describe_load_balancers",
                    LoadBalancerArns=[r.arn for r in group],
                )
                known = {
                    lb["LoadBalancerName"]: _State(lb["State"]["Code"], lb.get("Type", ""))
                    for lb in answer["LoadBalancers"]
                }
            elif kind == "ecs":
                known = self._ecs_services(region, names, detailed=detailed)
            elif kind == "ecs-cluster":
                answer = self.call("ecs", region, "describe_clusters", clusters=names)
                known = {c["clusterName"]: _ecs_cluster_state(c) for c in answer["clusters"]}
            elif kind == "eks":
                known = {name: self._eks_state(region, name) for name in names}
            else:
                continue
            for resource in group:
                states[resource] = known.get(resource.name, _State("não encontrado", ""))
        return states

    def _ecs_services(self, region: str, names: list[str], *, detailed: bool) -> dict[str, _State]:
        """Serviços `<cluster>/<serviço>`, descritos de 10 em 10 por cluster."""
        by_cluster: dict[str, list[str]] = {}
        for name in names:
            cluster, service = name.split("/", 1)
            by_cluster.setdefault(cluster, []).append(service)
        known: dict[str, _State] = {}
        for cluster, services in by_cluster.items():
            for start in range(0, len(services), ECS_BATCH):
                answer = self.call(
                    "ecs",
                    region,
                    "describe_services",
                    cluster=cluster,
                    services=services[start : start + ECS_BATCH],
                )
                for item in answer["services"]:
                    service = item["serviceName"]
                    stopped = self._stopped(region, cluster, service) if detailed else []
                    known[f"{cluster}/{service}"] = _ecs_service_state(item, stopped)
        return known

    def _stopped(self, region: str, cluster: str, service: str) -> list[str]:
        """As tarefas paradas mais recentes do serviço, com o motivo."""
        arns = self.call(
            "ecs",
            region,
            "list_tasks",
            cluster=cluster,
            serviceName=service,
            desiredStatus="STOPPED",
            maxResults=LIST_PAGE,
        )["taskArns"]
        if not arns:
            return []
        tasks = self.call("ecs", region, "describe_tasks", cluster=cluster, tasks=arns)["tasks"]
        recent = sorted(tasks, key=lambda t: t.get("stoppedAt") or _EPOCH, reverse=True)
        return [
            f"{_hhmm(t['stoppedAt']) if t.get('stoppedAt') else '?'} "
            f"{_clip(str(t.get('stoppedReason', '?')))}"
            for t in recent[:MAX_STOPPED]
        ]

    def _eks_state(self, region: str, name: str) -> _State:
        cluster = self.call("eks", region, "describe_cluster", name=name)["cluster"]
        state = f"{cluster.get('status', '?')}{_issues(cluster)}"
        groups = self.call(
            "eks", region, "list_nodegroups", clusterName=name, maxResults=LIST_PAGE
        )["nodegroups"]
        nodes: list[str] = []
        for group in groups:
            nodegroup = self.call(
                "eks", region, "describe_nodegroup", clusterName=name, nodegroupName=group
            )["nodegroup"]
            desired = nodegroup.get("scalingConfig", {}).get("desiredSize", "?")
            nodes.append(
                f"{group} {nodegroup.get('status', '?')} ({desired} nós desejados)"
                f"{_issues(nodegroup)}"
            )
        if nodes:
            state += ", nodegroups: " + ", ".join(nodes)
        version = cluster.get("version")
        return _State(state, f"Kubernetes {version}" if version else "")


def _allowed(service: str, operation: str) -> None:
    if (service, operation) not in READ_OPERATIONS:
        raise ToolUnavailableError(f"operação fora da lista de leitura: {service}.{operation}")


def _code(error: ClientError) -> str:
    details: Any = error.response
    return str(details.get("Error", {}).get("Code", "erro"))


def _reason(error: ClientError | BotoCoreError) -> str:
    if isinstance(error, ClientError):  # permissão, limite, recurso: a fonte recusou
        return f"AWS recusou a consulta: {_code(error)}"
    return f"AWS indisponível: {type(error).__name__}"  # credencial ausente, rede, timeout


def _noted(result: ToolResult, notes: list[str]) -> ToolResult:
    """Acrescenta as contas que não responderam, sem esconder as que responderam."""
    if not notes:
        return result
    return ToolResult(f"{result.summary.rstrip('.')}. " + "; ".join(notes), result.url)


def _parse(item: str, default_region: str, account: str | None) -> _Resource:
    """ARN ou identificador do mapa; identificador sem ARN fica na primeira região."""
    if not item.startswith("arn:"):
        kind = "ec2" if item.startswith("i-") else "outro"
        return _Resource(default_region, kind, item, None, item, account)
    parts = item.split(":", 5)
    service, region, resource = parts[2], parts[3] or default_region, parts[5]
    if service == "rds" and resource.startswith("db:"):
        name = resource.removeprefix("db:")
        return _Resource(region, "rds", name, item, name, account)
    if service == "ec2" and resource.startswith("instance/"):
        name = resource.removeprefix("instance/")
        return _Resource(region, "ec2", name, item, name, account)
    if service == "elasticloadbalancing" and resource.startswith(
        ("loadbalancer/app/", "loadbalancer/net/")
    ):
        dimension = resource.removeprefix("loadbalancer/")  # app/<nome>/<id>, como no CloudWatch
        return _Resource(region, "elb", dimension.split("/")[1], item, dimension, account)
    container = _container(item, region, account)
    if container is not None:
        return container
    return _Resource(region, "outro", item, item, resource.split("/")[-1], account)


def _container(arn: str, region: str, account: str | None) -> _Resource | None:
    """Serviço ou cluster do ECS e cluster do EKS (um nodegroup vira o cluster dele)."""
    _, partition, service, _, account_id, resource = arn.split(":", 5)
    if service == "ecs" and resource.startswith("service/"):
        return _ecs_service(arn, "default", region, account)
    if service == "ecs" and resource.startswith("cluster/"):
        name = resource.removeprefix("cluster/")
        return _Resource(region, "ecs-cluster", name, arn, name, account)
    if service == "eks" and resource.startswith(("cluster/", "nodegroup/")):
        cluster = resource.split("/")[1]
        cluster_arn = f"arn:{partition}:eks:{region}:{account_id}:cluster/{cluster}"
        return _Resource(region, "eks", cluster, cluster_arn, cluster, account)
    return None


def _account_id(arn: str) -> str | None:
    parts = arn.split(":", 5)
    return parts[4] if arn.startswith("arn:") and len(parts) == ARN_PARTS else None


def _ecs_service(arn: str, cluster: str, region: str, account: str | None) -> _Resource:
    """`service/<cluster>/<nome>` (ou o formato antigo `service/<nome>`, no `cluster` dado)."""
    path = arn.split(":", 5)[5].removeprefix("service/").split("/")
    cluster, name = (path[0], path[1]) if len(path) > 1 else (cluster, path[0])
    return _Resource(region, "ecs", f"{cluster}/{name}", arn, name, account)


def _ecs_service_state(service: Any, stopped: list[str]) -> _State:
    """'4 desejadas, 1 rodando, deployment em andamento, 3 pendentes, tarefas paradas…'."""
    parts = [
        f"{service.get('desiredCount', 0)} desejadas, {service.get('runningCount', 0)} rodando"
    ]
    deployments: list[Any] = service.get("deployments", [])
    empty: dict[str, Any] = {}
    primary: Any = next((d for d in deployments if d.get("status") == "PRIMARY"), empty)
    rollout = primary.get("rolloutState")
    if rollout == "FAILED":
        parts.append(f"deployment falhou: {_clip(str(primary.get('rolloutStateReason', '')))}")
    elif rollout == "IN_PROGRESS" or len(deployments) > 1:
        parts.append("deployment em andamento")
    if service.get("pendingCount"):
        parts.append(f"{service['pendingCount']} pendentes")
    if stopped:
        parts.append("tarefas paradas recentemente: " + " / ".join(stopped))
    status = service.get("status", "ACTIVE")
    state = ", ".join(parts)
    return _State(state if status == "ACTIVE" else f"{status}, {state}", "")


def _ecs_cluster_state(cluster: Any) -> _State:
    state = (
        f"{cluster.get('status', '?')}, {cluster.get('activeServicesCount', 0)} serviço(s), "
        f"{cluster.get('runningTasksCount', 0)} tarefa(s) rodando"
    )
    if cluster.get("pendingTasksCount"):
        state += f", {cluster['pendingTasksCount']} pendente(s)"
    return _State(state, "")


def _issues(item: Any) -> str:
    """Problemas de saúde do cluster ou nodegroup do EKS."""
    issues: list[Any] = item.get("health", {}).get("issues", [])
    if not issues:
        return ""
    found = " / ".join(f"{i.get('code', '?')}: {_clip(str(i.get('message', '')))}" for i in issues)
    return f" com problema: {found}"


def _grouped(found: Iterable[_Resource]) -> dict[tuple[str, str], list[_Resource]]:
    groups: dict[tuple[str, str], list[_Resource]] = {}
    for resource in found:
        groups.setdefault((resource.region, resource.kind), []).append(resource)
    return groups


def _resource_line(resource: _Resource, state: _State | None) -> str:
    line = _state_line(resource, state)
    console = _console(resource)
    return f"{line} [console: {console}]" if console else line


def _state_line(resource: _Resource, state: _State | None) -> str:
    """'banco RDS grolla-lab-db (postgres) em us-east-1: stopped'."""
    detail = f" ({state.detail})" if state and state.detail else ""
    line = f"{resource.label()}{detail} em {resource.region}{_in(resource.account)}"
    return f"{line}: {state.state}" if state is not None else line


def _in(account: str | None) -> str:
    return f" na conta {account}" if account else ""


def _console(resource: _Resource) -> str | None:
    home = f"https://{resource.region}.console.aws.amazon.com"
    query = f"?region={resource.region}"
    name = quote(resource.name)
    if resource.kind == "elb":
        arn = quote(resource.arn or "")
        return f"{home}/ec2/home{query}#LoadBalancer:loadBalancerArn={arn}" if arn else None
    if resource.kind == "ecs":
        cluster, service = (quote(part) for part in resource.name.split("/", 1))
        return f"{home}/ecs/v2/clusters/{cluster}/services/{service}/health{query}"
    paths = {
        "rds": f"rds/home{query}#database:id={name}",
        "ec2": f"ec2/home{query}#InstanceDetails:instanceId={name}",
        "ecs-cluster": f"ecs/v2/clusters/{name}/services{query}",
        "eks": f"eks/home{query}#/clusters/{name}",
    }
    path = paths.get(resource.kind)
    return f"{home}/{path}" if path else None


def _none_found(service: str) -> ToolResult:
    return ToolResult(f"{NO_CLOUD_RESOURCES} {service}", None)


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= REASON_CHARS else flat[: REASON_CHARS - 1] + "…"


def _hhmm(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%H:%M")


def _when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%d/%m %H:%M UTC")
