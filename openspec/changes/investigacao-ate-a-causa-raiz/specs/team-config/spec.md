## ADDED Requirements

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
