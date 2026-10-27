# cloud-investigation Specification

## Purpose

Deixar o agente consultar a nuvem só para leitura: estado dos recursos do serviço, mudanças registradas na auditoria e alarmes, por ferramentas neutras.
## Requirements
### Requirement: Recursos de nuvem do serviço
A ferramenta `cloud_resources(service)` SHALL listar os recursos de nuvem do serviço e o estado de cada um: tipo, identificador, estado atual (por exemplo `available`, `stopped`, `running`) e link para o console. Os recursos SHALL ser descobertos pela tag `service=<nome do serviço>` e por um mapa opcional serviço → recursos no YAML do time. Serviço sem recursos encontrados SHALL receber a resposta "nenhum recurso de nuvem encontrado para o serviço", sem erro.

#### Scenario: Banco parado
- **WHEN** o LLM chama `cloud_resources` para `pedidos-api` e o banco gerenciado com a tag `service=pedidos-api` está parado
- **THEN** a resposta lista o banco com o estado `stopped`, além da máquina com o estado `running`

#### Scenario: Serviço sem recursos
- **WHEN** nenhum recurso tem a tag do serviço e o mapa não o cita
- **THEN** a resposta diz que nenhum recurso de nuvem foi encontrado, e a avaliação registra isso

### Requirement: Mudanças recentes nos recursos
A ferramenta `cloud_changes(service)` SHALL listar os eventos de auditoria da janela da investigação nos recursos do serviço, com horário, ação, recurso e autor, do mais recente para o mais antigo. As leituras puras (Describe*, List*, Get*) SHALL ficar de fora.

#### Scenario: Parada manual do banco
- **WHEN** o banco do serviço foi parado manualmente às 21:42
- **THEN** a resposta mostra "21:42 StopDBInstance em grolla-lab-db por root"

### Requirement: Alarmes da nuvem
A ferramenta `cloud_alarms(service)` SHALL listar os alarmes de monitoramento da nuvem em estado de alarme cujas dimensões citam os recursos do serviço, com nome, motivo e desde quando.

#### Scenario: Sem alarmes
- **WHEN** nenhum alarme está em estado de alarme para os recursos do serviço
- **THEN** a resposta diz que não há alarmes ativos

### Requirement: Somente leitura na nuvem
O adapter de nuvem SHALL fazer só chamadas de leitura de uma lista fechada no código. As credenciais SHALL vir do ambiente pela cadeia padrão do SDK e SHALL ser de um principal somente leitura. Falha de credencial, permissão ou rede SHALL virar "não foi possível consultar" na avaliação, sem interromper a análise.

#### Scenario: Permissão negada
- **WHEN** as credenciais não permitem consultar os eventos de auditoria
- **THEN** `cloud_changes` vira uma avaliação "não foi possível consultar" e a análise continua

### Requirement: Eventos da AWS na investigação
O LLM SHALL ter a ferramenta neutra `cloud_health(service)`, que devolve os eventos abertos ou recentes da plataforma de nuvem (AWS Health) nas regiões configuradas que afetam os recursos do serviço ou a região toda, com o serviço da nuvem, o tipo, o início e o estado. Sem o plano que libera a API, a resposta SHALL dizer "eventos da nuvem indisponíveis no plano de suporte desta conta", sem erro.

#### Scenario: Conta sem plano de suporte
- **WHEN** o LLM chama `cloud_health` numa conta sem o plano que libera o AWS Health
- **THEN** recebe que os eventos não estão disponíveis no plano, e a investigação segue

### Requirement: Cotas na investigação
O LLM SHALL ter a ferramenta neutra `cloud_quotas(service)`, que devolve o uso contra o limite das cotas de uma lista fechada (instâncias e armazenamento do banco gerenciado, vCPUs de máquinas sob demanda, balanceadores), destacando as que passam de 80%.

#### Scenario: Cota perto do limite
- **WHEN** a conta usa 18 de 20 instâncias de banco gerenciado
- **THEN** a resposta destaca "instâncias de banco: 18 de 20 (90%)"

### Requirement: Contêineres na investigação
`cloud_resources` SHALL incluir os serviços de contêiner (ECS) com a tag `service=<nome>` (tarefas desejadas e rodando, estado do último deployment) e os clusters Kubernetes gerenciados (EKS) com a tag (estado do cluster e dos nodegroups).

#### Scenario: Deployment do ECS travado
- **WHEN** o serviço ECS do `pedidos-api` tem 4 tarefas desejadas e 1 rodando, com o deployment em andamento
- **THEN** `cloud_resources` mostra "4 desejadas, 1 rodando, deployment em andamento"

### Requirement: Várias contas
Com `cloud.accounts`, o adapter SHALL assumir a role somente leitura de cada conta e juntar as respostas de todas as ferramentas de nuvem, dizendo de que conta veio cada item. Falha numa conta SHALL aparecer na resposta e SHALL NOT esconder as outras.

#### Scenario: Recurso em outra conta
- **WHEN** o RDS do `pedidos-api` está na conta "dados" e a EC2 na conta "apps"
- **THEN** `cloud_resources` mostra os dois, cada um com o nome da conta

