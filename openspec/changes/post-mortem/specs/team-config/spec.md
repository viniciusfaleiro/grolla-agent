## ADDED Requirements

### Requirement: Configuração do post mortem
A seção `llm.stages` SHALL aceitar a etapa `post_mortem`; sem ela, SHALL valer o modelo da etapa `investigation`. O YAML SHALL aceitar `post_mortem.max_tokens` (padrão 80000) e `post_mortem.timeout_seconds` (padrão 180), maiores que zero.

#### Scenario: Modelo herdado
- **WHEN** o YAML define só `llm.stages.investigation`
- **THEN** o rascunho do post mortem usa o mesmo modelo e registra o uso com a etapa `post_mortem`
