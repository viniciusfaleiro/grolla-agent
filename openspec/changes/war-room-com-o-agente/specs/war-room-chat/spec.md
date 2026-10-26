## ADDED Requirements

### Requirement: Chat com o agente no war room
O war room SHALL ter um chat em que qualquer pessoa, identificada pelo nome, escreve para o agente. Cada mensagem SHALL virar um evento na timeline, com o autor, e um pedido de resposta. O agente SHALL responder de forma assíncrona no mesmo chat, e a resposta também SHALL virar um evento.

#### Scenario: Pergunta ao agente
- **WHEN** "Ana" pergunta "o rollback resolveu?"
- **THEN** a pergunta aparece no chat e na timeline, e a resposta do agente aparece em seguida

### Requirement: O agente responde com evidência
Para responder, o agente SHALL poder usar as mesmas ferramentas neutras e somente leitura da investigação: telemetria, deploys e nuvem.

O contexto da resposta SHALL incluir:
- o incidente;
- a análise inicial;
- a validação;
- a timeline, com as ações humanas;
- as mensagens anteriores do chat.

As consultas feitas para responder SHALL ser mostradas junto da resposta, como avaliações. O agente SHALL NOT mudar estado, severidade ou hipóteses: ele só responde, e pode sugerir que um humano faça uma ação.

#### Scenario: Pedido de investigação
- **WHEN** "Ana" pede "olha os logs do pedidos-db desde 21:40"
- **THEN** o agente consulta os logs, responde com o que encontrou e mostra a consulta feita

### Requirement: A resposta sempre chega
Cada pedido de resposta SHALL terminar com uma resposta do agente. Se o LLM falhar, se o tempo ou o orçamento se esgotarem, ou se o worker cair no meio, a resposta SHALL ser "não consegui responder: <motivo>", com as consultas já feitas.

A resposta SHALL ter limites próprios de tempo e de tokens, configuráveis. O chat de um incidente SHALL ter um teto de tokens. Atingido o teto, o agente SHALL responder que o limite do chat foi atingido, sem chamar o LLM.

O uso SHALL ser registrado com a etapa `warroom_chat`.

#### Scenario: LLM fora do ar
- **WHEN** a API do LLM falha numa pergunta
- **THEN** o chat mostra "não consegui responder: LLM indisponível"

#### Scenario: Teto do chat atingido
- **WHEN** o chat do incidente já usou o teto de tokens
- **THEN** a próxima pergunta recebe "limite do chat deste incidente atingido" sem chamada ao LLM
