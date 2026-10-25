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

