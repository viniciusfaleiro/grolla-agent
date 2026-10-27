## Why

O post mortem aprovado é o registro mais confiável do que aconteceu, mas a memória do Grolla ainda aprende só com o log no encerramento. Também não há como saber se o agente está acertando mais com o tempo, nem se o "já vi isso" ajudou (roadmap S7).

## What Changes

- **Post mortem aprovado alimenta a memória.** Ao aprovar, o aprendizado do incidente roda de novo com o post mortem como fonte. O episódio é reescrito, fica **confirmado** (aprovado por uma pessoa) e os dossiês dos serviços e integrações incorporam os fatores contribuintes e o plano.
- **"Era o mesmo problema?"** Na análise com "já vi isso", o time responde "era o mesmo" ou "não era". A resposta vira evento e conta nas métricas.
- **Avaliação do agente estruturada no post mortem.** Além do texto, três campos que o time preenche:
  - a hipótese principal estava certa (sim/não);
  - minutos até a hipótese certa;
  - se a memória ajudou (sim/não).
- **Página de métricas do agente** (`/metricas`), por semana ou mês:
  - incidentes resolvidos e post mortems aprovados;
  - acerto da hipótese principal;
  - mediana do tempo até a hipótese certa;
  - "já vi isso" confirmados e recusados;
  - quantas vezes a memória ajudou.

## Capabilities

### New Capabilities
- `agent-metrics`: confirmação do "já vi isso", avaliação estruturada e a página de métricas.

### Modified Capabilities
- `agent-memory`: o post mortem aprovado é fonte do aprendizado.
- `post-mortem`: campos de avaliação do agente.

## Non-goals

- **Métrica calculada pelo LLM.** Todas as métricas saem de respostas humanas e do log.
- **Comparação automática de hipótese com causa.** A pessoa diz se a hipótese principal estava certa.
- **Painel de custo de LLM** (fase 2).

## Impact

- `grolla-core`:
  - domínio: avaliação, confirmação e agregação;
  - casos de uso: confirmar "já vi isso", avaliar, métricas e aprendizado a partir do post mortem;
  - Postgres: migração 0010 e consultas de métricas.
- `grolla-api`: botões de confirmação, campos de avaliação e `/metricas`.
- `e2e`: cenários de confirmação, avaliação e métricas.
- Custo: uma chamada de aprendizado por post mortem aprovado.
