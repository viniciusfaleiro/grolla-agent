## ADDED Requirements

### Requirement: Configuração da nuvem
O YAML do time SHALL aceitar a seção `cloud`, com as regiões e um mapa opcional serviço → identificadores de recursos. Sem a seção, as ferramentas de nuvem SHALL responder que a nuvem não está configurada, sem erro. As credenciais da nuvem SHALL vir só do ambiente, pela cadeia padrão do SDK, e o YAML SHALL NOT contê-las.

#### Scenario: Nuvem não configurada
- **WHEN** o YAML não tem a seção `cloud` e o LLM chama `cloud_resources`
- **THEN** a resposta diz que a consulta à nuvem não está configurada nesta instância
