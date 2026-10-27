## Context

O `Toolbox` liga as ferramentas neutras aos ports: `Telemetry`, `ChangeHistory`, `CloudInventory` e
a memória. O `CloudInventory` (AWS) vê o EKS só como cluster e nodegroups, não o que roda dentro.
Mudança feita com `kubectl` não aparece no pipeline (`recent_deploys`) nem no CloudTrail
(`cloud_changes`). O Kubernetes guarda o que a investigação precisa: as revisões do workload
(ReplicaSets com o template de cada revisão), o `managedFields` de cada objeto (quando cada campo
mudou e por qual gerenciador), os eventos e o status dos pods.

## Goals / Non-Goals

**Goals:** os da proposta. **Non-Goals:** os da proposta.

## Contratos

```python
# grolla.application.investigation
NO_CLUSTER = "consulta ao cluster não configurada nesta instância"
NO_WORKLOADS = "nenhum workload encontrado no cluster para o serviço"


class ClusterInventory(Protocol):
    """Estado e mudanças do serviço no orquestrador, só leitura. Os dois levantam
    ToolUnavailableError quando o cluster falha (credencial, permissão, rede). Serviço sem
    workload não é erro: o resumo diz NO_WORKLOADS."""

    async def state(self, service: str, since: datetime) -> ToolResult: ...
    async def changes(self, service: str, since: datetime) -> ToolResult: ...


class NoCluster:  # instância sem a seção `cluster`: as duas respondem NO_CLUSTER
    ...


# Toolbox(..., cloud, *, cluster: ClusterInventory | None = None, memory=..., ...)
# Ferramentas neutras em TOOL_SPECS, só com `service`: `cluster_state` e `cluster_changes`
# (desde = agora - lookback). Os casos de uso que montam Toolbox recebem `cluster`.


# grolla.testing.investigation
class FakeClusterInventory:  # mapas serviço → resumo, `failing`, `calls`, como FakeCloudInventory
    ...


# grolla.adapters.kubernetes
@dataclass(frozen=True)
class ClusterAccess:
    mode: Literal["kubeconfig", "in_cluster", "eks"]
    context: str | None = None  # kubeconfig
    name: str | None = None  # eks
    region: str | None = None  # eks


class KubernetesClusterInventory:
    def __init__(
        self,
        access: ClusterAccess,
        namespaces: Sequence[str],
        service_labels: Sequence[str] = ("app.kubernetes.io/name", "app"),
        workload_map: Mapping[str, Sequence[str]] | None = None,
        api_client: Any = None,
    ) -> None: ...  # testes injetam um ApiClient


# grolla.bootstrap.team_config
class ClusterConfig(_Section):
    access: Literal["kubeconfig", "in_cluster", "eks"] = "kubeconfig"
    context: str | None = None
    name: str | None = None
    region: str | None = None  # `eks` exige `name` e `region`
    namespaces: list[str]  # ao menos um
    service_label: str | None = None  # None: app.kubernetes.io/name, depois app
    workload_map: dict[str, list[str]] = {}  # serviço → ["namespace/nome"]


# TeamConfig.cluster: ClusterConfig | None; WorkerContainer.cluster: ClusterInventory
```

Sem schema novo nem evento novo: as chamadas viram avaliações, como as outras ferramentas.

## Decisions

**1. Port próprio `ClusterInventory`, não métodos novos no `CloudInventory`.** O cluster não é da
nuvem: o mesmo adapter serve EKS, AKS, GKE, k3s ou kind, e a nuvem segue com a AWS. A alternativa
mais simples (estender o `CloudInventory`) prenderia o Kubernetes ao adapter da AWS.

**2. Duas ferramentas: estado e mudanças.** O estado (pods, HPA, eventos) responde "o que está
quebrado agora"; as mudanças respondem "o que mudou antes de quebrar". A alternativa de uma
ferramenta só misturaria as duas perguntas numa resposta longa; a de uma por recurso (pods,
eventos, HPA, ConfigMaps) multiplicaria chamadas do modelo.

**3. Mudanças pelo `managedFields` e pelas revisões do ReplicaSet, não pelo audit log.** Os dois
existem em qualquer cluster e respondem quando e por qual ferramenta. O audit log diria quem, mas
depende da configuração do cluster (no EKS, ligar o log no CloudWatch) e não existe num kind. Fica
para depois.

**4. Cliente oficial `kubernetes`, síncrono em `asyncio.to_thread`.** Como o boto3 no adapter da
AWS. Ele já resolve kubeconfig, in-cluster, certificados e plugins `exec`. A alternativa de chamar
a API REST com httpx exigiria reimplementar a autenticação.

**5. EKS com o token gerado no adapter** (URL pré-assinada do `sts:GetCallerIdentity` com o header
`x-k8s-aws-id`, renovada antes de expirar). O endpoint e o CA vêm do `eks:DescribeCluster`. A
alternativa (kubeconfig com `exec: aws eks get-token`) exige o AWS CLI na imagem do worker.

**6. Secrets fora.** A permissão de listar Secret entrega os valores. As referências a Secret nas
variáveis aparecem só pelo nome, e uma troca de Secret aparece como reinício ou revisão.

**7. Testes:**
- **Adapter:** a suíte de contrato roda contra um k3s real (testcontainers), com um Deployment, um
  ConfigMap e uma mudança feita como o `kubectl` faria (field manager `kubectl-edit`).
- **e2e:** o `grolla-fakes` responde as listagens que o adapter usa. A alternativa de um kind no
  compose exigiria docker dentro do docker e deixaria o e2e mais lento.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| `grolla-dominio` | port, `NoCluster`, ferramentas no Toolbox, `cluster` nos casos de uso e no worker, prompts, `FakeClusterInventory`, suíte de contrato |
| `grolla-integracoes` | `grolla.adapters.kubernetes` (kubeconfig, in-cluster, EKS), testes com k3s, a API fake no `grolla-fakes` e o LLM fake chamando as ferramentas |
| `grolla-e2e` | cenário: alerta sem deploy no pipeline, ConfigMap alterado por `kubectl-edit` no fake e causa raiz apontando a mudança |

O agente pai faz a config, o bootstrap, a dependência, a RBAC no `deploy/`, os rótulos no war
room e, com o custo aprovado, o teste no EKS.

## Risks / Trade-offs

- [Resposta grande em namespace com muitos pods] → resumo por workload, até 10 pods, com os não
  prontos primeiro.
- [`managedFields` desligado ou limpo por alguma ferramenta] → as revisões e os eventos continuam;
  a resposta diz que não há registro de campo.
- [Token do EKS expira em 15 min] → renovado antes, por cliente.
