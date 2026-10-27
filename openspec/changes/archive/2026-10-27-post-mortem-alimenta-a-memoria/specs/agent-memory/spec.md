## ADDED Requirements

### Requirement: Post mortem aprovado alimenta a memória
Quando um post mortem é aprovado, o aprendizado do incidente SHALL rodar de novo, com o texto do post mortem aprovado (fatores contribuintes, lições e plano de ação) como fonte. O episódio do incidente SHALL ser reescrito com confiança `confirmado` e a pessoa que aprovou como fonte, e os dossiês dos serviços e integrações SHALL incorporar o que o post mortem ensinou.

#### Scenario: Aprovar o post mortem
- **WHEN** Ana aprova o post mortem de um incidente do `pedidos-api`
- **THEN** em até 1 min o episódio do incidente fica confirmado com Ana como fonte, e o dossiê do `pedidos-api` ganha uma versão com o motivo "post mortem aprovado"
