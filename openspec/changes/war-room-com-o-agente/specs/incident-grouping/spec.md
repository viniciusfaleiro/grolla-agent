## ADDED Requirements

### Requirement: Incidente encerrado não recebe alertas novos
Um alerta disparando SHALL NOT entrar num incidente Resolvido ou Descartado. Ele SHALL abrir um incidente novo, ou entrar noutro incidente ativo do mesmo serviço.

#### Scenario: Alerta depois do encerramento
- **WHEN** um incidente do `pedidos-api` foi encerrado há 2 minutos e chega um alerta novo do `pedidos-api`
- **THEN** um incidente novo é aberto
