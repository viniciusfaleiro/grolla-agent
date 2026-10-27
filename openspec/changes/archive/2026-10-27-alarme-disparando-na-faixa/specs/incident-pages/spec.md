## MODIFIED Requirements

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
