## Why

A lista de incidentes era uma coluna de títulos com estado e serviço: não dizia o que precisa de
alguém agora (alarme disparando, sem IC, normalizado esperando encerrar, esquecido), nem a causa, e
não tinha filtro. O usuário pediu filtros, e a proposta visual aprovada está no canvas "War room do
Grolla — proposta de redesenho" (`docs/initial-spec.md`, lista de incidentes).

## What Changes

- **"Precisa de você agora"**: os incidentes abertos em cartões, com a pílula do alarme (disparando,
  normalizado há X), a causa raiz (ou a hipótese mais provável) numa linha, o estado, o IC e o
  próximo passo ("Abrir war room", "Juntar ou encerrar", "Encerrar"). O aberto há mais de 24 h sem
  ninguém ganha "esquecido?".
- **Encerrados** numa tabela: severidade, título, causa raiz, duração, situação do post mortem e
  quando encerrou; os 20 mais recentes, com "mostrar mais".
- **Filtros** na URL (`GET /incidents?...`): severidade (várias), estado (abertos, encerrados, todos),
  serviço, alarme (disparando, normalizado), IC (os meus, sem IC), período da detecção (24 h, 7, 30
  dias) e busca por título ou serviço; a contagem ("3 de 12") e "Limpar filtros".
- O contador vermelho de alarmes disparando na aba "Incidentes" da navegação.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `incident-pages`: a lista de incidentes com cartões dos abertos, tabela dos encerrados e filtros.

## Non-goals

- **Lista ao vivo.** A lista atualiza ao recarregar ou ao filtrar.
- **Paginação real.** A lista lê os 200 incidentes mais recentes, como hoje, e filtra sobre eles.

## Impact

- `grolla-api`: rota `GET /incidents`, `incidents.html`, `base.html` (contador), `war_room.py`
  (filtros e cartões) e CSS; testes da página.
- `e2e/scenarios`: um cenário dos filtros.
