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
O adapter de telemetria SHALL chamar no servidor MCP só as ferramentas de leitura de uma lista fechada, mantida no código. O adapter de histórico de mudanças SHALL usar um token somente leitura. Nenhuma ferramenta de escrita (criar, alterar ou apagar alerta, dashboard, view ou canal) SHALL ser chamada neste slice.

#### Scenario: Ferramenta de escrita fora da lista
- **WHEN** o adapter de telemetria recebe um pedido de ferramenta fora da lista de leitura
- **THEN** ele recusa sem chamar o servidor MCP

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

