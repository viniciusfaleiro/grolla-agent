# team-config Specification

## Purpose

Configurar cada instância por um YAML do time, validado no startup, com segredos só em variáveis de ambiente.

## Requirements
### Requirement: Configuração da instância por YAML
Cada instância SHALL ler um arquivo YAML indicado por `GROLLA_CONFIG_FILE` com: nome do time, URL base das páginas, filtro de labels dos alertas, labels que identificam o serviço, janela de agrupamento, regras de severidade e severidade padrão. Os nomes de labels SHALL ser todos configuráveis, porque os labels reais dos alertas do time ainda não são conhecidos. O arquivo SHALL ser validado na inicialização, e erro de validação SHALL impedir o processo de subir com mensagem que aponte o campo.

#### Scenario: Regra com severidade inválida
- **WHEN** o YAML tem uma regra com `severity: P7`
- **THEN** o processo não sobe e a mensagem aponta a regra e o campo

### Requirement: Segredos só no ambiente
Segredos SHALL vir só de variáveis de ambiente: `GROLLA_TEAMS_WEBHOOK_URL`, `GROLLA_ALERTS_WEBHOOK_USER` e `GROLLA_ALERTS_WEBHOOK_PASSWORD`. O YAML SHALL NOT conter segredos.

#### Scenario: Senha do webhook ausente
- **WHEN** a api sobe sem `GROLLA_ALERTS_WEBHOOK_PASSWORD`
- **THEN** o processo não sobe e a mensagem nomeia a variável

