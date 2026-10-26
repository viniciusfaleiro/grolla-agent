## ADDED Requirements

### Requirement: Normalização fecha a história do incidente
O card "Incidente normalizado" SHALL trazer a duração do incidente, da detecção até a normalização, e o desfecho da investigação, conforme o estado da validação:
- **confirmada**: a causa raiz e o plano de ação que foi sugerido;
- **sem confirmação**: "causa raiz não confirmada" e o que faltou;
- **ainda em andamento**: aviso de que a causa raiz aparecerá na página do incidente;
- **sem validação**: o card SHALL omitir a seção.

O card SHALL manter o botão Abrir incidente.

#### Scenario: Normalizou depois da causa raiz
- **WHEN** um incidente com causa raiz confirmada tem o último alerta resolvido 32 minutos depois da detecção
- **THEN** o card "Incidente normalizado" diz "durou 32 min" e traz a causa raiz e o plano de ação

#### Scenario: Normalizou durante a validação
- **WHEN** o último alerta resolve enquanto a validação ainda roda
- **THEN** o card diz que a validação está em andamento e que a causa raiz aparecerá na página do incidente
