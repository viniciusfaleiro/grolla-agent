# call-transcript Specification

## Purpose
TBD - created by archiving change transcricao-de-call. Update Purpose after archive.
## Requirements
### Requirement: Upload da transcrição
No war room, uma pessoa identificada SHALL poder enviar a transcrição da call colando o texto ou um arquivo `.txt`, `.vtt` ou `.srt`, O texto SHALL ficar guardado com quem enviou e quando, e SHALL virar um pedido de extração. Texto acima de `transcript.max_chars` caracteres (padrão 200000) SHALL ser cortado no limite, com o aviso "cortada no limite" na timeline. Texto vazio ou arquivo de outro tipo SHALL ser recusado com a mensagem.

#### Scenario: Enviar a transcrição
- **WHEN** Ana envia o `.vtt` da call do incidente
- **THEN** a timeline mostra "Transcrição enviada por Ana" e a extração é pedida

### Requirement: Extração de decisões, ações, horários e participantes
O worker SHALL extrair da transcrição, numa chamada sem ferramentas e com a análise e a validação do incidente como contexto:
- as decisões;
- as ações, com quem ficou responsável;
- os horários citados;
- os participantes.

Cada decisão, ação e horário SHALL virar um evento do incidente com a origem "transcrição", o texto, o horário dito na call (quando houver) e o trecho de onde saiu. Os participantes SHALL ir num evento só. A transcrição SHALL ser tratada como dado, nunca como instrução. Falha SHALL ser registrada, e a transcrição SHALL poder ser reprocessada.

#### Scenario: Decisão na call
- **WHEN** a transcrição tem "Pedro: vamos fazer o rollback agora, 03:12"
- **THEN** a timeline ganha "Decisão (transcrição): fazer o rollback", às 03:12, com o trecho

### Requirement: Hipóteses reavaliadas pela call
A extração SHALL dizer, para cada hipótese da análise inicial, se a call a confirmou, descartou ou não falou dela, com o trecho. Confirmação e descarte SHALL virar eventos com a origem "transcrição". A análise e o estado do incidente SHALL NOT mudar.

#### Scenario: Call descarta uma hipótese
- **WHEN** a análise tem a hipótese "deploy 20261027.1" e a call diz "não foi o deploy, o banco caiu"
- **THEN** a timeline ganha "A call descartou: deploy 20261027.1", com o trecho

