## Context

`GET /incidents` lê os 200 incidentes mais recentes (`list_incidents`) e mostra título, severidade,
estado, serviços, IC e "há quanto tempo", com o selo "alarme normalizado".

## Goals / Non-Goals

**Goals:** ver o que precisa de alguém agora, a causa de cada incidente e filtrar.

**Non-Goals:** lista ao vivo, paginação real.

## Decisions

### 1. Filtro em Python sobre os 200 mais recentes

`war_room.IncidentFilter.from_query(params)` lê a query e `matches(incident, author, now)` decide.
Valor inválido vale como sem filtro, como na lista de post mortems.

- *Alternativa:* filtro em SQL no repositório. Descartada: o limite de 200 já existe, o filtro é
  barato em memória e não muda o port.

### 2. Causa e post mortem só das linhas mostradas

Os cartões abertos e as 20 primeiras linhas encerradas leem a validação e a análise (para a causa) e
o post mortem (para a situação), uma consulta por linha mostrada.

- *Alternativa:* um método novo no repositório que traga tudo junto. Descartada por ora: no máximo
  algumas dezenas de consultas simples; fica registrado como o próximo passo se a lista crescer.

### 3. Contador na navegação

`base.html` recebe `firing_count` de um contexto comum das páginas: incidentes abertos com alerta
disparando, contados da mesma leitura da lista. Nas páginas que não leem a lista, o contador não
aparece.

## Contratos

Sem port, evento ou migração. Query de `GET /incidents`: `sev` (repetível: P0..P3), `estado`
(`abertos`, `encerrados`, `todos`; padrão `abertos` para os cartões e encerrados sempre na tabela),
`servico`, `alarme` (`disparando`, `normalizado`), `ic` (`meus`, `sem`), `periodo` (`24h`, `7d`,
`30d`), `q`, `mais` (mostra todos os encerrados filtrados).

## Risks / Trade-offs

- [Uma consulta por linha mostrada] → Limitado a 20 encerrados mais os abertos.
