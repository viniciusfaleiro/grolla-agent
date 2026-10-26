# incident-notifications Specification

## Purpose

Avisar o canal do time nos momentos que importam, com entrega confiável por outbox, em ordem e com retentativa.
## Requirements
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

### Requirement: Entrega por outbox com retentativa
Cada card SHALL ser gravado numa fila de saída (outbox) na mesma transação da mudança que o originou. O `grolla-worker` SHALL entregar a fila em ordem por incidente, com retentativa e espera crescente quando o canal falhar, e SHALL registrar no incidente a entrega ou a desistência após o limite de tentativas.

#### Scenario: Teams fora do ar
- **WHEN** o canal responde erro nas duas primeiras tentativas e aceita na terceira
- **THEN** o card é entregue uma vez só e o incidente registra a entrega

#### Scenario: Desistência
- **WHEN** o canal falha em todas as tentativas permitidas
- **THEN** o incidente registra a falha de entrega e o card sai da fila

### Requirement: Card da causa raiz
Ao fim da validação, o agente SHALL postar no canal o card da causa raiz, depois do card da análise inicial.

O card SHALL trazer:
- a causa raiz confirmada, ou "causa raiz não confirmada";
- o veredito de cada hipótese, com uma linha de justificativa;
- o plano de ação concreto;
- o botão **Abrir incidente**.

O card da análise inicial SHALL avisar que as hipóteses estão sendo validadas.

#### Scenario: Causa confirmada no canal
- **WHEN** a validação confirma "RDS grolla-lab-db parado manualmente às 21:42"
- **THEN** o canal recebe "Causa raiz · P1" com a causa, as hipóteses confirmada e rejeitada e a ação "religar o RDS grolla-lab-db"

### Requirement: Normalização fecha a história do incidente
O card "Incidente normalizado" SHALL trazer a duração do incidente, da detecção até a normalização, e o desfecho da investigação, conforme o estado da validação:
- **confirmada**: a causa raiz e o plano de ação que foi sugerido;
- **sem confirmação**: "causa raiz não confirmada" e o que faltou;
- **ainda em andamento**: aviso de que a causa raiz aparecerá na página do incidente;
- **sem validação**: o card SHALL omitir a seção.

O card SHALL manter o botão Abrir incidente.

#### Scenario: Normalizou depois da causa raiz
- **WHEN** um incidente com causa raiz confirmada tem o último alerta resolvido 32 minutos depois da detecção
- **THEN** o card "Incidente normalizado" diz "durou 32 min" e traz a causa raiz e o plano de ação

#### Scenario: Normalizou durante a validação
- **WHEN** o último alerta resolve enquanto a validação ainda roda
- **THEN** o card diz que a validação está em andamento e que a causa raiz aparecerá na página do incidente

### Requirement: Cards das ações humanas
O agente SHALL postar no canal um card curto quando alguém assume como IC, muda a severidade ou marca mitigado, citando o autor.

No encerramento, SHALL postar o card "Incidente encerrado", com:
- a duração e o TTR;
- quem encerrou;
- o motivo;
- a causa raiz da validação, se houver;
- as ações registradas pelo time.

No descarte, SHALL postar "Incidente descartado" com o motivo. As mensagens do chat SHALL NOT ir para o canal.

#### Scenario: Encerramento no canal
- **WHEN** "Ana" encerra o incidente com o motivo "rollback do build .3"
- **THEN** o canal recebe "Incidente encerrado" com duração, "por Ana", o motivo, a causa raiz e as ações registradas

