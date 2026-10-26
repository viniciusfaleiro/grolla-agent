# incident-pages Specification

## Purpose

Dar ao time uma página por incidente e uma lista de incidentes, só leitura, na rede interna.
## Requirements
### Requirement: Lista de incidentes
O `grolla-api` SHALL servir `GET /incidents`, uma página HTML com os incidentes do mais recente para o mais antigo, mostrando título, severidade, estado, serviços e horário de detecção, cada um com link para a página dele.

#### Scenario: Dois incidentes
- **WHEN** existem dois incidentes
- **THEN** a página lista os dois, o mais recente primeiro

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

### Requirement: Página mostra as duas fases
A página do incidente SHALL mostrar a análise inicial e, abaixo dela, a validação. A validação aparece como em andamento, concluída ou parcial com o motivo. Quando existir, a página SHALL mostrar a causa raiz, o veredito de cada hipótese com justificativa e evidências, o plano de ação e as avaliações feitas na validação.

#### Scenario: Validação em andamento
- **WHEN** a análise inicial terminou e a validação ainda roda
- **THEN** a página mostra as hipóteses iniciais e "validando as hipóteses"

### Requirement: Página do incidente é o war room
A página do incidente SHALL mostrar um cabeçalho com estado, severidade, duração, IC e TTA, TTM e TTR. Abaixo do cabeçalho, SHALL mostrar as ações permitidas no estado atual, o chat com o agente, a análise, a validação, os alertas e a timeline com autor.

O war room SHALL pedir um nome na primeira visita e guardá-lo num cookie, que identifica o autor das ações e mensagens.

O chat e a timeline SHALL se atualizar sozinhos a cada poucos segundos, sem recarregar a página.

#### Scenario: Primeira visita
- **WHEN** alguém abre o war room sem ter informado o nome
- **THEN** a página pede o nome antes de permitir ações e mensagens

#### Scenario: Resposta do agente aparece sozinha
- **WHEN** o agente responde uma pergunta enquanto a página está aberta
- **THEN** a resposta aparece no chat em poucos segundos, sem recarregar a página

### Requirement: War room fácil de conduzir
O war room SHALL deixar claro, num relance, o estado do incidente e o próximo passo:
- a etapa atual dentro do ciclo Detectado → Em tratamento → Mitigado → Resolvido;
- a ação principal do estado em destaque (assumir, marcar mitigado ou encerrar);
- as outras ações a um clique, com o motivo pedido no próprio lugar.

O descarte de uma hipótese SHALL ficar junto da hipótese na análise, e a hipótese descartada SHALL aparecer como descartada, com autor e motivo.

A página SHALL funcionar no celular e em tema escuro, e SHALL ter a identidade do Grolla, o mascote do agente.

#### Scenario: Próximo passo em destaque
- **WHEN** alguém abre um incidente em tratamento
- **THEN** a etapa "Em tratamento" aparece marcada no ciclo e "Marcar mitigado" é a ação em destaque

#### Scenario: Hipótese descartada
- **WHEN** "Ana" descarta a hipótese "Adquirente lenta" com o motivo "latência normal"
- **THEN** a hipótese aparece na análise como descartada por Ana, com o motivo

