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

