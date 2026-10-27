# incident-grouping Specification

## Purpose

Transformar alertas em incidentes: agrupamento determinístico por serviço, estados do ciclo de vida e o log de eventos imutável de onde o incidente é projetado.
## Requirements
### Requirement: Alerta do mesmo serviço entra no incidente ativo
Um alerta disparando SHALL entrar num incidente existente quando o incidente tem o mesmo serviço do alerta e teve atividade de alerta nos últimos 15 minutos (janela configurável) ou ainda tem algum alerta disparando. Havendo mais de um candidato, SHALL entrar no de atividade mais recente. Sem candidato, SHALL abrir um incidente novo. O motivo do agrupamento SHALL ficar registrado no evento.

#### Scenario: Dois alertas do mesmo serviço
- **WHEN** chegam, com 5 minutos de diferença, dois alertas diferentes do serviço `authorizer-api`
- **THEN** existe um incidente só, com os dois alertas e o motivo "mesmo serviço"

#### Scenario: Serviço diferente
- **WHEN** chega um alerta do `capture-api` enquanto há um incidente ativo do `authorizer-api`
- **THEN** um incidente novo é aberto para o `capture-api`

#### Scenario: Fora da janela
- **WHEN** chega um alerta do `authorizer-api` 20 minutos depois da última atividade de um incidente do mesmo serviço cujos alertas já resolveram
- **THEN** um incidente novo é aberto

### Requirement: Estados do incidente no S2
Todo incidente SHALL nascer em **Detectado**. Com severidade P2 ou P3, SHALL passar a **Informado** na mesma decisão. Com P0 ou P1, SHALL permanecer em Detectado. Um incidente Informado que escala para P0/P1 SHALL voltar a Detectado. Cada transição SHALL gravar um evento com o horário do `Clock`.

#### Scenario: Incidente P3
- **WHEN** um alerta abre um incidente de severidade P3
- **THEN** o incidente fica em Informado, com os eventos de detecção e de transição

#### Scenario: Escalonamento de um informado
- **WHEN** um incidente Informado P3 recebe um alerta P1
- **THEN** o incidente passa a P1 e volta para Detectado

### Requirement: Alertas resolvidos são registrados
Um alerta resolvido SHALL ser registrado no incidente que contém aquele fingerprint. Quando todos os alertas do incidente estiverem resolvidos, o incidente SHALL registrar que normalizou. Um alerta resolvido sem incidente correspondente SHALL ser ignorado.

#### Scenario: Último alerta resolve
- **WHEN** o único alerta disparando de um incidente chega como resolvido
- **THEN** o incidente registra o evento de normalização

### Requirement: Incidente é projeção do log de eventos
Todo fato sobre um incidente (alerta recebido, agrupado, resolvido, mudança de severidade, mudança de estado, normalização, card enfileirado e card entregue) SHALL ser gravado como evento imutável, com horário, tipo, autor (`sistema`) e conteúdo. O estado atual do incidente SHALL ser atualizado na mesma transação do evento.

#### Scenario: Timeline reconstruída
- **WHEN** um incidente recebeu dois alertas e escalou de severidade
- **THEN** a lista de eventos do incidente mostra, em ordem, a detecção, o agrupamento e a mudança de severidade

### Requirement: Incidente encerrado não recebe alertas novos
Um alerta disparando SHALL NOT entrar num incidente Resolvido ou Descartado. Ele SHALL abrir um incidente novo, ou entrar noutro incidente ativo do mesmo serviço.

#### Scenario: Alerta depois do encerramento
- **WHEN** um incidente do `pedidos-api` foi encerrado há 2 minutos e chega um alerta novo do `pedidos-api`
- **THEN** um incidente novo é aberto

### Requirement: Incidente aberto por uma pessoa
O núcleo SHALL aceitar a abertura manual de um incidente com título, serviço, severidade e autor. O incidente SHALL nascer detectado (ou informado, pela severidade, como no alerta), sem alerta disparando, com o evento de abertura manual com o autor e a origem, e com o pedido de análise inicial. Título, serviço ou autor vazios SHALL ser recusados.

#### Scenario: Abrir manualmente
- **WHEN** Ana abre o incidente "pedidos-api lento", serviço `pedidos-api`, P2
- **THEN** o incidente aparece na lista com Ana como autora da abertura e a análise inicial pedida

