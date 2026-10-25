# incident-pages Specification

## Purpose

Dar ao time uma página por incidente e uma lista de incidentes, só leitura, na rede interna.

## Requirements
### Requirement: Lista de incidentes
O `grolla-api` SHALL servir `GET /incidents`, uma página HTML com os incidentes do mais recente para o mais antigo, mostrando título, severidade, estado, serviços e horário de detecção, cada um com link para a página dele.

#### Scenario: Dois incidentes
- **WHEN** existem dois incidentes
- **THEN** a página lista os dois, o mais recente primeiro

### Requirement: Página do incidente
O `grolla-api` SHALL servir `GET /incidents/{id}`, uma página HTML com título, severidade, estado, serviços, horário de detecção, os alertas (status, regra, resumo, link para a origem) e a timeline de eventos em ordem. Incidente inexistente SHALL responder 404.

#### Scenario: Timeline visível
- **WHEN** a página de um incidente com dois alertas e uma escalada é aberta
- **THEN** ela mostra os dois alertas e os eventos de detecção, agrupamento e mudança de severidade em ordem

#### Scenario: Incidente inexistente
- **WHEN** `GET /incidents/<id que não existe>` é chamado
- **THEN** a resposta é 404

