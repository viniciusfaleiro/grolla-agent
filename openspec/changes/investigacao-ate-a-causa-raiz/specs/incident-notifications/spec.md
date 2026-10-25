## ADDED Requirements

### Requirement: Card da causa raiz
Ao fim da validação, o agente SHALL postar no canal o card da causa raiz, depois do card da análise inicial.

O card SHALL trazer:
- a causa raiz confirmada, ou "causa raiz não confirmada";
- o veredito de cada hipótese, com uma linha de justificativa;
- o plano de ação concreto;
- o botão **Abrir incidente**.

O card da análise inicial SHALL avisar que as hipóteses estão sendo validadas.

#### Scenario: Causa confirmada no canal
- **WHEN** a validação confirma "RDS grolla-lab-db parado manualmente às 21:42"
- **THEN** o canal recebe "Causa raiz · P1" com a causa, as hipóteses confirmada e rejeitada e a ação "religar o RDS grolla-lab-db"
