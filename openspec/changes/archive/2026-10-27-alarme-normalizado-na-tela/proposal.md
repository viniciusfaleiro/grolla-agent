## Why

No laboratório, os alertas do incidente pararam de disparar e ninguém no war room percebeu. A
única pista na página era uma linha "Normalizado" no fim da timeline. A tabela de alertas e o
cabeçalho só mudavam recarregando a página, e o card "Incidente normalizado" ia só para o canal do
time. Para quem conduz o incidente, saber que o alarme parou é o sinal para mitigar ou encerrar.
Saber que ele voltou a disparar é o sinal de que a mitigação não segurou. Os dois precisam chamar a
atenção na hora (`docs/initial-spec.md`, war room e ciclo do incidente).

## What Changes

- **Faixa do alarme no topo do war room**, atualizada sozinha:
  - **"Alertas normalizados às 15:03 · há 2 min"**, quando nenhum alerta do incidente aberto está
    disparando. Traz a ação em destaque do estado: **Assumir como IC** (detectado), **Marcar mitigado** (em
    tratamento) ou **Encerrar** (mitigado, abre o pedido de motivo que já existe).
  - **"Voltou a disparar às 15:10"**, quando um alerta dispara de novo depois da normalização.
  - Some quando o incidente é encerrado ou descartado, e não aparece em incidente que nunca teve
    alerta.
- **Aviso na transição.** Quando a faixa muda com a página aberta, aparece um aviso na tela
  (toast), uma vez só. O título da aba pisca ("✅ Normalizado · <título>" ou "⚠️ Voltou a
  disparar · <título>") até a pessoa voltar para a aba. Ao abrir a página, não há aviso, só a
  faixa.
- **Alertas e estado ao vivo.** A tabela de alertas e o bloco de estado do cabeçalho (estado,
  etapas, IC, duração, TTA, TTM, TTR) se atualizam no mesmo ciclo da timeline, sem recarregar a
  página e sem apagar o que a pessoa está digitando.
- **Selo na lista.** Na lista de incidentes, um incidente aberto cujos alertas pararam mostra o selo
  **alarme normalizado**, para achar quem falta mitigar ou encerrar.
- O Grolla continua sem mudar o estado sozinho: a faixa só sugere, e quem clica é a pessoa.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `incident-pages`: faixa do alarme, aviso na transição, alertas e estado ao vivo no war room, e
  selo na lista de incidentes.

## Non-goals

- **Notificação do navegador** (Notification API), com o navegador minimizado. Ela pede permissão
  e configuração por pessoa, e fica para quando o time sentir falta. O título piscando já avisa
  quem está com a aba em segundo plano.
- **Som.**
- **Encerrar ou mitigar sozinho**, por exemplo um P3 normalizado há X minutos. É decisão do produto,
  numa change própria.
- **Atualização ao vivo da lista de incidentes.** O selo aparece ao abrir ou recarregar a lista.

## Impact

- `grolla-api`: `war_room.py` (estado do alarme a partir do incidente e dos eventos), as rotas da
  página e da timeline (os fragmentos fora do lugar), os templates `_war_room.html`,
  `incident.html`, `_timeline.html` e `incidents.html`, além de `war-room.js` e `war-room.css`.
- `e2e/scenarios`: o cenário de normalização confere a faixa e o selo.
- Sem mudança no core: sem port, evento, migração nem card novo.
