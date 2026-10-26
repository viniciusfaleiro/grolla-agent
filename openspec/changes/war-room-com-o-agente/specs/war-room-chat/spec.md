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

A resposta SHALL ter limites próprios de tempo e de tokens, configuráveis. Quando o orçamento da resposta estiver acabando, o agente SHALL parar de consultar e responder com o que já consultou, dizendo o que não deu para verificar. O chat de um incidente SHALL ter um teto de tokens. Atingido o teto, o agente SHALL responder que o limite do chat foi atingido, sem chamar o LLM.

O uso SHALL ser registrado com a etapa `warroom_chat`.

#### Scenario: LLM fora do ar
- **WHEN** a API do LLM falha numa pergunta
- **THEN** o chat mostra "não consegui responder: LLM indisponível"

#### Scenario: Teto do chat atingido
- **WHEN** o chat do incidente já usou o teto de tokens
- **THEN** a próxima pergunta recebe "limite do chat deste incidente atingido" sem chamada ao LLM

#### Scenario: Orçamento acabando
- **WHEN** o agente já fez várias consultas e o orçamento da resposta não paga mais uma volta
- **THEN** ele responde com o que já consultou, sem novas consultas, em vez de "não consegui responder"

### Requirement: Resposta curta e direta
A resposta do agente no chat SHALL ser curta e direta, para não atrapalhar o war room:
- a primeira linha responde a pergunta numa frase;
- no máximo três linhas de evidência, com número, horário ou versão e o link quando houver;
- no máximo uma sugestão de ação;
- até cerca de 60 palavras, salvo quando pedirem detalhes ou um resumo.

A resposta SHALL NOT repetir a pergunta, o contexto do incidente ou a análise, nem explicar como o agente investigou. O agente SHALL fazer só as consultas necessárias para responder.

#### Scenario: Pergunta de sim ou não
- **WHEN** "Ana" pergunta "o rollback resolveu?"
- **THEN** a primeira linha responde sim ou não com o número que sustenta, seguida de no máximo três evidências curtas

### Requirement: Chat com cara de chat
O chat SHALL se parecer com um aplicativo de mensagens:
- as mensagens de quem está vendo à direita e as dos outros à esquerda, com nome e horário;
- o agente identificado como Grolla, com o avatar do mascote;
- a resposta do agente formatada (negrito, listas e links clicáveis), com as consultas feitas recolhidas;
- enquanto o agente trabalha, um indicador de que ele está investigando;
- Enter envia e Shift+Enter quebra a linha;
- a conversa rola sozinha para a mensagem nova, sem tirar quem está lendo mais acima do lugar.

O histórico inteiro da conversa SHALL ficar guardado com o incidente e aparecer igual para todos e a cada visita. Com o chat vazio, SHALL haver sugestões de perguntas que podem ser enviadas com um clique.

#### Scenario: Histórico ao voltar
- **WHEN** "Ana" fecha a página e volta mais tarde
- **THEN** a conversa inteira continua no chat, na ordem em que aconteceu

#### Scenario: Sugestão com um clique
- **WHEN** o chat está vazio e Ana clica em "Os erros continuam?"
- **THEN** a pergunta é enviada ao agente como se Ana a tivesse escrito
