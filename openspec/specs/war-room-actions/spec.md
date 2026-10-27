# war-room-actions Specification

## Purpose
TBD - created by archiving change war-room-com-o-agente. Update Purpose after archive.
## Requirements
### Requirement: Ações humanas viram eventos com autor
O war room SHALL permitir estas ações:
- assumir como IC;
- mudar a severidade;
- registrar uma ação feita (texto livre);
- descartar uma hipótese, com motivo;
- marcar mitigado;
- encerrar com motivo obrigatório;
- descartar o incidente como falso positivo, com motivo obrigatório.

Cada ação SHALL gravar um evento imutável na timeline com o autor, o horário do `Clock` e os dados da ação. Uma ação sem autor SHALL ser recusada.

#### Scenario: Registrar o rollback
- **WHEN** "Ana" registra "fiz rollback do build 20261025.3"
- **THEN** a timeline mostra o evento com o autor "Ana", o horário e o texto

#### Scenario: Encerrar sem motivo
- **WHEN** alguém tenta encerrar o incidente sem motivo
- **THEN** a ação é recusada e o estado não muda

### Requirement: Ciclo de estados do war room
Os estados SHALL ser Detectado, Informado, Em tratamento, Mitigado, Resolvido e Descartado. As transições SHALL ser:
- **Detectado ou Informado → Em tratamento:** alguém assume como IC.
- **Em tratamento → Mitigado:** um humano marca mitigado.
- **Mitigado → Em tratamento:** um humano reabre.
- **Qualquer estado aberto → Resolvido:** um humano encerra.
- **Qualquer estado aberto → Descartado:** um humano descarta.

Resolvido e Descartado SHALL ser finais. Uma ação inválida para o estado atual SHALL ser recusada com a explicação.

#### Scenario: Assumir um incidente detectado
- **WHEN** "Ana" assume como IC um incidente Detectado
- **THEN** o incidente fica Em tratamento com IC "Ana" e o horário de TTA gravado

#### Scenario: Ação num incidente encerrado
- **WHEN** alguém tenta mudar a severidade de um incidente Resolvido
- **THEN** a ação é recusada

### Requirement: Severidade alterada por humano
Um humano SHALL poder subir ou descer a severidade. O evento SHALL registrar a severidade anterior, a nova e o autor. Depois de uma mudança humana, o escalonamento automático por alertas SHALL continuar só subindo a partir da nova severidade.

#### Scenario: Rebaixar para P3
- **WHEN** "Ana" muda um incidente P1 para P3
- **THEN** o incidente fica P3 e a timeline registra "P1 → P3 por Ana"

### Requirement: Horários de TTA, TTM e TTR
O incidente SHALL guardar o horário em que foi assumido, mitigado e encerrado. O war room SHALL mostrar TTA, TTM e TTR a partir da detecção.

#### Scenario: Incidente encerrado
- **WHEN** um incidente detectado às 10:00 é assumido às 10:04, mitigado às 10:20 e encerrado às 10:35
- **THEN** o cabeçalho mostra TTA de 4 min, TTM de 20 min e TTR de 35 min

### Requirement: Mitigado volta para Em tratamento quando o alerta volta
Quando um alerta disparando entra num incidente mitigado, o núcleo SHALL devolver o incidente ao estado em tratamento, com o evento de mudança de estado motivado por "alerta voltou a disparar" e o card no canal. O IC e os horários de TTA e TTM SHALL ser mantidos.

#### Scenario: Erro volta depois de mitigar
- **WHEN** o IC marcou um incidente como mitigado e o alerta do serviço volta a disparar 8 min depois
- **THEN** o incidente volta a em tratamento, a timeline mostra "alerta voltou a disparar" e o canal recebe o card

