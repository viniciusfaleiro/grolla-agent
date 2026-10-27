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
class ClusterTarget:
    name: str
    mode: Literal["kubeconfig", "in_cluster", "eks"]
    namespaces: tuple[str, ...]
    context: str | None = None  # kubeconfig
    eks_name: str | None = None  # eks
    region: str | None = None  # eks
    role_arn: str | None = None  # eks em outra conta
    service_labels: tuple[str, ...] = ("app.kubernetes.io/name", "app")
    workload_map: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    show_values: bool = True  # False: só nomes de chaves e variáveis (clusters PCI)


class KubernetesClusterInventory:
    def __init__(
        self,
        clusters: Sequence[ClusterTarget],  # só os ligados; o bootstrap filtra `enabled`
        api_clients: Mapping[str, Any] | None = None,  # testes injetam um ApiClient por cluster
    ) -> None: ...


# grolla.bootstrap.team_config
class ClusterConfig(_Section):
    name: str
    enabled: bool = True
    access: Literal["kubeconfig", "in_cluster", "eks"] = "kubeconfig"
    context: str | None = None
    eks_name: str | None = None
    region: str | None = None  # `eks` exige `eks_name` e `region`
    role_arn: str | None = None  # só no `eks`; ARN de role do IAM
    namespaces: list[str]  # ao menos um
    service_label: str | None = None  # None: app.kubernetes.io/name, depois app
    workload_map: dict[str, list[str]] = {}  # serviço → ["namespace/nome"]
    show_values: bool = True


# TeamConfig.clusters: list[ClusterConfig] (nomes únicos)
# WorkerContainer.cluster: ClusterInventory (NoCluster sem clusters ligados)
```

Sem schema novo nem evento novo: as chamadas viram avaliações, como as outras ferramentas.

### Ajustes combinados durante a implementação

- `NO_CLUSTER_CHANGES` em `grolla.application.investigation`, usada pelo fake e pelo adapter.
- `changes` de serviço sem workload responde `NO_WORKLOADS` no adapter (o fake responde
  `NO_CLUSTER_CHANGES`; a suíte só exige resposta compacta).
- O adapter lê o `KUBECONFIG` na hora de conectar (o cliente fixa o caminho no import).
- Simplificações: seletor dos pods só por `matchLabels`; o gerenciador de uma revisão é a entrada
  do `managedFields` do template mais próxima do horário do ReplicaSet.
- O teste realista no EKS (várias aplicações e uma falha injetada por `kubectl`) saiu desta change
  e roda numa rodada própria, depois do custo aprovado pelo usuário.
- Os prompts dizem "edição manual" nos exemplos: `kubectl` é palavra proibida no núcleo.
- e2e: kubeconfig com os dois clusters fake (`nao-pci`, `pci`) montado no worker; rota
  `/_control/k8s` roteiriza o ConfigMap editado.

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
`x-k8s-aws-id`, renovada antes de expirar), depois de assumir a `role_arn` do cluster quando há. O endpoint e o CA vêm do `eks:DescribeCluster`. A
alternativa (kubeconfig com `exec: aws eks get-token`) exige o AWS CLI na imagem do worker.

**6. Secrets fora.** A permissão de listar Secret entrega os valores. As referências a Secret nas
variáveis aparecem só pelo nome, e uma troca de Secret aparece como reinício ou revisão.

**7. Vários clusters num adapter só, consultados em paralelo e rotulados.** Cada cluster tem o
próprio acesso, inclusive a role da conta dele no EKS, o que separa as contas PCI e não PCI.
A falha de um vira uma linha e não esconde os outros, como nas várias contas da nuvem. A
alternativa de um mapa serviço → cluster foi descartada: o rótulo do serviço já diz onde ele roda,
e um mapa a mais desatualiza.

**8. Valores ocultos no adapter (`show_values`).** O valor de ConfigMap e de variável de ambiente
nem sai do adapter num cluster com `show_values: false`: o resto do sistema, a memória e o LLM
nunca o veem. A alternativa de filtrar no prompt confiaria no modelo para não repetir o valor.

**9. Ligar e desligar pelo YAML** (`enabled`), valendo no próximo restart. O bootstrap passa ao
adapter só os clusters ligados. Um interruptor na web foi descartado por ora (pedido do usuário):
exigiria estado no banco e auditoria de quem mudou.

**10. Testes:**
- **Adapter:** a suíte de contrato roda contra um k3s real (testcontainers), com um Deployment, um
  ConfigMap e uma mudança feita como o `kubectl` faria (field manager `kubectl-edit`).
- **e2e:** o `grolla-fakes` responde as listagens que o adapter usa. A alternativa de um kind no
  compose exigiria docker dentro do docker e deixaria o e2e mais lento.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| `grolla-dominio` | port, `NoCluster`, ferramentas no Toolbox, `cluster` nos casos de uso e no worker, prompts, `FakeClusterInventory`, suíte de contrato |
| `grolla-integracoes` | `grolla.adapters.kubernetes` (vários clusters, kubeconfig, in-cluster, EKS com role, `show_values`), testes com k3s, a API fake no `grolla-fakes` e o LLM fake chamando as ferramentas |
| `grolla-e2e` | cenário: alerta sem deploy no pipeline, ConfigMap alterado por `kubectl-edit` no fake e causa raiz apontando a mudança |

O agente pai faz a config, o bootstrap, a dependência, a RBAC no `deploy/`, os rótulos no war
room e, com o custo aprovado, o teste no EKS.

## Risks / Trade-offs

- [Resposta grande em namespace com muitos pods] → resumo por workload, até 10 pods, com os não
  prontos primeiro.
- [`managedFields` desligado ou limpo por alguma ferramenta] → as revisões e os eventos continuam;
  a resposta diz que não há registro de campo.
- [Token do EKS expira em 15 min] → renovado antes, por cluster.
- [Mais clusters, mais latência] → consultas em paralelo, cada uma com o timeout da ferramenta.
