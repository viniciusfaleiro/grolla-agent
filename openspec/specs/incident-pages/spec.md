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
A página do incidente SHALL mostrar cada hipótese da análise inicial uma vez, com o veredito da validação de mesmo título (confirmada, rejeitada, inconclusiva, ou "validando" enquanto a validação roda), a confiança, o motivo (a justificativa do veredito, ou a primeira evidência) e as evidências recolhidas. Um veredito sem hipótese correspondente SHALL aparecer no fim da lista. A análise e a validação aparecem como em andamento, concluídas ou parciais com o motivo, e as consultas das duas ficam juntas.

#### Scenario: Validação em andamento
- **WHEN** a análise inicial terminou e a validação ainda roda
- **THEN** a página mostra as hipóteses iniciais como "validando" e o cartão "Agora" mostra "validando as hipóteses"

### Requirement: Página do incidente é o war room
A página do incidente SHALL ter uma barra do incidente no topo, fixa ao rolar no computador, com a severidade, o título, os serviços, a pílula do alarme, o estado, o ciclo, o IC, a duração e TTA, TTM e TTR, e o botão do próximo passo. Abaixo, nesta ordem de leitura:
1. o cartão "Agora": a causa raiz confirmada em destaque ou, sem ela, a hipótese mais provável com a confiança, "validando as hipóteses", "o Grolla está investigando" ou "causa raiz não confirmada" com o que faltou; os números do incidente (desde quando, serviços afetados, a taxa de erro do último ciclo de acompanhamento quando houver, o impacto quando houver); o primeiro passo do plano em destaque, com o comando para copiar quando a ação traz um, e os outros passos recolhidos; e as sugestões do Grolla (juntar com outro incidente, já vi isso);
2. "Como o Grolla chegou lá": as hipóteses com o veredito, as evidências e as consultas;
3. a linha do tempo, com o registro do que a pessoa fez no topo;
4. os alertas.

O chat com o agente SHALL ficar ao lado, acompanhando a rolagem no computador.

O war room SHALL pedir um nome na primeira visita e guardá-lo num cookie, que identifica o autor das ações e mensagens.

O chat e a timeline SHALL se atualizar sozinhos a cada poucos segundos, sem recarregar a página, sem apagar o que a pessoa está digitando nos formulários.

A linha do tempo SHALL poder ser filtrada por pessoas, alertas e Grolla, e os eventos de sistema (cards enviados, pedidos de análise e de validação) SHALL ficar recolhidos atrás de "mostrar N eventos do sistema".

#### Scenario: Primeira visita
- **WHEN** alguém abre o war room sem ter informado o nome
- **THEN** a página pede o nome antes de permitir ações e mensagens

#### Scenario: Resposta do agente aparece sozinha
- **WHEN** o agente responde uma pergunta enquanto a página está aberta
- **THEN** a resposta aparece no chat em poucos segundos, sem recarregar a página

#### Scenario: Alerta resolvido aparece sozinho
- **WHEN** um alerta do incidente resolve enquanto a página está aberta
- **THEN** em poucos segundos a lista de alertas mostra o alerta como resolvido, sem recarregar a página

#### Scenario: Agora antes da investigação
- **WHEN** alguém abre um incidente com causa raiz confirmada e plano de ação
- **THEN** o cartão "Agora" mostra a causa uma vez só, acima de "Como o Grolla chegou lá", com "Comece por aqui" e o primeiro passo

#### Scenario: Eventos de sistema recolhidos
- **WHEN** a linha do tempo tem 6 eventos de cards enviados e pedidos de análise
- **THEN** eles ficam escondidos atrás de "Mostrar 6 eventos do sistema"

### Requirement: War room fácil de conduzir
O war room SHALL deixar claro, num relance, o estado do incidente e o próximo passo:
- a etapa atual dentro do ciclo Detectado → Em tratamento → Mitigado → Resolvido;
- um botão só com a ação principal do estado (assumir, marcar mitigado ou encerrar);
- as outras ações (assumir no lugar de alguém, reabrir, severidade, juntar com outro incidente, enviar transcrição, descartar) num menu "Mais ações" a um clique, com o motivo pedido no próprio lugar.

O descarte de uma hipótese SHALL ficar junto da hipótese, e a hipótese descartada SHALL aparecer como descartada, com autor e motivo.

A página SHALL funcionar no celular, com atalhos para as seções e o botão do próximo passo fixo embaixo da tela, e em tema escuro, e SHALL ter a identidade do Grolla, o mascote do agente.

#### Scenario: Próximo passo em destaque
- **WHEN** alguém abre um incidente em tratamento
- **THEN** a etapa "Em tratamento" aparece marcada no ciclo e "Marcar mitigado" é o único botão em destaque, com as outras ações em "Mais ações"

#### Scenario: Hipótese descartada
- **WHEN** "Ana" descarta a hipótese "Adquirente lenta" com o motivo "latência normal"
- **THEN** a hipótese aparece como descartada por Ana, com o motivo

### Requirement: Faixa do alarme no war room
O war room de um incidente aberto que já teve alerta SHALL mostrar, na barra do incidente, à direita do título, uma pílula com a situação do alarme:
- **disparando**: há alerta disparando e o alarme nunca normalizou. A pílula diz "Alarme disparando desde HH:MM", com o horário do primeiro alerta, e quantos alertas disparam. Ela SHALL pulsar levemente enquanto dispara, sem animação para quem pede menos movimento no sistema;
- **normalizado**: nenhum alerta do incidente está disparando. A pílula diz "Alertas normalizados às HH:MM", com o horário da última normalização e há quanto tempo foi;
- **voltou a disparar**: um alerta disparou depois de uma normalização. A pílula diz "Voltou a disparar às HH:MM", com o horário do alerta, e quantos alertas disparam.

A pílula SHALL levar para a lista de alertas do incidente, em que cada alerta aparece uma vez, os que disparam primeiro, com a regra, o resumo, o início e o link "abrir o alerta" para a origem. A pílula SHALL NOT ter botão: o próximo passo fica ao lado dela, na barra. Incidente encerrado ou descartado, ou que nunca teve alerta, SHALL NOT ter pílula. O estado do incidente SHALL NOT mudar sem uma pessoa.

A barra do incidente (pílula, estado, ciclo, IC, duração, TTA, TTM, TTR, o próximo passo e o menu das outras ações) e a lista de alertas SHALL se atualizar sozinhas no mesmo ciclo da timeline, para o estado e os botões não ficarem incoerentes quando o alarme reabre o incidente. Quando a pílula muda para normalizado ou voltou a disparar com a página aberta, a página SHALL mostrar um aviso na tela uma vez e piscar o título da aba ("✅ Normalizado · <título>" ou "⚠️ Voltou a disparar · <título>") até a pessoa voltar para a aba. Ao abrir a página, SHALL NOT haver aviso.

#### Scenario: Alarme disparando
- **WHEN** alguém abre um incidente cujo alerta "Latência alta na estoque-api" dispara desde 17:35
- **THEN** a barra mostra "Alarme disparando desde 17:35 · 1 alerta" e a lista de alertas mostra "Latência alta na estoque-api" com o link "abrir o alerta"

#### Scenario: Alarme normaliza com a página aberta
- **WHEN** o último alerta disparando de um incidente em tratamento resolve às 15:03 e o war room está aberto
- **THEN** em poucos segundos a pílula passa a "Alertas normalizados às 15:03", com o aviso na tela e o título da aba piscando

#### Scenario: Alarme reabre o incidente mitigado
- **WHEN** o war room de um incidente mitigado está aberto e um alerta dispara de novo
- **THEN** em poucos segundos o estado passa a "Em tratamento", a pílula a "Voltou a disparar" e o botão do próximo passo a "Marcar mitigado", sem recarregar a página

#### Scenario: Incidente encerrado
- **WHEN** alguém abre um incidente resolvido
- **THEN** não há pílula do alarme

