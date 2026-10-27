## ADDED Requirements

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
