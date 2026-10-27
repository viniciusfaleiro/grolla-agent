## ADDED Requirements

### Requirement: Campos de avaliação no post mortem
A página do post mortem SHALL mostrar e permitir editar os campos de avaliação do agente (hipótese principal certa, minutos até a hipótese certa e memória ajudou) junto da seção "Avaliação do agente", e o PDF SHALL trazê-los.

#### Scenario: PDF com avaliação
- **WHEN** um post mortem aprovado tem a avaliação preenchida
- **THEN** o PDF mostra "Hipótese principal certa: sim", os minutos e "Memória ajudou: sim"
