> Change multiagente: domínio, integrações e e2e em paralelo a partir do commit de contrato. Sem
> persistência e sem web. Os jobs do worker ficam com o `grolla-dominio`.

## 0. Contratos (agente pai)

- [x] 0.1 `ClusterInventory`, `NoCluster`, `NO_CLUSTER`, `NO_WORKLOADS`; stubs de `FakeClusterInventory` e do `KubernetesClusterInventory`; `ClusterConfig`/`clusters` e o bootstrap (só os ligados); dependência `kubernetes`.
- [x] 0.2 `make check` verde e commit de contrato.

## 1. grolla-dominio

- [ ] 1.1 `FakeClusterInventory` e a suíte de contrato (commit cedo).
- [ ] 1.2 Ferramentas `cluster_state` e `cluster_changes` no Toolbox; `cluster` nos casos de uso (análise, validação, chat, console, acompanhamento) e no worker; prompts; testes; `make check` verde.

## 2. grolla-integracoes

- [ ] 2.1 `KubernetesClusterInventory`: estado (workloads, pods, HPA, eventos) e mudanças (revisões com o diff do template, réplicas, ConfigMaps usados, HPA, NetworkPolicies), com o gerenciador do `managedFields`.
- [ ] 2.2 Vários clusters em paralelo, rotulados, com falha isolada; acesso por kubeconfig, in-cluster e EKS (role da conta e token pré-assinado); `show_values: false` sem nenhum valor de ConfigMap ou env.
- [ ] 2.3 Suíte de contrato contra k3s (testcontainers).
- [ ] 2.4 A API do Kubernetes no `grolla-fakes` (listagens usadas, com um ConfigMap alterado por `kubectl-edit` roteirizável) e o LLM fake chamando as ferramentas do cluster; README do core e do fakes; `make check` verde.

## 3. grolla-e2e

- [ ] 3.1 Cenário: alerta sem deploy no pipeline, ConfigMap alterado no fake e a causa raiz citando a mudança e a ferramenta; dois clusters, um com `show_values: false`, sem o valor em nenhuma requisição ao LLM fake; `make check` verde.

## 4. Integração (agente pai)

- [ ] 4.1 Juntar os branches e resolver conflitos.
- [ ] 4.2 Code review do todo, com os ajustes que couberem.
- [ ] 4.3 RBAC somente leitura em `deploy/` (ClusterRole, binding e o mapeamento no EKS) e o README do deploy; rótulos das ferramentas no war room.
- [ ] 4.4 Registro de decisões, roadmap e `CLAUDE.md`; desvios no `design.md`; READMEs.
- [ ] 4.5 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 4.6 Rodar `make fmt && make check` e garantir tudo verde.
- [ ] 4.7 Teste realista no EKS (várias aplicações e uma falha injetada por `kubectl`), só depois do custo aprovado pelo usuário.
