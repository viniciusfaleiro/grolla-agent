## Why

O time só conversa com o Grolla dentro de um incidente. Perguntas do dia a dia ficam sem
resposta fácil, mesmo com o agente já conhecendo o ambiente e tendo as ferramentas:
- "o autorizador está saudável?";
- "o que mudou ontem no pedidos-api?";
- "por que o p99 sobe às 3h?".

E quando a conversa revela um problema, não há como virar incidente sem esperar um alerta
(roadmap S9, "Console de operação").

## What Changes

- **Console de operação** (`/console`): chat geral com o Grolla, fora de incidente, com sessões
  salvas (título, autor, histórico) que qualquer pessoa do time reabre.
- **Respostas com evidência e gráficos.** O agente usa as ferramentas, a memória, o RAG e o
  `recall`, no formato curto do chat. Quando ajuda, ele pede um **gráfico** (serviço, sinal e
  janela), que a página desenha com os dados reais da telemetria, sem passar pelo modelo.
- **Aprender pelo console.** O `remember` funciona no console, com o autor da pergunta como fonte,
  como no war room.
- **Abrir incidente a partir da conversa.** Um botão abre um incidente manual (título, serviço e
  severidade escolhidos pela pessoa), que segue o caminho normal: análise, war room e cards. A
  conversa fica ligada ao incidente.
- **Prioridade menor e limite próprio.** As respostas do console rodam numa fila de prioridade
  baixa, com teto de tokens por resposta e por dia.

## Capabilities

### New Capabilities
- `operations-console`: sessões, respostas, gráficos, aprender, abrir incidente e limites.

### Modified Capabilities
- `incident-grouping`: incidente aberto manualmente por uma pessoa.
- `team-config`: seção `console`.

## Non-goals

- **Vigias criados pelo chat** ("me avise se…"), que são fase 2.
- **Gráficos arbitrários** (consultas livres): só os sinais que já existem (taxa de erro,
  latência p99, vazão).
- **Sessões privadas.** Toda sessão é visível ao time, como o war room.
- **Streaming da resposta.** O console usa o polling do chat.

## Impact

- `grolla-core`:
  - domínio do console (sessões, mensagens, gráficos) e do incidente manual;
  - casos de uso do console;
  - port `ConsoleStore`, com o Postgres e a migração 0012;
  - `Telemetry.series` (SigNoz);
  - `LanguageModel.consult` (Anthropic).
- `grolla-worker`: fila `console` de prioridade baixa.
- `grolla-api`: `/console`, a sessão, os gráficos em SVG e o "abrir incidente".
- `e2e/fakes`: LLM fake com a etapa `console`; o SigNoz fake devolve as séries.
- Custo: teto diário do console no YAML.
