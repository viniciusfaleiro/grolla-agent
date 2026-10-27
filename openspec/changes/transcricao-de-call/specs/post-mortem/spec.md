## ADDED Requirements

### Requirement: Itens da transcrição na timeline do post mortem
A timeline do post mortem SHALL incluir os eventos extraídos da transcrição com a origem "transcrição" e o horário dito na call, quando houver, na ordem dos horários.

#### Scenario: Post mortem com a call
- **WHEN** o post mortem de um incidente com transcrição é gerado
- **THEN** a timeline tem "[transcrição]" com a decisão do rollback às 03:12
