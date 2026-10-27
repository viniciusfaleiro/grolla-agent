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
    ClientError,
    EndpointConnectionError,
)
from botocore.stub import ANY, Stubber  # pyright: ignore[reportMissingTypeStubs] — sem stubs
from contracts.cloud_inventory_contract import (
    CloudHealthQuotasContract,
    CloudInventoryContract,
    CloudOverviewContract,
)
from grolla.adapters.aws import READ_OPERATIONS, AwsCloudInventory, CloudAccount
from grolla.application.investigation import (
    HEALTH_UNAVAILABLE,
    CloudInventory,
    ToolResult,
    ToolUnavailableError,
)

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
        self._clients: dict[tuple[str, str, str], tuple[Any, Any]] = {}

    def stub(self, service: str, region: str = REGION, key_id: str = "teste") -> Any:
        """Stubber do cliente; `key_id` separa as contas pela credencial (a assumida ou não)."""
        key = (service, region, key_id)
        if key not in self._clients:
            client = self._session.client(service, region_name=region)
            stubber = Stubber(client)
            stubber.activate()
            self._clients[key] = (client, stubber)
        return self._clients[key][1]

    def client(
        self,
        service: str,
        region_name: str,
        config: Any,  # noqa: ARG002
        aws_access_key_id: str = "teste",
        **_credentials: str,
    ) -> Any:
        self.stub(service, region_name, aws_access_key_id)
        return self._clients[(service, region_name, aws_access_key_id)][0]

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
        "describe_load_balancers": {  # sem a tag: vai para o grupo "sem serviço"
            "LoadBalancers": [
                {
                    "LoadBalancerArn": LB_ARN,
                    "LoadBalancerName": "pedidos-lb",
                    "State": {"Code": "active"},
                }
            ]
        },
        "lookup_events": {"Events": []},
        "describe_alarms": {"MetricAlarms": []},
        "list_clusters": {"clusterArns": [], "clusters": []},  # ECS e EKS
        "describe_events": {"events": []},
        "describe_account_attributes": {
            "AccountQuotas": [{"AccountQuotaName": "DBInstances", "Used": 1, "Max": 40}]
        },
        "get_service_quota": {"Quota": {"QuotaCode": "L-1216C47A", "Value": 32.0}},
        "describe_account_limits": {
            "Limits": [{"Name": "application-load-balancers", "Max": "50"}]
        },
    }

    def client(self, service: str, region_name: str, config: Any) -> Any:  # noqa: ARG002
        return self

    def get_resources(
        self,
        TagFilters: list[dict[str, Any]] | None = None,  # noqa: N803 — API da AWS
        **_arguments: Any,
    ) -> Any:
        if TagFilters is None:  # visão geral: tudo que tem tag
            return TAGGED
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


class NoHealthPlanSession(LabSession):
    """Conta sem o plano de suporte que libera o AWS Health."""

    def describe_events(self, **_arguments: Any) -> Any:
        raise ClientError(
            {"Error": {"Code": "SubscriptionRequiredException", "Message": "sem plano"}},
            "DescribeEvents",
        )


class TestAwsCloudHealthQuotasContract(CloudHealthQuotasContract):
    @pytest.fixture
    def cloud(self) -> CloudInventory:
        return AwsCloudInventory([REGION], {}, session=LabSession())

    @pytest.fixture
    def unavailable_cloud(self) -> CloudInventory:
        return AwsCloudInventory([REGION], {}, session=UnreachableSession())

    @pytest.fixture
    def cloud_without_health_plan(self) -> CloudInventory:
        return AwsCloudInventory([REGION], {}, session=NoHealthPlanSession())


class TestAwsCloudOverviewContract(CloudOverviewContract):
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

    def __getattr__(self, operation: str) -> Any:
        def fail(**_arguments: Any) -> Any:
            raise EndpointConnectionError(endpoint_url=f"https://{operation}.amazonaws.com")

        return fail


async def test_network_failure_is_unavailable() -> None:
    cloud = AwsCloudInventory([REGION], {}, session=UnreachableSession())

    with pytest.raises(ToolUnavailableError, match="EndpointConnectionError"):
        await cloud.resources("pedidos-api")


def test_only_read_operations_are_allowed() -> None:
    reads = ("get_", "describe_", "lookup_", "list_")
    others = {op for op in READ_OPERATIONS if not op[1].startswith(reads)}
    assert others == {("sts", "assume_role")}  # só a credencial da role somente leitura


def test_operation_outside_the_list_is_refused(session: StubbedSession) -> None:
    with pytest.raises(ToolUnavailableError, match="fora da lista de leitura"):
        inventory(session).call("rds", REGION, "stop_db_instance")


# ---- Visão geral: tudo das regiões, agrupado pela tag `service`

SG_ARN = f"arn:aws:ec2:{REGION}:{ACCOUNT}:security-group/sg-0a1b2c3d4e5f60718"
PAGE = {"ResourcesPerPage": 100}


def stub_no_containers(session: StubbedSession, region: str = REGION) -> None:
    session.stub("ecs", region).add_response(
        "list_clusters", {"clusterArns": []}, {"maxResults": 100}
    )
    session.stub("eks", region).add_response("list_clusters", {"clusters": []}, {"maxResults": 100})


def stub_overview_describes(session: StubbedSession) -> None:
    session.stub("rds").add_response(
        "describe_db_instances",
        {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "grolla-lab-db",
                    "DBInstanceArn": DB_ARN,
                    "DBInstanceStatus": "stopped",
                    "Engine": "postgres",
                    "TagList": [{"Key": "service", "Value": "pedidos-api"}],
                },
                {
                    "DBInstanceIdentifier": "compartilhado-db",
                    "DBInstanceStatus": "available",
                    "Engine": "postgres",
                },
            ]
        },
        {},
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
        {},
    )
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
        {},
    )
    stub_no_containers(session)


async def test_overview_groups_by_service_and_kind_with_the_untagged_group(
    session: StubbedSession,
) -> None:
    tagging = session.stub("resourcegroupstaggingapi")
    first_page = {
        "ResourceTagMappingList": [
            {"ResourceARN": EC2_ARN, "Tags": [{"Key": "service", "Value": "pedidos-api"}]},
        ],
        "PaginationToken": "pagina-2",
    }
    second_page = {
        "ResourceTagMappingList": [
            {"ResourceARN": DB_ARN, "Tags": [{"Key": "service", "Value": "pedidos-api"}]},
            {"ResourceARN": SG_ARN, "Tags": [{"Key": "Name", "Value": "lab"}]},
        ],
        "PaginationToken": "",
    }
    tagging.add_response("get_resources", first_page, PAGE)
    tagging.add_response("get_resources", second_page, {**PAGE, "PaginationToken": "pagina-2"})
    stub_overview_describes(session)

    result = await inventory(session).overview()

    assert result.summary == (
        f"5 recurso(s) de nuvem em {REGION}, pela tag service. "
        f"pedidos-api: máquina EC2 i-0def690e1aea64498 (t3.micro) em {REGION}: running; "
        f"banco RDS grolla-lab-db (postgres) em {REGION}: stopped. "
        f"sem serviço: balanceador ELB pedidos-lb (application) em {REGION}: active; "
        f"recurso {SG_ARN} em {REGION}; "
        f"banco RDS compartilhado-db (postgres) em {REGION}: available"
    )
    assert result.url == (
        f"https://{REGION}.console.aws.amazon.com/resource-groups/tag-editor/"
        f"find-resources?region={REGION}"
    )
    session.assert_no_pending()


async def test_overview_of_an_empty_account(session: StubbedSession) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", NOTHING_TAGGED, PAGE)
    session.stub("rds").add_response("describe_db_instances", {"DBInstances": []}, {})
    session.stub("ec2").add_response("describe_instances", {"Reservations": []}, {})
    session.stub("elbv2").add_response("describe_load_balancers", {"LoadBalancers": []}, {})
    stub_no_containers(session)

    result = await inventory(session).overview()

    assert result.summary == f"nenhum recurso de nuvem encontrado em {REGION}"


async def test_overview_shows_at_most_the_limit(session: StubbedSession) -> None:
    many: dict[str, Any] = {
        "ResourceTagMappingList": [
            {"ResourceARN": f"arn:aws:sns:{REGION}:{ACCOUNT}:topico-{i:03}", "Tags": []}
            for i in range(205)
        ]
    }
    session.stub("resourcegroupstaggingapi").add_response("get_resources", many, PAGE)
    session.stub("rds").add_response("describe_db_instances", {"DBInstances": []}, {})
    session.stub("ec2").add_response("describe_instances", {"Reservations": []}, {})
    session.stub("elbv2").add_response("describe_load_balancers", {"LoadBalancers": []}, {})
    stub_no_containers(session)

    result = await inventory(session).overview()

    assert result.summary.startswith(f"205 recurso(s) de nuvem em {REGION} (mostrando 200)")
    assert result.summary.count("topico-") == 200


async def test_overview_with_the_cloud_refusing_is_unavailable() -> None:
    cloud = AwsCloudInventory([REGION], {}, session=UnreachableSession())

    with pytest.raises(ToolUnavailableError, match="EndpointConnectionError"):
        await cloud.overview()


# ---- Contêineres: ECS e EKS

ECS_CLUSTER_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/prod"
ECS_SERVICE_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/prod/pedidos-api"
EKS_CLUSTER_ARN = f"arn:aws:eks:{REGION}:{ACCOUNT}:cluster/lab"
EKS_NODEGROUP_ARN = (
    f"arn:aws:eks:{REGION}:{ACCOUNT}:nodegroup/lab/ng-a/8ec9b1f2-1d3b-4f6a-9a51-2f0c3d4e5f60"
)
TASK_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/prod/0a1b2c3d4e5f60718293a4b5c6d7e8f9"


def stub_eks_lab(session: StubbedSession, *, nodegroups: bool = True) -> None:
    eks = session.stub("eks")
    eks.add_response(
        "describe_cluster",
        {"cluster": {"name": "lab", "status": "ACTIVE", "version": "1.30"}},
        {"name": "lab"},
    )
    eks.add_response(
        "list_nodegroups",
        {"nodegroups": ["ng-a"] if nodegroups else []},
        {"clusterName": "lab", "maxResults": 100},
    )
    if nodegroups:
        eks.add_response(
            "describe_nodegroup",
            {
                "nodegroup": {
                    "nodegroupName": "ng-a",
                    "status": "DEGRADED",
                    "scalingConfig": {"desiredSize": 2},
                    "health": {
                        "issues": [
                            {"code": "AsgInstanceLaunchFailures", "message": "Could not launch"}
                        ]
                    },
                }
            },
            {"clusterName": "lab", "nodegroupName": "ng-a"},
        )


async def test_resources_show_the_stuck_ecs_deployment_and_the_eks_nodegroups(
    session: StubbedSession,
) -> None:
    tagged = {
        "ResourceTagMappingList": [
            {"ResourceARN": arn, "Tags": [{"Key": "service", "Value": "pedidos-api"}]}
            for arn in (ECS_SERVICE_ARN, ECS_CLUSTER_ARN, EKS_CLUSTER_ARN, EKS_NODEGROUP_ARN)
        ]
    }
    session.stub("resourcegroupstaggingapi").add_response("get_resources", tagged, TAG_QUERY)
    ecs = session.stub("ecs")
    ecs.add_response(
        "describe_services",
        {
            "services": [
                {
                    "serviceName": "pedidos-api",
                    "status": "ACTIVE",
                    "desiredCount": 4,
                    "runningCount": 1,
                    "pendingCount": 3,
                    "deployments": [
                        {"status": "PRIMARY", "rolloutState": "IN_PROGRESS"},
                        {"status": "ACTIVE", "rolloutState": "COMPLETED"},
                    ],
                }
            ]
        },
        {"cluster": "prod", "services": ["pedidos-api"]},
    )
    ecs.add_response(
        "list_tasks",
        {"taskArns": [TASK_ARN]},
        {
            "cluster": "prod",
            "serviceName": "pedidos-api",
            "desiredStatus": "STOPPED",
            "maxResults": 100,
        },
    )
    ecs.add_response(
        "describe_tasks",
        {
            "tasks": [
                {
                    "taskArn": TASK_ARN,
                    "stoppedAt": STOPPED_AT,
                    "stoppedReason": "Essential container in task exited",
                }
            ]
        },
        {"cluster": "prod", "tasks": [TASK_ARN]},
    )
    ecs.add_response(
        "describe_clusters",
        {
            "clusters": [
                {
                    "clusterName": "prod",
                    "status": "ACTIVE",
                    "activeServicesCount": 1,
                    "runningTasksCount": 1,
                    "pendingTasksCount": 3,
                }
            ]
        },
        {"clusters": ["prod"]},
    )
    stub_eks_lab(session)

    result = await inventory(session).resources("pedidos-api")

    console = f"https://{REGION}.console.aws.amazon.com"
    assert result.summary.startswith("3 recurso(s) de nuvem de pedidos-api: ")  # 1 cluster EKS
    assert (
        f"serviço ECS prod/pedidos-api em {REGION}: 4 desejadas, 1 rodando, deployment em "
        "andamento, 3 pendentes, tarefas paradas recentemente: 21:42 Essential container in "
        f"task exited [console: {console}/ecs/v2/clusters/prod/services/pedidos-api/health"
        f"?region={REGION}]"
    ) in result.summary
    assert (
        f"cluster ECS prod em {REGION}: ACTIVE, 1 serviço(s), 1 tarefa(s) rodando, 3 pendente(s)"
    ) in result.summary
    assert (
        f"cluster EKS lab (Kubernetes 1.30) em {REGION}: ACTIVE, nodegroups: ng-a DEGRADED "
        "(2 nós desejados) com problema: AsgInstanceLaunchFailures: Could not launch "
        f"[console: {console}/eks/home?region={REGION}#/clusters/lab]"
    ) in result.summary
    session.assert_no_pending()


async def test_overview_lists_every_ecs_and_eks_cluster_even_untagged(
    session: StubbedSession,
) -> None:
    tagged = {
        "ResourceTagMappingList": [
            {"ResourceARN": ECS_SERVICE_ARN, "Tags": [{"Key": "service", "Value": "pedidos-api"}]}
        ]
    }
    session.stub("resourcegroupstaggingapi").add_response("get_resources", tagged, PAGE)
    session.stub("rds").add_response("describe_db_instances", {"DBInstances": []}, {})
    session.stub("ec2").add_response("describe_instances", {"Reservations": []}, {})
    session.stub("elbv2").add_response("describe_load_balancers", {"LoadBalancers": []}, {})
    ecs = session.stub("ecs")
    ecs.add_response("list_clusters", {"clusterArns": [ECS_CLUSTER_ARN]}, {"maxResults": 100})
    ecs.add_response(
        "list_services",
        {"serviceArns": [ECS_SERVICE_ARN]},
        {"cluster": "prod", "maxResults": 100},
    )
    ecs.add_response(
        "describe_clusters",
        {"clusters": [{"clusterName": "prod", "status": "ACTIVE"}]},
        {"clusters": ["prod"]},
    )
    ecs.add_response(
        "describe_services",
        {
            "services": [
                {
                    "serviceName": "pedidos-api",
                    "status": "ACTIVE",
                    "desiredCount": 2,
                    "runningCount": 0,
                    "deployments": [
                        {
                            "status": "PRIMARY",
                            "rolloutState": "FAILED",
                            "rolloutStateReason": "ECS deployment circuit breaker: tasks failed",
                        }
                    ],
                }
            ]
        },
        {"cluster": "prod", "services": ["pedidos-api"]},
    )
    session.stub("eks").add_response("list_clusters", {"clusters": ["lab"]}, {"maxResults": 100})
    stub_eks_lab(session, nodegroups=False)

    result = await inventory(session).overview()

    assert result.summary == (
        f"3 recurso(s) de nuvem em {REGION}, pela tag service. "
        f"pedidos-api: serviço ECS prod/pedidos-api em {REGION}: 2 desejadas, 0 rodando, "
        "deployment falhou: ECS deployment circuit breaker: tasks failed. "
        f"sem serviço: cluster ECS prod em {REGION}: ACTIVE, 0 serviço(s), 0 tarefa(s) rodando; "
        f"cluster EKS lab (Kubernetes 1.30) em {REGION}: ACTIVE"
    )
    session.assert_no_pending()


# ---- Eventos do AWS Health

HEALTH_FILTER = {"regions": [REGION], "eventStatusCodes": ["open", "upcoming"]}
EC2_EVENT = f"arn:aws:health:{REGION}::event/EC2/AWS_EC2_OPERATIONAL_ISSUE/AWS_EC2_ISSUE_1"
RDS_EVENT = f"arn:aws:health:{REGION}::event/RDS/AWS_RDS_MAINTENANCE_SCHEDULED/AWS_RDS_1"
OTHER_EVENT = f"arn:aws:health:{REGION}::event/RDS/AWS_RDS_MAINTENANCE_SCHEDULED/AWS_RDS_2"
MAINTENANCE = "AWS_RDS_MAINTENANCE_SCHEDULED"


def health_event(arn: str, kind: str, status: str, scope: str, start: datetime) -> dict[str, Any]:
    return {
        "arn": arn,
        "service": kind.split("_")[1],
        "eventTypeCode": kind,
        "region": REGION,
        "statusCode": status,
        "eventScopeCode": scope,
        "startTime": start,
    }


async def test_health_shows_regional_events_and_the_ones_citing_the_service(
    session: StubbedSession,
) -> None:
    later_on = STOPPED_AT + timedelta(days=2)
    health = session.stub("health")
    health.add_response(
        "describe_events",
        {
            "events": [
                health_event(EC2_EVENT, "AWS_EC2_OPERATIONAL_ISSUE", "open", "PUBLIC", STOPPED_AT),
                health_event(RDS_EVENT, MAINTENANCE, "upcoming", "ACCOUNT_SPECIFIC", later_on),
                health_event(OTHER_EVENT, MAINTENANCE, "upcoming", "ACCOUNT_SPECIFIC", later_on),
            ]
        },
        {"filter": HEALTH_FILTER, "maxResults": 100},
    )
    health.add_response(
        "describe_affected_entities",
        {
            "entities": [
                {"eventArn": RDS_EVENT, "entityValue": "grolla-lab-db"},
                {"eventArn": OTHER_EVENT, "entityValue": "outro-db"},
            ]
        },
        {"filter": {"eventArns": [RDS_EVENT, OTHER_EVENT]}},
    )
    session.stub("resourcegroupstaggingapi").add_response("get_resources", TAGGED, TAG_QUERY)

    result = await inventory(session).health("pedidos-api")

    assert result.summary == (
        f"2 evento(s) aberto(s) da nuvem em {REGION} (UTC): "
        f"RDS {MAINTENANCE} em {REGION}: upcoming, início 27/10 21:42 UTC, afeta grolla-lab-db; "
        f"EC2 AWS_EC2_OPERATIONAL_ISSUE em {REGION}: open, início 25/10 21:42 UTC, "
        "afeta a região"
    )
    assert result.url == "https://health.aws.amazon.com/health/home#/account/dashboard/open-issues"
    session.assert_no_pending()


async def test_account_event_without_entities_affects_the_account(
    session: StubbedSession,
) -> None:
    health = session.stub("health")
    health.add_response(
        "describe_events",
        {"events": [health_event(RDS_EVENT, MAINTENANCE, "open", "ACCOUNT_SPECIFIC", STOPPED_AT)]},
    )
    health.add_response("describe_affected_entities", {"entities": []})

    result = await inventory(session).health("pedidos-api")

    assert result.summary.endswith(": open, início 25/10 21:42 UTC, afeta a conta")


async def test_health_without_events_does_not_look_for_resources(session: StubbedSession) -> None:
    session.stub("health").add_response("describe_events", {"events": []})

    result = await inventory(session).health("pedidos-api")

    assert result.summary == f"nenhum evento aberto da nuvem em {REGION} que afete pedidos-api"


async def test_health_without_the_support_plan_is_an_answer(session: StubbedSession) -> None:
    session.stub("health").add_client_error(
        "describe_events", service_error_code="SubscriptionRequiredException"
    )

    result = await inventory(session).health("pedidos-api")

    assert result == ToolResult(HEALTH_UNAVAILABLE, None)


async def test_health_other_refusal_is_unavailable(session: StubbedSession) -> None:
    session.stub("health").add_client_error(
        "describe_events", service_error_code="AccessDeniedException"
    )

    with pytest.raises(ToolUnavailableError, match="AccessDeniedException"):
        await inventory(session).health("pedidos-api")


# ---- Cotas

VCPU_METRIC = {
    "MetricNamespace": "AWS/Usage",
    "MetricName": "ResourceCount",
    "MetricDimensions": {
        "Service": "EC2",
        "Type": "Resource",
        "Resource": "vCPU",
        "Class": "Standard/OnDemand",
    },
    "MetricStatisticRecommendation": "Maximum",
}


def stub_quotas(session: StubbedSession, datapoints: list[dict[str, Any]]) -> None:
    session.stub("rds").add_response(
        "describe_account_attributes",
        {
            "AccountQuotas": [
                {"AccountQuotaName": "DBInstances", "Used": 18, "Max": 20},
                {"AccountQuotaName": "AllocatedStorage", "Used": 100, "Max": 100000},
                {"AccountQuotaName": "ManualSnapshots", "Used": 1, "Max": 100},
            ]
        },
        {},
    )
    session.stub("service-quotas").add_response(
        "get_service_quota",
        {"Quota": {"QuotaCode": "L-1216C47A", "Value": 32.0, "UsageMetric": VCPU_METRIC}},
        {"ServiceCode": "ec2", "QuotaCode": "L-1216C47A"},
    )
    session.stub("cloudwatch").add_response(
        "get_metric_statistics",
        {"Datapoints": datapoints},
        {
            "Namespace": "AWS/Usage",
            "MetricName": "ResourceCount",
            "Dimensions": [
                {"Name": "Service", "Value": "EC2"},
                {"Name": "Type", "Value": "Resource"},
                {"Name": "Resource", "Value": "vCPU"},
                {"Name": "Class", "Value": "Standard/OnDemand"},
            ],
            "StartTime": ANY,
            "EndTime": ANY,
            "Period": 300,
            "Statistics": ["Maximum"],
        },
    )
    elb = session.stub("elbv2")
    elb.add_response(
        "describe_account_limits",
        {
            "Limits": [
                {"Name": "application-load-balancers", "Max": "50"},
                {"Name": "target-groups", "Max": "3000"},
            ]
        },
        {},
    )
    elb.add_response(
        "describe_load_balancers",
        {
            "LoadBalancers": [
                {"LoadBalancerName": "pedidos-lb", "Type": "application"},
                {"LoadBalancerName": "interno-nlb", "Type": "network"},
            ]
        },
        {"PageSize": 400},
    )


async def test_quotas_closest_to_the_limit_first_and_highlighted(session: StubbedSession) -> None:
    stub_quotas(
        session,
        [
            {"Timestamp": STOPPED_AT, "Maximum": 8.0},
            {"Timestamp": STOPPED_AT - timedelta(minutes=5), "Maximum": 12.0},
        ],
    )

    result = await inventory(session).quotas("pedidos-api")

    assert result.summary == (
        "1 acima de 80% do limite. 4 cota(s) de nuvem, da mais perto do limite: "
        f"instâncias de banco: 18 de 20 (90%) em {REGION}; "
        f"vCPUs de máquinas sob demanda: 8 de 32 (25%) em {REGION}; "
        f"balanceadores de aplicação: 1 de 50 (2%) em {REGION}; "
        f"armazenamento de banco (GiB): 100 de 100000 (0%) em {REGION}"
    )
    assert (
        result.url == f"https://{REGION}.console.aws.amazon.com/servicequotas/home?region={REGION}"
    )
    session.assert_no_pending()


async def test_quota_without_usage_data_goes_last(session: StubbedSession) -> None:
    stub_quotas(session, [])

    result = await inventory(session).quotas("pedidos-api")

    assert result.summary.endswith(
        f"; vCPUs de máquinas sob demanda: uso sem dado, limite 32 em {REGION}"
    )


# ---- Várias contas

DADOS = CloudAccount("dados", "arn:aws:iam::111111111111:role/grolla-leitura", (REGION,))
APPS = CloudAccount("apps", "arn:aws:iam::222222222222:role/grolla-leitura", ("sa-east-1",))
DADOS_DB_ARN = f"arn:aws:rds:{REGION}:111111111111:db:grolla-lab-db"
APPS_EC2_ARN = "arn:aws:ec2:sa-east-1:222222222222:instance/i-0def690e1aea64498"


def stub_assume(
    session: StubbedSession, account: CloudAccount, key_id: str, expires: datetime
) -> None:
    session.stub("sts", account.regions[0]).add_response(
        "assume_role",
        {
            "Credentials": {
                "AccessKeyId": key_id,
                "SecretAccessKey": "segredo-falso",
                "SessionToken": "token-falso",
                "Expiration": expires,
            }
        },
        {"RoleArn": account.role_arn, "RoleSessionName": "grolla-somente-leitura"},
    )


def stub_two_accounts(session: StubbedSession) -> None:
    session.stub("resourcegroupstaggingapi").add_response("get_resources", NOTHING_TAGGED)
    session.stub("resourcegroupstaggingapi", REGION, "ASIADADOS0000000").add_response(
        "get_resources",
        {"ResourceTagMappingList": [{"ResourceARN": DADOS_DB_ARN}]},
        TAG_QUERY,
    )
    session.stub("rds", REGION, "ASIADADOS0000000").add_response(
        "describe_db_instances",
        {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "grolla-lab-db",
                    "DBInstanceStatus": "stopped",
                    "Engine": "postgres",
                }
            ]
        },
    )
    session.stub("resourcegroupstaggingapi", "sa-east-1", "ASIAAPPS00000000").add_response(
        "get_resources",
        {"ResourceTagMappingList": [{"ResourceARN": APPS_EC2_ARN}]},
        TAG_QUERY,
    )
    session.stub("ec2", "sa-east-1", "ASIAAPPS00000000").add_response(
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
    )


def later(minutes: int) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


async def test_resources_in_other_accounts_say_which_account(session: StubbedSession) -> None:
    stub_assume(session, DADOS, "ASIADADOS0000000", later(60))
    stub_assume(session, APPS, "ASIAAPPS00000000", later(60))
    stub_two_accounts(session)
    cloud = AwsCloudInventory([REGION], {}, session=session, accounts=[DADOS, APPS])

    result = await cloud.resources("pedidos-api")

    assert result.summary.startswith("2 recurso(s) de nuvem de pedidos-api: ")
    assert f"banco RDS grolla-lab-db (postgres) em {REGION} na conta dados: stopped" in (
        result.summary
    )
    assert "máquina EC2 i-0def690e1aea64498 (t3.micro) em sa-east-1 na conta apps: running" in (
        result.summary
    )
    session.assert_no_pending()

    stub_two_accounts(session)  # a sessão assumida fica em cache: nenhum assume_role novo
    await cloud.resources("pedidos-api")
    session.assert_no_pending()


async def test_credential_near_expiry_is_assumed_again(session: StubbedSession) -> None:
    stub_assume(session, DADOS, "ASIADADOS0000000", later(2))  # dentro da margem de renovação
    stub_assume(session, DADOS, "ASIADADOS0000000", later(60))
    for _ in range(2):
        session.stub("resourcegroupstaggingapi").add_response("get_resources", NOTHING_TAGGED)
        session.stub("resourcegroupstaggingapi", REGION, "ASIADADOS0000000").add_response(
            "get_resources", NOTHING_TAGGED
        )
    cloud = AwsCloudInventory([REGION], {}, session=session, accounts=[DADOS])

    await cloud.resources("pedidos-api")
    await cloud.resources("pedidos-api")

    session.assert_no_pending()


async def test_failing_account_is_a_line_and_does_not_hide_the_others(
    session: StubbedSession,
) -> None:
    stub_assume(session, DADOS, "ASIADADOS0000000", later(60))
    session.stub("sts", "sa-east-1").add_client_error(
        "assume_role", service_error_code="AccessDenied", http_status_code=403
    )
    session.stub("resourcegroupstaggingapi").add_response("get_resources", NOTHING_TAGGED)
    session.stub("resourcegroupstaggingapi", REGION, "ASIADADOS0000000").add_response(
        "get_resources", {"ResourceTagMappingList": [{"ResourceARN": DADOS_DB_ARN}]}
    )
    session.stub("rds", REGION, "ASIADADOS0000000").add_response(
        "describe_db_instances",
        {
            "DBInstances": [
                {"DBInstanceIdentifier": "grolla-lab-db", "DBInstanceStatus": "available"}
            ]
        },
    )
    cloud = AwsCloudInventory([REGION], {}, session=session, accounts=[DADOS, APPS])

    result = await cloud.resources("pedidos-api")

    assert f"banco RDS grolla-lab-db em {REGION} na conta dados: available" in result.summary
    assert result.summary.endswith(". conta apps: AWS recusou a consulta: AccessDenied")


async def test_every_account_failing_is_unavailable(session: StubbedSession) -> None:
    session.stub("resourcegroupstaggingapi").add_client_error(
        "get_resources", service_error_code="AccessDeniedException"
    )
    session.stub("sts").add_client_error("assume_role", service_error_code="AccessDenied")
    cloud = AwsCloudInventory([REGION], {}, session=session, accounts=[DADOS])

    with pytest.raises(
        ToolUnavailableError,
        match=(
            "conta principal: AWS recusou a consulta: AccessDeniedException; "
            "conta dados: AWS recusou a consulta: AccessDenied"
        ),
    ):
        await cloud.alarms("pedidos-api")


async def test_mapped_arn_goes_to_the_account_of_its_account_id(session: StubbedSession) -> None:
    stub_assume(session, DADOS, "ASIADADOS0000000", later(60))
    session.stub("resourcegroupstaggingapi").add_response("get_resources", NOTHING_TAGGED)
    session.stub("resourcegroupstaggingapi", REGION, "ASIADADOS0000000").add_response(
        "get_resources", NOTHING_TAGGED
    )
    session.stub("cloudtrail", REGION, "ASIADADOS0000000").add_response(
        "lookup_events", {"Events": [event("StopDBInstance", STOPPED_AT, "root")]}
    )
    mapping = {"pedidos-api": [DADOS_DB_ARN]}
    cloud = AwsCloudInventory([REGION], mapping, session=session, accounts=[DADOS])

    result = await cloud.changes("pedidos-api", SINCE)

    assert "21:42 StopDBInstance em grolla-lab-db na conta dados por root" in result.summary
    session.assert_no_pending()


async def test_account_without_the_health_plan_is_a_line(session: StubbedSession) -> None:
    stub_assume(session, DADOS, "ASIADADOS0000000", later(60))
    session.stub("health").add_response("describe_events", {"events": []})
    session.stub("health", REGION, "ASIADADOS0000000").add_client_error(
        "describe_events", service_error_code="SubscriptionRequiredException"
    )
    cloud = AwsCloudInventory([REGION], {}, session=session, accounts=[DADOS])

    result = await cloud.health("pedidos-api")

    assert result.summary == (
        f"nenhum evento aberto da nuvem em {REGION} que afete pedidos-api. "
        f"conta dados: {HEALTH_UNAVAILABLE}"
    )
