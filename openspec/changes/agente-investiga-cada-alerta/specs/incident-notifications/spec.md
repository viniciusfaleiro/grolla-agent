## MODIFIED Requirements

### Requirement: Cards no canal do time
O agente SHALL postar no canal do time um card (Adaptive Card via Workflow do Teams) em quatro momentos: incidente novo ("investigando"), análise pronta, severidade elevada e incidente normalizado.

O card "investigando" SHALL:
- ser enfileirado na mesma transação que abre o incidente, sem esperar o LLM;
- conter o título, a severidade, os serviços, o resumo e a descrição do alerta como evidência e o que o agente vai checar;
- trazer os botões **Abrir incidente** (página do incidente) e de origem do alerta.

O card de análise SHALL:
- conter o resumo, o que o agente avaliou (cada avaliação com o achado), as hipóteses (título, confiança e a evidência principal com link) e o plano proposto;
- trazer os botões **Abrir incidente** e **Ver no SigNoz**;
- quando a análise for parcial, dizer que é parcial e o motivo.

Reenvios e agrupamentos sem mudança de severidade SHALL NOT gerar card. No canal, o card de análise SHALL vir sempre depois do "investigando" do mesmo incidente.

#### Scenario: Incidente novo
- **WHEN** um alerta abre um incidente P2 do `capture-api`
- **THEN** o canal recebe um card "investigando" com "P2", "capture-api", a evidência do alerta e o link da página do incidente

#### Scenario: Análise pronta
- **WHEN** a análise do incidente termina com duas hipóteses
- **THEN** o canal recebe, depois do "investigando", um card de análise com as avaliações, as duas hipóteses, o plano e o botão Abrir incidente

#### Scenario: Análise parcial
- **WHEN** a análise termina por tempo esgotado
- **THEN** o card de análise diz "análise parcial: tempo esgotado" e lista o que foi avaliado

#### Scenario: Agrupamento sem mudança
- **WHEN** um segundo alerta P2 do mesmo serviço entra no incidente
- **THEN** nenhum card novo é postado
