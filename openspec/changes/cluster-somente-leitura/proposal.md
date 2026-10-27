## Why

O agente investiga pela telemetria, pelos deploys do pipeline e pela nuvem, mas não enxerga dentro
do cluster. Uma mudança feita direto com `kubectl` (ConfigMap editado, `set env`, `set resources`,
`scale`, HPA ou NetworkPolicy alterados) não passa pelo pipeline nem pelo CloudTrail. O agente vê
o sintoma e, no máximo, desconfia de uma "mudança fora do pipeline". Também não vê o estado dos
pods (OOMKilled, CrashLoopBackOff, probe falhando, HPA no teto), que é onde muita causa raiz está.
É o passo de infraestrutura da investigação (`docs/initial-spec.md`, investigação e hipóteses)
levado ao orquestrador, e o pré-requisito do teste realista com várias aplicações num EKS.

## What Changes

- **Estado do serviço no cluster** (`cluster_state`): para cada workload do serviço, réplicas
  desejadas e prontas, rollout em andamento, cada pod com fase, reinícios, último término (OOMKilled,
  exit code), espera (CrashLoopBackOff, ImagePullBackOff), probes falhando, limites de CPU e
  memória. Também o HPA (atual, desejado, máximo, com aviso quando está no teto) e os eventos
  recentes do serviço (BackOff, Unhealthy, OOMKilling, FailedScheduling, FailedCreate).
- **Mudanças recentes no cluster** (`cluster_changes`): desde o início da janela, quando mudou e por
  qual ferramenta (por exemplo `kubectl-edit`, `kubectl-set`, `helm`, `argocd`):
  - as revisões do workload, com o que mudou no template (imagem, variáveis de ambiente, recursos)
    e as mudanças de réplicas;
  - os ConfigMaps que os pods do serviço usam, com as chaves alteradas e o valor atual delas;
  - o HPA e as NetworkPolicies do namespace.
- **Somente leitura**: o agente só lista e lê. A RBAC fica documentada em `deploy/` (um
  `ClusterRole` só com `get`/`list`/`watch`).
- **Configuração** `cluster`: kubeconfig (contexto) ou dentro do cluster, ou EKS com o token gerado
  pela credencial AWS, mais os namespaces, o rótulo que liga um workload ao serviço e um mapa para as
  exceções.
- **Prompts** da análise, da validação, do chat e do console passam a citar as duas ferramentas.

## Capabilities

### New Capabilities
- `cluster-investigation`: estado, eventos e mudanças do serviço no cluster, somente leitura.

### Modified Capabilities
- `team-config`: seção `cluster`.

## Non-goals

- **Secrets.** Ficam de fora: a permissão de listar Secret dá acesso aos valores. Uma mudança de
  Secret aparece como reinício ou revisão do workload.
- **Quem mudou.** O `managedFields` diz quando e por qual ferramenta, não a pessoa. O audit log do
  cluster (no EKS, pelo CloudWatch Logs) fica para depois.
- **Vários clusters.** Um cluster por instância.
- **Uso real de CPU e memória** (metrics-server). A telemetria já traz.
- **Estudo do ambiente no cluster.** O estudo da memória viva continua como está.
- **Escrever no cluster**, nem por sugestão executável.

## Impact

- `grolla-core`:
  - port novo `ClusterInventory` (`state`, `changes`) com `NoCluster` para a instância sem a seção;
  - ferramentas neutras `cluster_state` e `cluster_changes` no Toolbox e nos prompts;
  - adapter `grolla.adapters.kubernetes` com o cliente oficial do Kubernetes;
  - config `cluster` e bootstrap.
- Dependência nova: `kubernetes` (cliente oficial), no core.
- `e2e/fakes`: uma API do Kubernetes fake, só com as listagens que o adapter usa.
- `deploy/`: a RBAC somente leitura e o README.
- Testes: contrato do adapter contra um cluster k3s real (testcontainers); e2e com o fake; depois,
  teste realista num EKS com várias aplicações e uma falha injetada por `kubectl` (com custo
  aprovado antes).
