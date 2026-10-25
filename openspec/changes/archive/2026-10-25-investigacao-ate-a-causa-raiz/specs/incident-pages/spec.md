## ADDED Requirements

### Requirement: Página mostra as duas fases
A página do incidente SHALL mostrar a análise inicial e, abaixo dela, a validação. A validação aparece como em andamento, concluída ou parcial com o motivo. Quando existir, a página SHALL mostrar a causa raiz, o veredito de cada hipótese com justificativa e evidências, o plano de ação e as avaliações feitas na validação.

#### Scenario: Validação em andamento
- **WHEN** a análise inicial terminou e a validação ainda roda
- **THEN** a página mostra as hipóteses iniciais e "validando as hipóteses"
