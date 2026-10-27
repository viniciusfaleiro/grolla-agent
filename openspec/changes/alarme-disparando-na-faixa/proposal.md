## Why

No teste com o usuário, o alarme do estoque estava disparando e o topo do war room não dizia nada:
a faixa só aparecia depois da normalização. A situação do alarme ficava na tabela "Alertas", no
fim da página. Quem conduz o incidente precisa ver sempre, no topo, se o alarme está disparando,
desde quando, quais alertas são e o link de cada um.

## What Changes

- **Faixa também enquanto dispara.** "🔴 Alarme disparando desde 17:35", com os alertas que
  disparam (regra e link "abrir o alerta" para a origem). Fica vermelha e pisca levemente
  enquanto dispara (sem animação com "reduzir movimento" ligado).
- **Faixa ao lado do título**, à direita (abaixo no celular), **sem botão**: as ações ficam no
  painel de ações, logo abaixo.
- **Normalizado** continua com o horário e o "há X min".
- **Voltou a disparar** passa a listar também os alertas que disparam, com o link.
- Aviso na tela e título piscando continuam só para normalizado e voltou a disparar. Abrir a página
  com o alarme disparando não avisa.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `incident-pages`: a faixa do alarme passa a ter o estado "disparando", com os alertas e os links.

## Non-goals

- Aviso na tela quando o alarme começa a disparar. Quem abre o war room já sabe que há alarme, e o
  card de abertura vai para o canal do time.
- Mudar a tabela "Alertas", que continua no fim da página com o histórico.

## Impact

- `grolla-api`: `alarm_of` ganha o estado `disparando`; o template da faixa, o CSS e o cenário e2e
  `alarme_normalizado.yaml`.
