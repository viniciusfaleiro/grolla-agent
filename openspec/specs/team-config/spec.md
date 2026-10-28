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
- `GROLLA_ANTHROPIC_API_KEY`, exigida só quando `llm.provider` é `anthropic`;
- `GROLLA_SIGNOZ_API_KEY`, usada só pelo container do servidor MCP de telemetria;
- `GROLLA_AZURE_DEVOPS_TOKEN`.

O YAML SHALL NOT conter segredos. Cada processo SHALL exigir só os segredos que usa.

#### Scenario: Senha do webhook ausente
- **WHEN** a api sobe sem `GROLLA_ALERTS_WEBHOOK_PASSWORD`
- **THEN** o processo não sobe e a mensagem nomeia a variável

#### Scenario: Chave do LLM ausente
- **WHEN** o worker sobe com `llm.provider: anthropic` (ou sem `llm.provider`) e sem `GROLLA_ANTHROPIC_API_KEY`
- **THEN** o processo não sobe e a mensagem nomeia a variável

#### Scenario: Bedrock sem chave da Anthropic
- **WHEN** o worker sobe com `llm.provider: bedrock` e sem `GROLLA_ANTHROPIC_API_KEY`
- **THEN** o processo sobe normalmente

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

### Requirement: Configuração do acompanhamento e do impacto
O YAML SHALL aceitar a seção opcional `follow_up` com `severities` (padrão `[P0, P1]`), `cycle_minutes` (padrão 5), `quiet_minutes` (padrão 15), `stable_minutes` (padrão 15), `max_cycles` (padrão 24), `cycle_max_tokens` (padrão 40000), `cycle_timeout_seconds` (padrão 180) e `max_tokens_per_incident` (padrão 600000), e a etapa `follow_up` em `llm.stages`, que herda de `investigation`. Sem `follow_up`, nenhum ciclo SHALL rodar.

O YAML SHALL aceitar a seção opcional `impact` com `currency` (padrão `BRL`) e `services.<serviço>.per_minute`, maior que zero.

#### Scenario: Acompanhamento desligado
- **WHEN** o YAML não tem `follow_up`
- **THEN** nenhum ciclo roda e o resto do incidente funciona como antes

#### Scenario: Valor por minuto inválido
- **WHEN** o YAML tem `impact.services.pedidos-api.per_minute: 0`
- **THEN** o processo não sobe e a mensagem aponta o campo

### Requirement: Configuração do post mortem
A seção `llm.stages` SHALL aceitar a etapa `post_mortem`; sem ela, SHALL valer o modelo da etapa `investigation`. O YAML SHALL aceitar `post_mortem.max_tokens` (padrão 80000) e `post_mortem.timeout_seconds` (padrão 180), maiores que zero.

#### Scenario: Modelo herdado
- **WHEN** o YAML define só `llm.stages.investigation`
- **THEN** o rascunho do post mortem usa o mesmo modelo e registra o uso com a etapa `post_mortem`

### Requirement: Configuração da triagem
O YAML SHALL aceitar a seção opcional `noise` com `window_minutes` (padrão 30), maior que zero, e a etapa `triage` em `llm.stages`, que herda de `investigation`. Sem `noise`, nenhuma triagem SHALL rodar.

#### Scenario: Triagem desligada
- **WHEN** o YAML não tem `noise`
- **THEN** nenhum pedido de triagem é gravado e o incidente segue como antes

### Requirement: Configuração do console
O YAML SHALL aceitar a seção opcional `console` com:
- `max_tokens` por resposta (padrão 60000);
- `daily_max_tokens` (padrão 500000);
- `timeout_seconds` (padrão 120).

Também SHALL aceitar a etapa `console` em `llm.stages`, que herda de `warroom_chat` e, sem ela, de `investigation`. Sem `console`, a página SHALL dizer que o console está desligado.

#### Scenario: Console desligado
- **WHEN** o YAML não tem `console`
- **THEN** `/console` diz que o console está desligado nesta instância

### Requirement: Contas da nuvem
A seção `cloud` SHALL aceitar `accounts`, uma lista de `{name, role_arn, regions}`. Com `accounts`, SHALL valer a lista; sem ela, a conta das credenciais do ambiente, como hoje. `role_arn` SHALL ser um ARN de role do IAM, e o processo SHALL NOT subir com um ARN inválido.

#### Scenario: ARN inválido
- **WHEN** o YAML tem `cloud.accounts[0].role_arn: "admin"`
- **THEN** o processo não sobe e a mensagem aponta o campo

### Requirement: Configuração dos clusters
A seção opcional `clusters` SHALL ser uma lista de clusters consultados só para leitura, cada um com:
- `name`: o nome que aparece nas respostas (único);
- `enabled`: liga ou desliga o cluster na investigação (padrão `true`);
- `access`: `kubeconfig` (com `context` opcional), `in_cluster` ou `eks` (com `eks_name` e `region`, e o token gerado pela credencial da nuvem do ambiente);
- `role_arn`: opcional, só no `eks`: a role somente leitura da conta do cluster, assumida antes de gerar o token;
- `namespaces`: os namespaces consultados (ao menos um);
- `service_label`: o rótulo que liga um workload ao serviço (padrão `app.kubernetes.io/name`, depois `app`);
- `workload_map`: mapa opcional serviço → `[namespace/nome]` para as exceções;
- `show_values`: se valores de ConfigMap e de variáveis de ambiente podem aparecer (padrão `true`; `false` nos clusters PCI).

Sem a seção, ou com todos desligados, as ferramentas do cluster SHALL responder que a consulta não está configurada. Nenhuma credencial SHALL ir no YAML.

#### Scenario: EKS sem região
- **WHEN** o YAML tem um cluster com `access: eks` sem `region`
- **THEN** o processo não sobe e a mensagem aponta o campo

#### Scenario: Nomes repetidos
- **WHEN** dois clusters têm o mesmo `name`
- **THEN** o processo não sobe e a mensagem aponta o nome repetido

### Requirement: Provedor do modelo
A seção `llm` do YAML SHALL aceitar `provider`, com `anthropic` (padrão) ou `bedrock`, e `region`, a região da AWS em que o modelo é chamado. Com `bedrock`, `region` SHALL ser obrigatória, e o worker SHALL chamar o modelo pelo Amazon Bedrock com as credenciais da cadeia padrão do SDK da AWS, sem chave de API. Com `anthropic`, `region` SHALL NOT ser aceita. Os IDs de `llm.stages` SHALL ser os do provedor escolhido. Todas as etapas SHALL usar o mesmo provedor e manter os mesmos recursos (ferramentas, thinking adaptativo, `effort`, saída estruturada, cache de prompt e registro de uso por etapa). O YAML SHALL NOT conter credenciais da AWS.

#### Scenario: Investigação pelo Bedrock
- **WHEN** o YAML tem `llm.provider: bedrock` e `llm.region: us-east-1` e um incidente novo é investigado
- **THEN** as chamadas ao modelo vão para o Bedrock em `us-east-1`, com o ID de modelo da etapa, as ferramentas e a saída estruturada, e o uso é registrado com a etapa `investigation`

#### Scenario: Bedrock sem região
- **WHEN** o YAML tem `llm.provider: bedrock` sem `llm.region`
- **THEN** o processo não sobe e a mensagem aponta o campo `llm.region`

#### Scenario: Provedor desconhecido
- **WHEN** o YAML tem `llm.provider: vertex`
- **THEN** o processo não sobe e a mensagem aponta o campo `llm.provider`

#### Scenario: Sem provedor no YAML
- **WHEN** o YAML não tem `llm.provider`
- **THEN** o worker usa a API da Anthropic com `GROLLA_ANTHROPIC_API_KEY`, como antes

