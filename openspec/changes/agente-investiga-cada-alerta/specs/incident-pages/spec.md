## MODIFIED Requirements

### Requirement: Página do incidente
O `grolla-api` SHALL servir `GET /incidents/{id}`, uma página HTML com:
- título, severidade, estado, serviços e horário de detecção;
- a análise: em andamento, concluída ou parcial com o motivo, e, quando houver, o resumo, as avaliações, as hipóteses com evidências e links e o plano proposto;
- os alertas (status, regra, resumo, link para a origem);
- a timeline de eventos em ordem.

Incidente inexistente SHALL responder 404.

#### Scenario: Timeline visível
- **WHEN** a página de um incidente com dois alertas e uma escalada é aberta
- **THEN** ela mostra os dois alertas e os eventos de detecção, agrupamento e mudança de severidade em ordem

#### Scenario: Análise visível
- **WHEN** a página de um incidente com análise concluída é aberta
- **THEN** ela mostra o resumo, as avaliações, as hipóteses com confiança e links e o plano proposto

#### Scenario: Análise em andamento
- **WHEN** a página de um incidente cuja análise ainda roda é aberta
- **THEN** ela mostra "análise em andamento"

#### Scenario: Incidente inexistente
- **WHEN** `GET /incidents/<id que não existe>` é chamado
- **THEN** a resposta é 404
