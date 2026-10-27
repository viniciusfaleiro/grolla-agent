# team-config Specification

## Purpose

Configurar cada instância por um YAML do time, validado no startup, com segredos só em variáveis de ambiente.
## Requirements
### Requirement: Configuração da instância por YAML
Cada instância SHALL ler um arquivo YAML indicado por `GROLLA_CONFIG_FILE` com:
- nome do time e URL base das páginas;
- filtro de labels dos alertas, labels que identificam o serviço e janela de agrupamento;
- regras de severidade e severidade padrão;
- limites da análise inicial (tempo e tokens);
- modelo e parâmetros por etapa do LLM;
- URL do servidor MCP de telemetria e URL base para links;
- organização e projetos do histórico de mudanças e o mapa serviço → repositório (opcional, só para exceções ao nome do serviço).

Os nomes de labels SHALL ser todos configuráveis, porque os labels reais dos alertas do time ainda não são conhecidos. Nenhum ID de modelo SHALL ficar no código. O arquivo SHALL ser validado na inicialização, e erro de validação SHALL impedir o processo de subir com mensagem que aponte o campo.

#### Scenario: Regra com severidade inválida
- **WHEN** o YAML tem uma regra com `severity: P7`
- **THEN** o processo não sobe e a mensagem aponta a regra e o campo

#### Scenario: Trocar o modelo da investigação
- **WHEN** o YAML muda `llm.stages.investigation.model` e o worker é reiniciado
- **THEN** as análises seguintes usam o novo modelo, sem mudança de código

### Requirement: Segredos só no ambiente
Segredos SHALL vir só de variáveis de ambiente:
- `GROLLA_TEAMS_WEBHOOK_URL`;
- `GROLLA_ALERTS_WEBHOOK_USER` e `GROLLA_ALERTS_WEBHOOK_PASSWORD`;
- `GROLLA_ANTHROPIC_API_KEY`;
- `GROLLA_SIGNOZ_API_KEY`, usada só pelo container do servidor MCP de telemetria;
- `GROLLA_AZURE_DEVOPS_TOKEN`.

O YAML SHALL NOT conter segredos. Cada processo SHALL exigir só os segredos que usa.

#### Scenario: Senha do webhook ausente
- **WHEN** a api sobe sem `GROLLA_ALERTS_WEBHOOK_PASSWORD`
- **THEN** o processo não sobe e a mensagem nomeia a variável

#### Scenario: Chave do LLM ausente
- **WHEN** o worker sobe sem `GROLLA_ANTHROPIC_API_KEY`
- **THEN** o processo não sobe e a mensagem nomeia a variável

### Requirement: Configuração da nuvem
O YAML do time SHALL aceitar a seção `cloud`, com as regiões e um mapa opcional serviço → identificadores de recursos. Sem a seção, as ferramentas de nuvem SHALL responder que a nuvem não está configurada, sem erro. As credenciais da nuvem SHALL vir só do ambiente, pela cadeia padrão do SDK, e o YAML SHALL NOT contê-las.

#### Scenario: Nuvem não configurada
- **WHEN** o YAML não tem a seção `cloud` e o LLM chama `cloud_resources`
- **THEN** a resposta diz que a consulta à nuvem não está configurada nesta instância

### Requirement: Limites da validação
A seção `investigation` do YAML SHALL aceitar os limites da validação (`verification_timeout_seconds` e `verification_max_tokens`). A seção `llm.stages` SHALL aceitar a etapa `verification`; sem ela, a validação SHALL usar o modelo da etapa `investigation`.

#### Scenario: Modelo da validação herdado
- **WHEN** o YAML define só `llm.stages.investigation`
- **THEN** a validação usa o mesmo modelo e registra o uso com a etapa `verification`

### Requirement: Configuração da memória
O YAML do time SHALL aceitar a seção `memory` com:
- `learning_max_tokens`: limite de tokens do aprendizado de um incidente;
- `study` (opcional): `cron` do estudo diário, `run_max_tokens` (limite de uma rodada de estudo, inicial ou diária), `service_max_tokens` (limite por serviço) e `lookback_days` (janela de deploys e traces do estudo).

A seção `llm.stages` SHALL aceitar as etapas `learning` e `study`; sem elas, SHALL valer o modelo da etapa `investigation`. Sem a seção `memory`, o aprendizado SHALL usar um limite padrão e nenhum estudo do ambiente SHALL rodar.

#### Scenario: Estudo desligado
- **WHEN** o YAML não tem `memory.study`
- **THEN** nenhum estudo roda e o aprendizado ao encerrar continua ligado

#### Scenario: Limite inválido
- **WHEN** o YAML tem `memory.study.run_max_tokens: 0`
- **THEN** o processo não sobe e a mensagem aponta o campo

### Requirement: Configuração do RAG
A seção `memory` do YAML SHALL aceitar a subseção opcional `rag` com:
- `size`: o tamanho do modelo de embeddings, `small` (padrão) ou `large`, que precisa estar embutido na imagem do worker;
- `chunk_chars`: o tamanho máximo de um trecho;
- `max_doc_files` e `max_doc_bytes`: os limites da documentação indexada por serviço.

Sem `rag`, a memória SHALL funcionar sem embeddings, só com a busca textual. Valores fora do intervalo SHALL impedir o processo de subir, com a mensagem apontando o campo.

#### Scenario: RAG desligado
- **WHEN** o YAML não tem `memory.rag`
- **THEN** nenhum embedding é gerado e `recall` usa a busca textual

#### Scenario: Trecho pequeno demais
- **WHEN** o YAML tem `memory.rag.chunk_chars: 10`
- **THEN** o processo não sobe e a mensagem aponta o campo

