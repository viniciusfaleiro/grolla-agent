## ADDED Requirements

### Requirement: Mitigado volta para Em tratamento quando o alerta volta
Quando um alerta disparando entra num incidente mitigado, o núcleo SHALL devolver o incidente ao estado em tratamento, com o evento de mudança de estado motivado por "alerta voltou a disparar" e o card no canal. O IC e os horários de TTA e TTM SHALL ser mantidos.

#### Scenario: Erro volta depois de mitigar
- **WHEN** o IC marcou um incidente como mitigado e o alerta do serviço volta a disparar 8 min depois
- **THEN** o incidente volta a em tratamento, a timeline mostra "alerta voltou a disparar" e o canal recebe o card
