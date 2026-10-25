"""AwsCloudInventory com clientes boto3 reais sob `botocore.stub.Stubber`, sem rede.

O Stubber confere cada chamada e cada resposta contra o modelo do serviço da AWS. As respostas
são recortes das reais do laboratório (conta 851725637288, us-east-1): o banco da pedidos-api
parado manualmente e a máquina rodando.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import boto3  # pyright: ignore[reportMissingTypeStubs] — o boto3 não traz stubs de tipo
import pytest
from botocore.exceptions import (  # pyright: ignore[reportMissingTypeStubs] — sem stubs
    EndpointConnectionError,
)
from botocore.stub import Stubber  # pyright: ignore[reportMissingTypeStubs] — sem stubs
from contracts.cloud_inventory_contract import CloudInventoryContract
from grolla.adapters.aws import READ_OPERATIONS, AwsCloudInventory
from grolla.application.investigation import CloudInventory, ToolUnavailableError

REGION = "us-east-1"
ACCOUNT = "851725637288"
DB_ARN = f"arn:aws:rds:{REGION}:{ACCOUNT}:db:grolla-lab-db"
EC2_ARN = f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-0def690e1aea64498"
LB_ARN = (
    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/pedidos-lb/50dc6c495c0c9188"
)
SINCE = datetime(2026, 10, 25, 21, 0, tzinfo=UTC)
STOPPED_AT = datetime(2026, 10, 25, 21, 42, 5, tzinfo=UTC)  # 18:42 em -03:00 no CloudTrail
TAGGED = {
    "ResourceTagMappingList": [
        {"ResourceARN": EC2_ARN, "Tags": [{"Key": "service", "Value": "pedidos-api"}]},
        {"ResourceARN": DB_ARN, "Tags": [{"Key": "service", "Value": "pedidos-api"}]},
    ]
}
TAG_QUERY = {"TagFilters": [{"Key": "service", "Values": ["pedidos-api"]}]}
NOTHING_TAGGED: dict[str, Any] = {"ResourceTagMappingList": []}


def event(name: str, when: datetime, user: str, *, read_only: bool = False) -> dict[str, Any]:
    return {
        "EventId": f"{name}-{when.isoformat()}",
        "EventName": name,
        "ReadOnly": "true" if read_only else "false",
        "Username": user,
        "EventTime": when,
        "EventSource": "rds.amazonaws.com",
        "Resources": [{"ResourceType": "AWS::RDS::DBInstance", "ResourceName": "grolla-lab-db"}],
    }


class StubbedSession:
    """Faz o papel do `boto3.Session`: entrega um cliente real com Stubber por serviço e região."""

    def __init__(self) -> None:
        self._session: Any = boto3.Session(
            aws_access_key_id="teste",
            aws_secret_access_key="teste",  # noqa: S106 — credencial fake: o Stubber não assina
        )
        self._clients: dict[tuple[str, str], tuple[Any, Any]] = {}

    def stub(self, service: str, region: str = REGION) -> Any:
        key = (service, region)
        if key not in self._clients:
            client = self._session.client(service, region_name=region)
            stubber = Stubber(client)
            stubber.activate()
            self._clients[key] = (client, stubber)
        return self._clients[key][1]

    def client(self, service: str, region_name: str, config: Any) -> Any:  # noqa: ARG002
        self.stub(service, region_name)
        return self._clients[(service, region_name)][0]

    def assert_no_pending(self) -> None:
        for _, stubber in self._clients.values():
            stubber.assert_no_pending_responses()


@pytest.fixture
def session() -> StubbedSession:
    return StubbedSession()


def inventory(
    session: StubbedSession,
    resource_map: dict[str, list[str]] | None = None,
    regions: tuple[str, ...] = (REGION,),
) -> AwsCloudInventory:
    return AwsCloudInventory(regions, resource_map or {}, session=session)


class LabSession:
    """Responde por operação, em qualquer ordem: a suíte de contrato chama cada consulta à parte.

    As respostas são as mesmas conferidas pelo Stubber nos testes abaixo.
    """

    ANSWERS: ClassVar[dict[str, Any]] = {
        "describe_db_instances": {
            "DBInstances": [
                {"DBInstanceIdentifier": "grolla-lab-db", "DBInstanceStatus": "stopped"}
            ]
        },
        "describe_instances": {"Reservations": []},
        "lookup_events": {"Events": []},
        "describe_alarms": {"MetricAlarms": []},
    }

    def client(self, service: str, region_name: str, config: Any) -> Any:  # noqa: ARG002
        return self

    def get_resources(self, TagFilters: list[dict[str, Any]]) -> Any:  # noqa: N803 — API da AWS
        return TAGGED if TagFilters[0]["Values"] == ["pedidos-api"] else NOTHING_TAGGED

    def __getattr__(self, operation: str) -> Any:
        def answer(**_arguments: Any) -> Any:
            return self.ANSWERS[operation]

        return answer


class TestAwsCloudInventoryContract(CloudInventoryContract):
    @pytest.fixture
    def cloud(self) -> CloudInventory:
        return AwsCloudInventory([REGION], {}, session=LabSession())

    @pytest.fixture
    def unavailable_cloud(self) -> CloudInventory:
        return AwsCloudInventory([REGION], {}, session=UnreachableSession())


def stub_lab_states(session: StubbedSession) -> None:
    session.stub("rds").add_response(
        "describe_db_instances",
        {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "grolla-lab-db",
                    "DBInstanceArn": DB_ARN,
                    "DBInstanceStatus": "stopped",
                    "Engine": "postgres",
                }
            ]
        },
        {"Filters": [{"Name": "db-instance-id", "Values": ["grolla-lab-db"]}]},
    )
    session.stub("ec2").add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-0def690e1aea64498",
                            "InstanceType": "t3.micro",
                            "State": {"Code": 16, "Name": "running"},
                        }
                    ]
                }
            ]
        },
        {"Filters": [{"Name": "instance-id", "Values": ["i-0def690e1aea64498"]}]},
    )


async def test_resources_list_the_tagged_database_stopped_and_the_machine_running(
    session: StubbedSession,
) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", TAGGED, TAG_QUERY)
    stub_lab_states(session)

    result = await inventory(session).resources("pedidos-api")

    console = f"https://{REGION}.console.aws.amazon.com"
    assert result.summary == (
        "2 recurso(s) de nuvem de pedidos-api: "
        f"máquina EC2 i-0def690e1aea64498 (t3.micro) em {REGION}: running "
        f"[console: {console}/ec2/home?region={REGION}#InstanceDetails:instanceId="
        "i-0def690e1aea64498]; "
        f"banco RDS grolla-lab-db (postgres) em {REGION}: stopped "
        f"[console: {console}/rds/home?region={REGION}#database:id=grolla-lab-db]"
    )
    assert result.url == (
        f"{console}/ec2/home?region={REGION}#InstanceDetails:instanceId=i-0def690e1aea64498"
    )
    session.assert_no_pending()


async def test_map_adds_resources_without_the_tag_and_load_balancers_have_state(
    session: StubbedSession,
) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", NOTHING_TAGGED)
    session.stub("elbv2").add_response(
        "describe_load_balancers",
        {
            "LoadBalancers": [
                {
                    "LoadBalancerArn": LB_ARN,
                    "LoadBalancerName": "pedidos-lb",
                    "Type": "application",
                    "State": {"Code": "active"},
                }
            ]
        },
        {"LoadBalancerArns": [LB_ARN]},
    )
    mapping = {"pedidos-api": [LB_ARN, "arn:aws:sqs:us-east-1:851725637288:pedidos", "fila-x"]}

    result = await inventory(session, mapping).resources("pedidos-api")

    assert result.summary.startswith(
        f"3 recurso(s) de nuvem de pedidos-api: balanceador ELB pedidos-lb (application) "
        f"em {REGION}: active [console: "
    )
    assert f"; recurso arn:aws:sqs:us-east-1:{ACCOUNT}:pedidos em {REGION}; " in result.summary
    assert result.summary.endswith(f"; recurso fila-x em {REGION}")
    session.assert_no_pending()


async def test_resource_gone_from_the_describe_is_not_found(session: StubbedSession) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", NOTHING_TAGGED)
    session.stub("ec2").add_response("describe_instances", {"Reservations": []})

    result = await inventory(session, {"pedidos-api": ["i-0abc"]}).resources("pedidos-api")

    assert result.summary == (
        f"1 recurso(s) de nuvem de pedidos-api: máquina EC2 i-0abc em {REGION}: não encontrado "
        f"[console: https://{REGION}.console.aws.amazon.com/ec2/home?region={REGION}"
        "#InstanceDetails:instanceId=i-0abc]"
    )


async def test_every_region_is_searched_by_tag(session: StubbedSession) -> None:
    for region in ("us-east-1", "sa-east-1"):
        session.stub("resourcegroupstaggingapi", region).add_response(
            "get_resources", NOTHING_TAGGED, TAG_QUERY
        )

    result = await inventory(session, regions=("us-east-1", "sa-east-1")).resources("pedidos-api")

    assert result.summary == "nenhum recurso de nuvem encontrado para o serviço pedidos-api"
    assert result.url is None
    session.assert_no_pending()


async def test_changes_show_who_stopped_the_database_and_skip_reads(
    session: StubbedSession,
) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", TAGGED)
    trail = session.stub("cloudtrail")
    trail.add_response(
        "lookup_events",
        {"Events": []},
        {
            "LookupAttributes": [
                {"AttributeKey": "ResourceName", "AttributeValue": "i-0def690e1aea64498"}
            ],
            "StartTime": SINCE,
            "MaxResults": 50,
        },
    )
    stopped = STOPPED_AT
    trail.add_response(
        "lookup_events",
        {
            "Events": [
                event("DescribeDBInstances", stopped + timedelta(minutes=5), "grolla"),
                event(
                    "ListTagsForResource", stopped + timedelta(minutes=4), "root", read_only=True
                ),
                event("StopDBInstance", stopped, "root"),
                event("ModifyDBInstance", stopped - timedelta(minutes=30), "ana"),
            ]
        },
        {
            "LookupAttributes": [
                {"AttributeKey": "ResourceName", "AttributeValue": "grolla-lab-db"}
            ],
            "StartTime": SINCE,
            "MaxResults": 50,
        },
    )

    result = await inventory(session).changes("pedidos-api", SINCE)

    assert result.summary == (
        "2 mudança(s) nos recursos de pedidos-api desde 21:00 UTC (UTC): "
        "21:42 StopDBInstance em grolla-lab-db por root; "
        "21:12 ModifyDBInstance em grolla-lab-db por ana. "
        "Eventos de auditoria podem levar até 5 min para aparecer."
    )
    assert result.url == (
        f"https://{REGION}.console.aws.amazon.com/cloudtrail/home?region={REGION}#/events"
    )
    session.assert_no_pending()


async def test_changes_keep_the_twenty_most_recent(session: StubbedSession) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", NOTHING_TAGGED)
    start = STOPPED_AT
    events = [event("RebootDBInstance", start + timedelta(minutes=i), "ana") for i in range(25)]
    session.stub("cloudtrail").add_response("lookup_events", {"Events": events})

    result = await inventory(session, {"pedidos-api": [DB_ARN]}).changes("pedidos-api", SINCE)

    assert result.summary.startswith("20 mudança(s)")
    assert "22:06 RebootDBInstance" in result.summary
    assert "21:46 RebootDBInstance" not in result.summary


async def test_no_changes_still_warns_about_the_audit_delay(session: StubbedSession) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", NOTHING_TAGGED)
    session.stub("cloudtrail").add_response("lookup_events", {"Events": []})

    result = await inventory(session, {"pedidos-api": [DB_ARN]}).changes("pedidos-api", SINCE)

    assert result.summary == (
        "nenhuma mudança nos recursos de pedidos-api desde 21:00 UTC. "
        "Eventos de auditoria podem levar até 5 min para aparecer."
    )


async def test_alarms_are_the_ones_citing_the_service_resources(session: StubbedSession) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", TAGGED)
    alarm = {
        "AlarmName": "grolla-lab-db-sem-conexoes",
        "StateValue": "ALARM",
        "StateReason": "Threshold Crossed: 1 datapoint [0.0] was less than the threshold (1.0).",
        "StateUpdatedTimestamp": STOPPED_AT + timedelta(minutes=3),
        "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": "grolla-lab-db"}],
    }
    other = {
        **alarm,
        "AlarmName": "outro-servico",
        "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": "outro-db"}],
    }
    session.stub("cloudwatch").add_response(
        "describe_alarms",
        {"MetricAlarms": [alarm, other]},
        {"StateValue": "ALARM", "MaxRecords": 100},
    )

    result = await inventory(session).alarms("pedidos-api")

    assert result.summary == (
        "1 alarme(s) ativo(s) de pedidos-api: grolla-lab-db-sem-conexoes desde 21:45 UTC: "
        "Threshold Crossed: 1 datapoint [0.0] was less than the threshold (1.0)."
    )
    assert result.url == (
        f"https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#alarmsV2:"
    )


async def test_load_balancer_alarm_matches_the_cloudwatch_dimension(
    session: StubbedSession,
) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", NOTHING_TAGGED)
    alarm = {
        "AlarmName": "pedidos-lb-5xx",
        "StateReason": "x" * 300,
        "StateUpdatedTimestamp": STOPPED_AT,
        "Dimensions": [{"Name": "LoadBalancer", "Value": "app/pedidos-lb/50dc6c495c0c9188"}],
    }
    session.stub("cloudwatch").add_response("describe_alarms", {"MetricAlarms": [alarm]})

    result = await inventory(session, {"pedidos-api": [LB_ARN]}).alarms("pedidos-api")

    assert "pedidos-lb-5xx desde 21:42 UTC: " in result.summary
    assert result.summary.endswith("…")


async def test_no_active_alarms(session: StubbedSession) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", TAGGED)
    session.stub("cloudwatch").add_response("describe_alarms", {"MetricAlarms": []})

    result = await inventory(session).alarms("pedidos-api")

    assert result.summary == "nenhum alarme ativo nos recursos de pedidos-api"


@pytest.mark.parametrize("query", ["changes", "alarms"])
async def test_service_without_resources_is_an_answer(session: StubbedSession, query: str) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", NOTHING_TAGGED)
    cloud = inventory(session)

    result = await (cloud.changes("x", SINCE) if query == "changes" else cloud.alarms("x"))

    assert result.summary == "nenhum recurso de nuvem encontrado para o serviço x"


async def test_denied_permission_is_unavailable(session: StubbedSession) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", TAGGED)
    session.stub("cloudtrail").add_client_error(
        "lookup_events", service_error_code="AccessDeniedException", http_status_code=400
    )

    with pytest.raises(ToolUnavailableError, match="AccessDeniedException"):
        await inventory(session).changes("pedidos-api", SINCE)


class UnreachableSession:
    """Rede fora: todo cliente levanta o erro de conexão do botocore."""

    def client(self, service: str, region_name: str, config: Any) -> Any:  # noqa: ARG002
        return self

    def get_resources(self, **_arguments: Any) -> Any:
        raise EndpointConnectionError(endpoint_url="https://tagging.us-east-1.amazonaws.com")


async def test_network_failure_is_unavailable() -> None:
    cloud = AwsCloudInventory([REGION], {}, session=UnreachableSession())

    with pytest.raises(ToolUnavailableError, match="EndpointConnectionError"):
        await cloud.resources("pedidos-api")


def test_only_read_operations_are_allowed() -> None:
    reads = ("get_", "describe_", "lookup_")
    assert all(operation.startswith(reads) for _, operation in READ_OPERATIONS)


def test_operation_outside_the_list_is_refused(session: StubbedSession) -> None:
    with pytest.raises(ToolUnavailableError, match="fora da lista de leitura"):
        inventory(session).call("rds", REGION, "stop_db_instance")
