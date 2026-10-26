## ADDED Requirements

### Requirement: Consulta à memória
O LLM SHALL ter a ferramenta neutra `recall(subject, query)` na investigação, na validação e no chat. Ela SHALL devolver os registros ativos da memória que casam com a busca textual, confirmados e mais recentes primeiro, com tipo, confiança, data e link; `subject` vazio busca em todos os assuntos. Cada chamada SHALL virar avaliação como as outras ferramentas.

#### Scenario: Buscar caso antigo
- **WHEN** o LLM chama `recall` para `pedidos-api` com "pool de conexões"
- **THEN** recebe os episódios e notas do serviço que citam pool de conexões, com o link de cada incidente

#### Scenario: Nada na memória
- **WHEN** o LLM chama `recall` para um assunto sem memória
- **THEN** recebe "nada na memória sobre isso" em vez de um erro

### Requirement: Leitura do repositório
O LLM SHALL ter a ferramenta neutra `read_file(service, path)` na investigação, na validação, no chat e no estudo do ambiente. Ela SHALL ler, somente leitura, um arquivo do repositório do serviço (achado como em `recent_deploys`) no branch padrão; um caminho vazio ou de pasta SHALL devolver a listagem da pasta. O conteúdo SHALL ser cortado num limite fixo de tamanho, e o corte SHALL ser dito na resposta. Arquivo binário SHALL ser recusado com aviso.

#### Scenario: Ler o README
- **WHEN** o LLM chama `read_file` para `pedidos-api` com `README.md`
- **THEN** recebe o conteúdo do README e o link para o arquivo no repositório

#### Scenario: Listar a raiz
- **WHEN** o LLM chama `read_file` para `pedidos-api` com caminho vazio
- **THEN** recebe a lista de arquivos e pastas da raiz do repositório

#### Scenario: Arquivo grande
- **WHEN** o arquivo passa do limite de tamanho
- **THEN** a resposta traz o início do arquivo e diz que foi cortado

### Requirement: Dependências vistas nos traces
O LLM SHALL ter a ferramenta neutra `service_dependencies(service)` na investigação, na validação, no chat e no estudo do ambiente. Ela SHALL devolver, a partir dos spans de saída do serviço na janela, cada dependência chamada (outro serviço, banco, fila ou host externo) com o tipo, o volume de chamadas, a taxa de erro e a latência p99, e o link para os traces.

#### Scenario: Banco e API externa
- **WHEN** o LLM chama `service_dependencies` para `pedidos-api`
- **THEN** recebe o banco `grolla-lab-db` e o host externo chamado pelo serviço, cada um com chamadas, erro e p99

#### Scenario: Serviço sem traces
- **WHEN** o serviço não tem spans de saída na janela
- **THEN** a resposta diz que não há dependências vistas na janela, sem erro

### Requirement: Visão geral da nuvem
O LLM SHALL ter a ferramenta neutra `cloud_overview()` no estudo do ambiente e na investigação. Ela SHALL devolver, somente leitura, os recursos das regiões configuradas agrupados por tipo e pela tag `service`, incluindo os recursos sem a tag (rede, balanceadores, bancos compartilhados), com o estado de cada um. Sem a seção `cloud` no YAML, SHALL responder que a nuvem não está configurada.

#### Scenario: Recursos compartilhados
- **WHEN** o LLM chama `cloud_overview` e há um balanceador sem a tag `service` na frente de dois serviços
- **THEN** o balanceador aparece no grupo "sem serviço", com tipo e estado
