# investigation-tools Specification

## Purpose

Dar ao modelo de linguagem só ferramentas neutras e somente leitura sobre telemetria e histórico de mudanças, registrando cada consulta como avaliação.
## Requirements
### Requirement: Ferramentas neutras da investigação
O LLM SHALL investigar só por ferramentas com nome e parâmetros neutros, sem nome de fornecedor:
- `query_metric`: série de uma métrica de um serviço numa janela;
- `search_logs`: logs de um serviço por texto ou severidade numa janela;
- `find_traces`: traces com erro ou lentos de um serviço numa janela;
- `recent_deploys`: deploys e execuções de pipeline do repositório do serviço numa janela.

Cada ferramenta SHALL devolver um resumo compacto e, quando houver, um link para a origem. Trocar o fornecedor SHALL NOT mudar o nome, os parâmetros nem o formato de resposta das ferramentas.

#### Scenario: Deploys recentes
- **WHEN** o LLM chama `recent_deploys` para `authorizer-api` nas últimas 6 horas
- **THEN** recebe a lista de deploys com horário, versão e autor, e o link para o pipeline

#### Scenario: Repositório achado pelo nome
- **WHEN** o LLM chama `recent_deploys` para `captura-api`, que não está no mapa, e existe um repositório `captura-api` num dos projetos configurados
- **THEN** recebe os deploys desse repositório

#### Scenario: Serviço sem repositório
- **WHEN** o LLM chama `recent_deploys` para um serviço fora do mapa e sem repositório com o mesmo nome nos projetos configurados
- **THEN** recebe a resposta "serviço sem repositório mapeado" em vez de um erro, e a avaliação registra isso

### Requirement: Somente leitura nos fornecedores
O adapter de telemetria SHALL chamar no servidor MCP só as ferramentas de leitura de uma lista fechada, mantida no código. O adapter de histórico de mudanças SHALL usar um token somente leitura. O adapter da nuvem SHALL chamar só métodos `describe_*`, `list_*`, `get_*` e `lookup_*`, e o adapter do cluster SHALL fazer só requisições GET. Nenhuma ferramenta de escrita (criar, alterar ou apagar alerta, dashboard, view, canal, recurso de nuvem ou objeto do cluster) SHALL ser chamada. Um teste no `make check` SHALL conferir essas listas.

#### Scenario: Ferramenta de escrita fora da lista
- **WHEN** o adapter de telemetria recebe um pedido de ferramenta fora da lista de leitura
- **THEN** ele recusa sem chamar o servidor MCP

#### Scenario: Chamada de escrita na nuvem
- **WHEN** alguém acrescenta ao adapter da nuvem uma chamada `update_service`
- **THEN** o teste de capacidade falha

### Requirement: Falha de ferramenta vira avaliação, não queda
O erro ou o timeout de uma ferramenta SHALL voltar ao LLM como resultado de erro e SHALL ficar registrado como avaliação com achado "não foi possível consultar". O erro SHALL NOT interromper a análise.

#### Scenario: Telemetria fora do ar
- **WHEN** o servidor MCP não responde a uma chamada de `search_logs`
- **THEN** a análise continua e a avaliação de logs aparece como "não foi possível consultar"

### Requirement: Logs mostram a causa, não só o sintoma
A ferramenta `search_logs` SHALL considerar logs de nível WARN e acima. A resposta SHALL trazer as 5 mensagens mais frequentes, cada uma com a quantidade e o nível. Mensagens que só diferem em números, endereços IP, endereços de memória ou identificadores SHALL ser agrupadas como uma só.

#### Scenario: Causa registrada como WARN
- **WHEN** a aplicação registra 112 ERROR "couldn't get a connection after 5.00 sec" e 22 WARN "connection to server … failed: server closed the connection unexpectedly"
- **THEN** a resposta traz as duas mensagens, com quantidade e nível

### Requirement: Deploys trazem a mensagem do commit
Cada deploy na resposta de `recent_deploys` SHALL trazer a primeira linha da mensagem do commit, além de horário, versão, branch, commit, autor e resultado.

#### Scenario: Deploy de revert
- **WHEN** o último deploy foi o commit "Revert \"Mostrar o valor com desconto na listagem de pedidos\""
- **THEN** a resposta mostra essa mensagem ao lado do build

### Requirement: Séries dizem quando a mudança começou
A ferramenta `query_metric` SHALL usar pontos de 1 minuto. O resumo SHALL dizer o primeiro minuto em que o sinal saiu do patamar do início da janela, com o valor antes e depois.

#### Scenario: Erros começam depois do último deploy
- **WHEN** a taxa de erro fica em 0% até 21:41 e sobe para 70% a partir de 21:42
- **THEN** o resumo diz que a mudança começou às 21:42, de 0% para 70%

### Requirement: Consulta à memória
O LLM SHALL ter a ferramenta neutra `recall(subject, query)` na investigação, na validação e no chat. Ela SHALL devolver o que a busca da memória achar, mais relevantes primeiro, com tipo, confiança, data e link; `subject` vazio busca em todos os assuntos.
- Com `memory.rag`, a busca é a híbrida e devolve trechos: o título da seção, o texto do trecho e a origem, que pode ser um registro da memória ou um arquivo de documentação com o link.
- Sem `memory.rag`, devolve os registros que casam com a busca textual, confirmados e mais recentes primeiro.

Cada chamada SHALL virar avaliação como as outras ferramentas.

#### Scenario: Buscar caso antigo
- **WHEN** o LLM chama `recall` para `pedidos-api` com "pool de conexões"
- **THEN** recebe os episódios e notas do serviço que citam pool de conexões, com o link de cada incidente

#### Scenario: Buscar no runbook
- **WHEN** com `memory.rag`, o LLM chama `recall` para `pedidos-api` com "como liberar conexões presas"
- **THEN** recebe o trecho do runbook sobre pool de conexões, com o link do arquivo, mesmo sem as mesmas palavras

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

