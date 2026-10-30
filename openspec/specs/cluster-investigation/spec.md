# cluster-investigation Specification

## Purpose
TBD - created by archiving change cluster-somente-leitura. Update Purpose after archive.
## Requirements
### Requirement: Estado do serviço no cluster
O LLM SHALL ter a ferramenta neutra `cluster_state(service)`. Ela SHALL devolver, para cada workload do serviço:
- as réplicas desejadas e as prontas, e se há rollout em andamento;
- cada pod, com a fase, os reinícios, o último término (motivo e exit code), a espera (por exemplo `CrashLoopBackOff`, `ImagePullBackOff`), as probes falhando e os limites de CPU e memória;
- o HPA do workload, com as réplicas atuais, as desejadas e o máximo, dizendo "no máximo" quando o atual chegou ao máximo;
- os eventos de aviso do cluster na janela da investigação ligados ao workload, aos pods ou ao HPA, com horário, motivo e mensagem.

Os workloads SHALL ser descobertos pelo rótulo configurado do cluster (padrão `app.kubernetes.io/name`, depois `app`) nos namespaces configurados, e por um mapa opcional serviço → workloads no YAML. Serviço sem workload encontrado SHALL receber "nenhum workload encontrado no cluster para o serviço", sem erro.

#### Scenario: Pod morto por memória
- **WHEN** o LLM chama `cluster_state` para `pedidos-api` e um pod reiniciou 4 vezes com o último término `OOMKilled`
- **THEN** a resposta mostra o pod com 4 reinícios, o término `OOMKilled` e o limite de memória do contêiner

#### Scenario: HPA no teto
- **WHEN** o HPA do `pedidos-api` tem 10 réplicas atuais e máximo 10
- **THEN** a resposta mostra o HPA "no máximo (10 de 10)"

#### Scenario: Serviço fora do cluster
- **WHEN** nenhum workload tem o rótulo do serviço e o mapa não o cita
- **THEN** a resposta diz que nenhum workload foi encontrado no cluster, e a avaliação registra isso

### Requirement: Mudanças recentes no cluster
O LLM SHALL ter a ferramenta neutra `cluster_changes(service)`, que lista o que mudou no cluster para o serviço desde o início da janela da investigação, do mais recente para o mais antigo, com horário e a ferramenta que fez a mudança (o gerenciador do campo, por exemplo `kubectl-edit`, `kubectl-set`, `helm`, `argocd`):
- as revisões novas do workload, com o que mudou no template do pod em relação à revisão anterior: imagem, variáveis de ambiente (nome e valor literal; referências a Secret só pelo nome) e recursos;
- as mudanças de réplicas do workload;
- os ConfigMaps usados pelos pods do serviço (por variável, `envFrom` ou volume) alterados na janela, com as chaves alteradas e o valor atual de cada uma;
- o HPA do workload e as NetworkPolicies do namespace alterados na janela.

Sem mudanças, a resposta SHALL dizer "nenhuma mudança no cluster para o serviço na janela".

#### Scenario: Valor oculto no cluster PCI
- **WHEN** o ConfigMap `pedidos-config` foi alterado às 03:10 por `kubectl-edit` num cluster com `show_values: false`
- **THEN** a resposta mostra "03:10 ConfigMap pedidos-config alterado por kubectl-edit: DB_POOL_SIZE (valor oculto)" e nenhum valor sai do adapter

#### Scenario: ConfigMap editado na mão
- **WHEN** às 03:10 alguém rodou `kubectl edit` no ConfigMap `pedidos-config`, usado pelo `pedidos-api`, e mudou `DB_POOL_SIZE` para 2
- **THEN** a resposta mostra "03:10 ConfigMap pedidos-config alterado por kubectl-edit: DB_POOL_SIZE=2"

#### Scenario: Limite de memória reduzido
- **WHEN** uma revisão nova do `pedidos-api` às 02:50 reduziu o limite de memória de 512Mi para 128Mi, feita por `kubectl-set`
- **THEN** a resposta mostra a revisão às 02:50 com "memória 512Mi → 128Mi" e a ferramenta `kubectl-set`

### Requirement: Somente leitura no cluster
O adapter do cluster SHALL fazer só leituras (`get`, `list`) de uma lista fechada de recursos: workloads (Deployment, StatefulSet, DaemonSet, ReplicaSet), Pod, Event, ConfigMap, HorizontalPodAutoscaler e NetworkPolicy. Secrets SHALL NOT ser lidos. A RBAC recomendada no `deploy/` SHALL conceder só `get`, `list` e `watch` nesses recursos. Falha de credencial, permissão ou rede SHALL virar "não foi possível consultar" na avaliação, sem interromper a análise.

#### Scenario: Permissão negada
- **WHEN** a conta de serviço não pode listar eventos
- **THEN** o estado vem sem os eventos e diz que não foi possível consultá-los, e a análise continua

#### Scenario: Instância sem cluster
- **WHEN** o YAML não tem `clusters` ou todos estão com `enabled: false`
- **THEN** as ferramentas do cluster respondem "consulta ao cluster não configurada nesta instância", sem erro

### Requirement: Vários clusters
As ferramentas do cluster SHALL consultar todos os clusters com `enabled: true`, cada um com o acesso e os namespaces dele. Cada item da resposta SHALL dizer de que cluster veio, mesmo com um cluster só, para o plano dizer onde rodar um comando. A falha de um cluster SHALL virar a linha "cluster <nome>: <erro>" sem esconder os outros; só quando todos falham a ferramenta SHALL falhar. Nos clusters com `show_values: false`, o adapter SHALL NOT devolver valores de ConfigMap nem de variáveis de ambiente, só os nomes.

#### Scenario: PCI e não PCI
- **WHEN** o `pedidos-api` roda nos clusters `nao-pci` e `pci` e o LLM chama `cluster_state`
- **THEN** a resposta traz os pods "no cluster nao-pci" e "no cluster pci"

#### Scenario: Um cluster fora do ar
- **WHEN** o cluster `pci` recusa a credencial
- **THEN** a resposta traz o `nao-pci` e termina com "cluster pci: não foi possível consultar"

#### Scenario: Cluster desligado
- **WHEN** o cluster `pci` está com `enabled: false`
- **THEN** nenhuma consulta vai ao `pci`

#### Scenario: Um cluster só
- **WHEN** só o cluster `loja-prod` está ligado e o LLM chama `cluster_state`
- **THEN** os pods vêm "no cluster loja-prod"

### Requirement: Cluster na investigação
Os prompts da análise, da validação, do chat do war room e do console SHALL citar `cluster_state` e `cluster_changes` como passo de infraestrutura: o estado quando há erro, reinício ou lentidão num serviço que roda no cluster, e as mudanças quando o problema começou sem deploy no pipeline. Uma mudança no cluster que explica o sintoma SHALL poder virar a causa raiz, com o horário e a ferramenta como evidência.

#### Scenario: Causa raiz fora do pipeline
- **WHEN** os erros do `pedidos-api` começaram às 03:11, não há deploy no pipeline na janela e `cluster_changes` mostra o ConfigMap alterado às 03:10
- **THEN** a validação confirma a hipótese da mudança no ConfigMap, com a evidência do horário e da ferramenta, e o plano sugere reverter o valor

### Requirement: Réplicas do último apply
Quando um Deployment ou StatefulSet tem a anotação do último `apply` e as réplicas dela diferem das réplicas atuais, `cluster_state` SHALL dizer as réplicas do último apply ao lado das atuais. Um `scale` manual não deixa no cluster o valor anterior, e esse é o dado que o plano precisa para sugerir o comando que volta ao normal.

#### Scenario: Escalado a zero à mão
- **WHEN** o Deployment `antifraude-api` foi aplicado com 2 réplicas e alguém o escalou para 0
- **THEN** `cluster_state` traz "0 de 0 réplicas prontas (2 no último apply)"

