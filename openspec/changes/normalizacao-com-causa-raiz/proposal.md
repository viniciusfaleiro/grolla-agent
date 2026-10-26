## Why

Quando os alertas de um incidente resolvem, o canal recebe o card "Incidente normalizado" só com título, severidade, serviços e o resumo do alerta. A causa raiz que o agente confirmou na validação fica num card anterior e na página do incidente. O usuário quer que o aviso de normalização feche a história do incidente, trazendo a causa raiz.

## What Changes

- O card **"Incidente normalizado"** passa a trazer:
  - a **duração** do incidente, da detecção até a normalização;
  - a **causa raiz** confirmada, ou "causa raiz não confirmada" com o que faltou, ou "validação ainda em andamento", conforme o estado da validação;
  - o **plano de ação** que tinha sido sugerido;
  - o botão **Abrir incidente**.
- O texto da duração é decidido no domínio, no momento da normalização. A validação é lida pelo caso de uso `receive_alerts` e anexada à notificação, que já aceita `verification`.

## Non-goals

- **Encerramento humano do incidente** (war room): fica no S4. Aqui é só a normalização automática pelos alertas.
- **Novo resumo do LLM na normalização**: o card reaproveita o que a validação já produziu, sem nova chamada ao modelo.
- **Atualizar cards anteriores**: o Workflow do Teams só posta mensagens novas.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `incident-notifications`: o card de normalização traz duração, causa raiz e plano.

## Impact

- **Código**: `decide_resolved` (texto da duração), `receive_alerts` (anexa a validação), card de normalização no adapter do Teams, cenário e2e.
- **Dependências e schema**: nenhuma dependência nova e nenhuma migração. A outbox já serializa `Notification.verification`.
