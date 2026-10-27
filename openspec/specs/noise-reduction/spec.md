# noise-reduction Specification

## Purpose
TBD - created by archiving change menos-ruido. Update Purpose after archive.
## Requirements
### Requirement: Triagem de todo incidente novo
Com `noise` no YAML, todo incidente aberto SHALL ganhar um pedido de triagem na mesma transação. O worker SHALL fazer a triagem:
- sem chamar o modelo, quando não há outro incidente aberto de outro serviço na janela de `noise.window_minutes` e a severidade veio de uma regra;
- caso contrário, numa chamada sem ferramentas ao modelo, com os alertas do incidente, os outros incidentes abertos na janela (título, serviços, alertas e análise) e a memória (dossiês e integrações), como dado.

Falha do modelo SHALL ser registrada e SHALL NOT afetar o incidente.

#### Scenario: Incidente sozinho e classificado por regra
- **WHEN** abre um incidente P1 por regra e não há outro aberto na janela
- **THEN** a triagem termina sem chamar o modelo

### Requirement: Sugestão de junção
Quando o modelo diz que o incidente novo é o mesmo problema de outro aberto, a triagem SHALL registrar o evento de sugestão (com o outro incidente e o motivo) e enviar o card "Parece o mesmo problema que <outro>", e o war room do incidente novo SHALL mostrar a sugestão com o botão "Juntar" (o novo é juntado no outro). O agente SHALL NOT juntar.

#### Scenario: Banco caiu para dois serviços
- **WHEN** `pedidos-api` e `captura-api` abrem incidentes com 2 min de diferença e o dossiê diz que os dois usam `grolla-lab-db`
- **THEN** o incidente da `captura-api` mostra "Parece o mesmo problema que <incidente do pedidos-api>" com o motivo, e o canal recebe o card

### Requirement: Severidade sugerida quando nenhuma regra casa
Quando o incidente abriu pela severidade padrão (nenhuma regra casou), o modelo SHALL sugerir a severidade com o motivo, preferindo a mais alta na dúvida. A sugestão SHALL ser aplicada só se for mais alta que a atual, com o evento de mudança de severidade com o motivo "sugerida pelo agente: <motivo>" e o card de severidade elevada. A sugestão SHALL NOT baixar a severidade.

#### Scenario: Alerta sem regra parece grave
- **WHEN** um alerta sem regra abre um P3 e o modelo sugere P1 porque o serviço é o autorizador
- **THEN** o incidente passa a P1 com "sugerida pelo agente: …" na timeline

#### Scenario: Severidade de regra não muda
- **WHEN** um incidente abriu P2 por uma regra e o modelo sugere P3
- **THEN** a severidade continua P2: a sugestão só vale quando nenhuma regra casou, e nunca baixa

