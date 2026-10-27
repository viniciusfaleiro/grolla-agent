# incident-pages Specification

## Purpose

Dar ao time uma página por incidente e uma lista de incidentes, só leitura, na rede interna.
## Requirements
### Requirement: Lista de incidentes
O `grolla-api` SHALL servir `GET /incidents`, uma página HTML com os incidentes do mais recente para o mais antigo, mostrando título, severidade, estado, serviços e horário de detecção, cada um com link para a página dele.

Um incidente aberto que teve alerta e não tem nenhum disparando SHALL mostrar o selo "alarme normalizado".

#### Scenario: Dois incidentes
- **WHEN** existem dois incidentes
- **THEN** a página lista os dois, o mais recente primeiro

#### Scenario: Selo de alarme normalizado
- **WHEN** um incidente em tratamento teve o último alerta resolvido e outro incidente ainda dispara
- **THEN** só o primeiro mostra "alarme normalizado"

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

O chat e a timeline SHALL se atualizar sozinhos a cada poucos segundos, sem recarregar a página. Quando chega um evento novo, a tabela de alertas e o bloco de estado do cabeçalho (estado, etapas, IC, duração, TTA, TTM e TTR) SHALL se atualizar junto, sem apagar o que a pessoa está digitando nos formulários.

#### Scenario: Primeira visita
- **WHEN** alguém abre o war room sem ter informado o nome
- **THEN** a página pede o nome antes de permitir ações e mensagens

#### Scenario: Resposta do agente aparece sozinha
- **WHEN** o agente responde uma pergunta enquanto a página está aberta
- **THEN** a resposta aparece no chat em poucos segundos, sem recarregar a página

#### Scenario: Alerta resolvido aparece sozinho
- **WHEN** um alerta do incidente resolve enquanto a página está aberta
- **THEN** em poucos segundos a tabela de alertas mostra o alerta como resolvido, sem recarregar a página

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

### Requirement: Faixa do alarme no war room
O war room de um incidente aberto que já teve alerta SHALL mostrar, no topo, uma faixa com a situação do alarme:
- **disparando**: há alerta disparando e o alarme nunca normalizou. A faixa diz "Alarme disparando desde HH:MM", com o horário do primeiro alerta, e lista cada alerta que dispara com a regra e o link "abrir o alerta" para a origem. A faixa SHALL piscar levemente enquanto dispara, sem animação para quem pede menos movimento no sistema;
- **normalizado**: nenhum alerta do incidente está disparando. A faixa diz "Alertas normalizados às HH:MM", com o horário da última normalização e há quanto tempo foi;
- **voltou a disparar**: um alerta disparou depois de uma normalização. A faixa diz "Voltou a disparar às HH:MM", com o horário do alerta, e lista os alertas que disparam com o link.

A faixa fica à direita do título do incidente (abaixo dele no celular) e SHALL NOT ter botão: as ações ficam no painel de ações. Incidente encerrado ou descartado, ou que nunca teve alerta, SHALL NOT ter faixa. O estado do incidente SHALL NOT mudar sem uma pessoa.

A faixa e os botões do próximo passo (assumir, marcar mitigado, encerrar, reabrir) SHALL se atualizar sozinhos no mesmo ciclo da timeline, para o estado e os botões não ficarem incoerentes quando o alarme reabre o incidente. Cada alerta SHALL aparecer uma vez na faixa, mesmo recebido várias vezes. Quando ela muda para normalizado ou voltou a disparar com a página aberta, a página SHALL mostrar um aviso na tela uma vez e piscar o título da aba ("✅ Normalizado · <título>" ou "⚠️ Voltou a disparar · <título>") até a pessoa voltar para a aba. Ao abrir a página, SHALL NOT haver aviso, só a faixa.

#### Scenario: Alarme disparando
- **WHEN** alguém abre um incidente cujo alerta "Latência alta na estoque-api" dispara desde 17:35
- **THEN** a faixa diz "Alarme disparando desde 17:35" e mostra "Latência alta na estoque-api" com o link "abrir o alerta"

#### Scenario: Alarme normaliza com a página aberta
- **WHEN** o último alerta disparando de um incidente em tratamento resolve às 15:03 e o war room está aberto
- **THEN** em poucos segundos a faixa passa a "Alertas normalizados às 15:03", com o aviso na tela e o título da aba piscando

#### Scenario: Mitigado e normalizado
- **WHEN** alguém abre um incidente mitigado cujos alertas pararam
- **THEN** a faixa "Alertas normalizados" aparece sem botão, "Encerrar" segue no painel de ações, e não há aviso nem título piscando

#### Scenario: Alarme volta
- **WHEN** um alerta do incidente dispara de novo às 15:10, depois de normalizado
- **THEN** a faixa passa a "Voltou a disparar às 15:10", com o alerta e o link, e o aviso na tela

#### Scenario: Incidente encerrado
- **WHEN** alguém abre um incidente resolvido
- **THEN** não há faixa do alarme

#### Scenario: Alarme reabre o incidente mitigado
- **WHEN** o war room de um incidente mitigado está aberto e um alerta dispara de novo
- **THEN** em poucos segundos o estado passa a "Em tratamento", a faixa a "Voltou a disparar" e os botões a "Marcar mitigado", sem recarregar a página

