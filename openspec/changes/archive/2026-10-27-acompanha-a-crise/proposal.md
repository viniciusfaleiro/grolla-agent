## Why

Depois da análise inicial e da validação, o Grolla para de olhar o incidente: ninguém informa o
canal se piorou, melhorou ou estabilizou, o impacto não aparece, e o time precisa lembrar sozinho
de mitigar e encerrar. Num P0/P1 de adquirência, isso custa atenção de quem deveria estar
resolvendo (spec de produto, "Agente acompanha a crise"; roadmap S5).

## What Changes

- **Acompanhamento de P0/P1.** Enquanto o incidente está aberto e depois da análise inicial, o
  worker roda um ciclo a cada 5 min (configurável). Em cada ciclo, o modelo, com as mesmas
  ferramentas e a memória, diz a tendência (piorando, estável, melhorando ou normalizado), a taxa
  de erro atual e o que mudou.
- **Update só quando muda.** O card "Atualização" vai para o canal quando a tendência muda ou há
  novidade. Sem mudança, sai um card curto a cada 15 min. Todo ciclo fica na timeline.
- **Gatilhos.** Um alerta agrupado no incidente ou uma mensagem do time no chat antecipa o
  próximo ciclo.
- **Propostas com confirmação humana.** Com o serviço normalizado por 15 min, o agente propõe
  "marcar como mitigado" (em tratamento) ou "encerrar" (mitigado), no card e no war room. Quem
  decide é a pessoa, pelos botões que já existem.
- **Mitigado volta para Em tratamento** quando um alerta do incidente volta a disparar. É uma regra
  determinística do núcleo, não do modelo.
- **Impacto financeiro estimado** por serviço, a partir de um valor por minuto no YAML e da taxa
  de erro do ciclo, na página e no card.
- **Limites.** Teto de ciclos e de tokens por incidente e de tokens por ciclo.

## Capabilities

### New Capabilities

- `crisis-follow-up`: ciclos de acompanhamento, updates, gatilhos, propostas e impacto
  financeiro.

### Modified Capabilities

- `war-room-actions`: alerta que volta a disparar num incidente mitigado o devolve a Em
  tratamento.
- `team-config`: seções `follow_up` e `impact`.

## Non-goals

- **Acionamento do on-call** (S4): depende de conta no Spike.sh e fica para depois.
- **Agente mudando estado por julgamento próprio.** O modelo só propõe; a volta para Em
  tratamento é regra determinística sobre alertas.
- **Ciclos para P2 e P3.** Só P0 e P1, configurável.
- **Impacto financeiro calculado a partir de transações reais.** A fonte é um valor por minuto
  configurado; transações reais ficam para quando houver métrica de negócio.
- **Novas ferramentas de investigação.** O ciclo usa as que já existem.

## Impact

- `grolla-core`:
  - domínio do acompanhamento (tendência, decisão de postar, propostas, impacto e a volta para Em
    tratamento no alerta);
  - casos de uso `run_next_cycle`, `reap_stuck_cycles` e `schedule_cycles`;
  - port `CycleQueue`, com o adapter Postgres e a migração 0008;
  - `LanguageModel.follow_up` (Anthropic);
  - cards novos no Teams.
- `grolla-worker`: jobs dos ciclos.
- `grolla-api`: ciclos, impacto e proposta no war room.
- `e2e/fakes`: o LLM fake responde a etapa `follow_up`.
- Custo: até cerca de 30 mil tokens por ciclo, só em P0/P1 abertos, com teto por incidente.
