# operations-console Specification

## Purpose
TBD - created by archiving change console-de-operacao. Update Purpose after archive.
## Requirements
### Requirement: Sessões do console
A api SHALL servir `/console` com as sessões do time, as mais recentes primeiro, e o botão "Nova conversa". Uma sessão SHALL ter título (a primeira pergunta, cortada), autor, data e o histórico de mensagens. Qualquer pessoa identificada SHALL poder abrir uma sessão e continuar a conversa.

#### Scenario: Nova conversa
- **WHEN** Ana pergunta no console "o autorizador está saudável?"
- **THEN** nasce uma sessão com esse título, autora Ana, e a pergunta fica pendente de resposta

### Requirement: Respostas do console
O worker SHALL responder cada pergunta do console com o modelo, as ferramentas da investigação, a memória e o `recall`, no formato curto do chat:
- a resposta numa frase;
- até 3 evidências;
- uma sugestão, quando houver.

O contexto SHALL ser as últimas mensagens da sessão. Toda pergunta SHALL terminar com uma resposta ou "não consegui responder: <motivo>". As respostas do console SHALL rodar numa fila separada, de prioridade menor que a das análises e do chat dos incidentes.

#### Scenario: Pergunta sobre a saúde
- **WHEN** Ana pergunta "o pedidos-api está com erro agora?"
- **THEN** a resposta cita a taxa de erro medida e o link da ferramenta

#### Scenario: Teto diário
- **WHEN** o console já usou `console.daily_max_tokens` no dia
- **THEN** a pergunta recebe "limite diário do console atingido" sem chamar o modelo

### Requirement: Gráficos na resposta
O agente SHALL poder pedir até 2 gráficos por resposta, cada um com serviço, sinal (`taxa_de_erro`, `latencia_p99` ou `vazao`) e janela em minutos (até 24 h). A página SHALL desenhar cada gráfico com a série real da telemetria, lida pela api no momento em que a página abre, com eixos e legenda. Falha da telemetria SHALL mostrar "gráfico indisponível" no lugar.

#### Scenario: Gráfico da latência
- **WHEN** a resposta pede o gráfico de `latencia_p99` do `pedidos-api` nos últimos 60 min
- **THEN** a página mostra a linha da latência, com o horário no eixo e o valor em ms

### Requirement: Aprender pelo console
No console, a ferramenta `remember` SHALL gravar a nota com o autor da pergunta como fonte e a confiança `aprendido`, e SHALL recusar a nota com cara de ordem, como no chat do war room.

#### Scenario: Time ensina pelo console
- **WHEN** Ana escreve no console "o batch de conciliação roda às 3h e sobe o p99"
- **THEN** a memória ganha a nota `aprendido` com Ana como fonte, e a resposta diz "Guardei: …"

### Requirement: Abrir incidente a partir da conversa
Numa sessão do console, uma pessoa identificada SHALL poder abrir um incidente com título, serviço e severidade escolhidos. O incidente SHALL seguir o caminho normal: análise inicial, cards e war room. A sessão SHALL mostrar o link para o incidente, e a timeline do incidente SHALL registrar que ele foi aberto pelo console.

#### Scenario: Problema achado na conversa
- **WHEN** Ana abre, a partir da conversa, o incidente "pedidos-api lento" como P2
- **THEN** o incidente existe, a análise inicial é pedida e a sessão mostra o link

