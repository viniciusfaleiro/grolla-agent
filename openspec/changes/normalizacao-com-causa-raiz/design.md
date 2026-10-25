## Context

A validação (S3.1) grava a causa raiz em `verifications`. A normalização acontece em `receive_alerts`, quando `decide_resolved` vê o último alerta disparando resolver. O `Notification` já tem o campo `verification`, e a outbox já o serializa.

## Goals / Non-Goals

**Goals:** o card de normalização traz duração, causa raiz e plano, sem chamada nova ao LLM.

**Non-Goals:** ver a proposta.

## Contratos

- **`decide_resolved`**: o resumo do card de normalização passa a ser "Normalizado: durou N min", de `detected_at` até `now`. O formato é `N min`, ou `H h M min` a partir de 60 minutos.
- **`receive_alerts`**: quando a decisão traz um `INCIDENT_NORMALIZED`, lê `repository.verification_of(incident.id)` e anexa o resultado ao `Notification` com `dataclasses.replace(notification, verification=...)` antes do `enqueue`.
- **Card do Teams**:
  - o `INCIDENT_NORMALIZED` ganha as seções "Causa raiz" e "Plano de ação", quando a validação foi concluída com causa;
  - ganha "Causa raiz não confirmada" com o que faltou, quando foi concluída sem causa;
  - ganha "Validação em andamento: a causa raiz aparecerá na página do incidente", quando está pendente ou em andamento;
  - não ganha nenhuma dessas seções quando não há validação;
  - mantém o limite de tamanho do card.

Nenhum port novo e nenhuma migração.

## Decisions

1. **Ler a validação no caso de uso, não no domínio.**
   - *Mais simples considerada:* que o `decide_resolved` recebesse a validação. Descartada porque o domínio receberia um dado que só serve ao card.
   - O caso de uso já tem o repositório e a transação, e anexar é uma linha.
2. **Reaproveitar `Notification.verification`.** Sem campo novo, e a outbox e o card já sabem lidar com ele.
3. **Duração calculada no domínio**, para ser testável com `FakeClock`.

## Risks / Trade-offs

- [A validação termina depois da normalização] → o card diz que a causa aparece na página. O card "Causa raiz" ainda sai depois, pela fila da validação.
