## MODIFIED Requirements

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
