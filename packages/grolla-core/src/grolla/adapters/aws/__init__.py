"""AWS: recursos do serviço, mudanças no CloudTrail e alarmes do CloudWatch, só leitura.

O boto3 é síncrono: cada consulta roda em `asyncio.to_thread`. Só chamadas de leitura de uma
lista fechada; as credenciais vêm da cadeia padrão do SDK (ambiente ou role da máquina).

Os recursos do serviço são os marcados com a tag `service=<nome>` (Resource Groups Tagging API)
mais os do `resource_map`. O estado vem de uma chamada de describe por tipo encontrado: banco
RDS, máquina EC2 e balanceador ELBv2. Outros tipos aparecem só com o ARN.
"""

import asyncio
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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

from grolla.application.investigation import NO_CLOUD_RESOURCES, ToolResult, ToolUnavailableError

# Lista fechada: só estas operações de leitura saem daqui, mesmo que a credencial permita mais.
READ_OPERATIONS = frozenset(
    {
        ("resourcegroupstaggingapi", "get_resources"),
        ("rds", "describe_db_instances"),
        ("ec2", "describe_instances"),
        ("elbv2", "describe_load_balancers"),
        ("cloudtrail", "lookup_events"),
        ("cloudwatch", "describe_alarms"),
    }
)

SERVICE_TAG = "service"
MAX_CHANGES = 20
EVENTS_PER_RESOURCE = 50
MAX_ALARMS = 100
REASON_CHARS = 200
AUDIT_DELAY = "Eventos de auditoria podem levar até 5 min para aparecer."
_READ_PREFIXES = ("Describe", "List", "Get")
_CONFIG = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})

# Tipo de recurso -> como aparece para o modelo.
_KINDS = {"rds": "banco RDS", "ec2": "máquina EC2", "elb": "balanceador ELB"}


@dataclass(frozen=True)
class _Resource:
    region: str
    kind: str  # rds | ec2 | elb | outro
    name: str  # identificador curto: grolla-lab-db, i-0abc…, nome do balanceador
    arn: str | None
    dimension: str  # valor da dimensão do CloudWatch que cita o recurso

    def label(self) -> str:
        return f"{_KINDS.get(self.kind, 'recurso')} {self.name}"


@dataclass(frozen=True)
class _State:
    state: str
    detail: str  # motor, tipo da máquina ou do balanceador


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
        self._clients: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()  # criar cliente a partir da sessão não é thread-safe

    async def resources(self, service: str) -> ToolResult:
        return await self._run(lambda: self._resources(service))

    async def changes(self, service: str, since: datetime) -> ToolResult:
        return await self._run(lambda: self._changes(service, since))

    async def alarms(self, service: str) -> ToolResult:
        return await self._run(lambda: self._alarms(service))

    async def _run(self, query: Callable[[], ToolResult]) -> ToolResult:
        try:
            return await asyncio.to_thread(query)
        except ClientError as error:  # permissão, limite, recurso: a fonte recusou
            details: Any = error.response
            code = str(details.get("Error", {}).get("Code", "erro"))
            raise ToolUnavailableError(f"AWS recusou a consulta: {code}") from error
        except BotoCoreError as error:  # credencial ausente, rede, timeout
            raise ToolUnavailableError(f"AWS indisponível: {type(error).__name__}") from error

    # ---- Consultas (síncronas, numa thread)

    def _resources(self, service: str) -> ToolResult:
        found = self._discover(service)
        if not found:
            return _none_found(service)
        states = self._states(found)
        lines = [_resource_line(r, states.get(r)) for r in found]
        summary = f"{len(found)} recurso(s) de nuvem de {service}: " + "; ".join(lines)
        return ToolResult(summary, _console(found[0]))

    def _changes(self, service: str, since: datetime) -> ToolResult:
        found = self._discover(service)
        if not found:
            return _none_found(service)
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
                line = f"{_hhmm(when)} {name} em {resource.name} por {author}"
                events[str(event.get("EventId", line))] = (when, line)
        region = found[0].region
        url = f"https://{region}.console.aws.amazon.com/cloudtrail/home?region={region}#/events"
        where = f"nos recursos de {service} desde {_hhmm(since)} UTC"
        if not events:
            return ToolResult(f"nenhuma mudança {where}. {AUDIT_DELAY}", url)
        recent = sorted(events.values(), key=lambda e: e[0], reverse=True)[:MAX_CHANGES]
        lines = "; ".join(line for _, line in recent)
        return ToolResult(f"{len(recent)} mudança(s) {where} (UTC): {lines}. {AUDIT_DELAY}", url)

    def _alarms(self, service: str) -> ToolResult:
        found = self._discover(service)
        if not found:
            return _none_found(service)
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
                    lines.append(f"{alarm['AlarmName']} desde {_hhmm(since)} UTC: {reason}")
        region = found[0].region
        url = f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#alarmsV2:"
        if not lines:
            return ToolResult(f"nenhum alarme ativo nos recursos de {service}", url)
        return ToolResult(f"{len(lines)} alarme(s) ativo(s) de {service}: " + "; ".join(lines), url)

    # ---- Descoberta e estado

    def _discover(self, service: str) -> list[_Resource]:
        arns: dict[str, None] = {}
        for region in self._regions:
            answer = self.call(
                "resourcegroupstaggingapi",
                region,
                "get_resources",
                TagFilters=[{"Key": SERVICE_TAG, "Values": [service]}],
            )
            arns.update(dict.fromkeys(m["ResourceARN"] for m in answer["ResourceTagMappingList"]))
        arns.update(dict.fromkeys(self._resource_map.get(service, ())))
        return [_parse(item, self._regions[0]) for item in arns]

    def _states(self, found: Iterable[_Resource]) -> dict[_Resource, _State]:
        states: dict[_Resource, _State] = {}
        for (region, kind), group in _grouped(found).items():
            if kind == "rds":
                answer = self.call(
                    "rds",
                    region,
                    "describe_db_instances",
                    Filters=[{"Name": "db-instance-id", "Values": [r.name for r in group]}],
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
                    Filters=[{"Name": "instance-id", "Values": [r.name for r in group]}],
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
            else:
                continue
            for resource in group:
                states[resource] = known.get(resource.name, _State("não encontrado", ""))
        return states

    def call(self, service: str, region: str, operation: str, **arguments: Any) -> Any:
        """Chama uma operação de leitura; operação fora de READ_OPERATIONS é recusada."""
        if (service, operation) not in READ_OPERATIONS:
            raise ToolUnavailableError(f"operação fora da lista de leitura: {service}.{operation}")
        return getattr(self._client(service, region), operation)(**arguments)

    def _client(self, service: str, region: str) -> Any:
        with self._lock:
            key = (service, region)
            if key not in self._clients:
                session: Any = self._session or boto3.Session()
                self._clients[key] = session.client(service, region_name=region, config=_CONFIG)
            return self._clients[key]


def _parse(item: str, default_region: str) -> _Resource:
    """ARN ou identificador do mapa; identificador sem ARN fica na primeira região."""
    if not item.startswith("arn:"):
        kind = "ec2" if item.startswith("i-") else "outro"
        return _Resource(default_region, kind, item, None, item)
    parts = item.split(":", 5)
    service, region, resource = parts[2], parts[3] or default_region, parts[5]
    if service == "rds" and resource.startswith("db:"):
        name = resource.removeprefix("db:")
        return _Resource(region, "rds", name, item, name)
    if service == "ec2" and resource.startswith("instance/"):
        name = resource.removeprefix("instance/")
        return _Resource(region, "ec2", name, item, name)
    if service == "elasticloadbalancing" and resource.startswith(
        ("loadbalancer/app/", "loadbalancer/net/")
    ):
        dimension = resource.removeprefix("loadbalancer/")  # app/<nome>/<id>, como no CloudWatch
        return _Resource(region, "elb", dimension.split("/")[1], item, dimension)
    return _Resource(region, "outro", item, item, resource.split("/")[-1])


def _grouped(found: Iterable[_Resource]) -> dict[tuple[str, str], list[_Resource]]:
    groups: dict[tuple[str, str], list[_Resource]] = {}
    for resource in found:
        groups.setdefault((resource.region, resource.kind), []).append(resource)
    return groups


def _resource_line(resource: _Resource, state: _State | None) -> str:
    detail = f" ({state.detail})" if state and state.detail else ""
    line = f"{resource.label()}{detail} em {resource.region}"
    if state is not None:
        line += f": {state.state}"
    console = _console(resource)
    return f"{line} [console: {console}]" if console else line


def _console(resource: _Resource) -> str | None:
    home = f"https://{resource.region}.console.aws.amazon.com"
    query = f"?region={resource.region}"
    if resource.kind == "rds":
        return f"{home}/rds/home{query}#database:id={quote(resource.name)}"
    if resource.kind == "ec2":
        return f"{home}/ec2/home{query}#InstanceDetails:instanceId={quote(resource.name)}"
    if resource.kind == "elb" and resource.arn:
        return f"{home}/ec2/home{query}#LoadBalancer:loadBalancerArn={quote(resource.arn)}"
    return None


def _none_found(service: str) -> ToolResult:
    return ToolResult(f"{NO_CLOUD_RESOURCES} {service}", None)


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= REASON_CHARS else flat[: REASON_CHARS - 1] + "…"


def _hhmm(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%H:%M")
