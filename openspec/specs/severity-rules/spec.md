# severity-rules Specification

## Purpose

Decidir a severidade de forma determinística, pelas regras do time, e só deixá-la subir automaticamente.

## Requirements
### Requirement: Severidade decidida por regras do time
A severidade de cada alerta SHALL ser decidida pela primeira regra do YAML do time cujos pares `match` estão todos nos labels do alerta. Sem regra que case, SHALL valer a severidade padrão configurada. A decisão SHALL registrar qual regra casou, ou que foi a padrão.

#### Scenario: Primeira regra que casa vence
- **WHEN** as regras são `{alertname: "Queda de aprovação"} → P0` e `{severity: critical} → P1`, e chega um alerta com os dois labels
- **THEN** a severidade do alerta é P0 e o evento registra a primeira regra

#### Scenario: Nenhuma regra casa
- **WHEN** chega um alerta que não casa com nenhuma regra e a padrão é P3
- **THEN** a severidade é P3 e o evento registra "severidade padrão"

### Requirement: Pior severidade vence e só sobe sozinha
A severidade de um incidente SHALL ser a pior entre a atual e a do alerta que entra (P0 é a pior). A severidade SHALL subir automaticamente e SHALL NOT descer automaticamente.

#### Scenario: Alerta mais grave entra
- **WHEN** um incidente P2 recebe um alerta P1
- **THEN** o incidente passa a P1 e grava o evento de mudança de severidade

#### Scenario: Alerta mais leve entra
- **WHEN** um incidente P1 recebe um alerta P3
- **THEN** o incidente continua P1

