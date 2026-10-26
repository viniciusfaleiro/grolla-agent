## Why

Hoje a página do incidente é só leitura. O time recebe a análise e a causa raiz, mas não conduz o incidente por ali, e o agente não participa depois da validação. O usuário quer um **war room** de verdade:

- o time assume o incidente, registra o que fez, ajusta a severidade e encerra;
- o agente participa por um **chat**, onde dá para perguntar ou pedir que ele investigue algo para ajudar no troubleshooting.

Ver `docs/initial-spec.md`, seções "Ciclo de vida do incidente", "War room web" e "Identificação de autor na fase 1". O chat com o agente estava previsto no S5 e foi trazido para cá.

## What Changes

- **Ações humanas no war room**:
  - **Assumir como IC**: o incidente passa para **Em tratamento**.
  - **Mudar a severidade**, para cima ou para baixo, registrando quem mudou.
  - **Registrar uma ação feita**, em texto livre ("fiz rollback do build .3").
  - **Descartar uma hipótese** do agente, com motivo.
  - **Marcar mitigado**.
  - **Encerrar** (Resolvido), com motivo obrigatório, ou **descartar** como falso positivo, também com motivo.
  - Só um humano encerra.
- **Ciclo de estados**: Detectado/Informado → Em tratamento → Mitigado → Resolvido, ou Descartado a partir de qualquer estado aberto.
  - Cada transição grava o horário. O cabeçalho mostra TTA (até assumir), TTM (até mitigar) e TTR (até encerrar).
  - Um incidente encerrado ou descartado deixa de receber alertas novos: o alerta seguinte abre outro incidente.
- **Chat com o agente**:
  - O time escreve perguntas ou pedidos ("olha os logs do pedidos-db desde 21:40", "o rollback resolveu?").
  - O agente responde no war room, usando as mesmas ferramentas neutras e somente leitura: SigNoz, Azure DevOps e nuvem. O contexto da resposta inclui o incidente, a análise inicial, a validação, a timeline com as ações humanas e o histórico do chat.
  - A resposta sempre chega. Se falhar ou estourar o tempo, sai "não consegui responder: motivo".
  - Limites próprios de tempo e de tokens por resposta, e um teto de tokens do chat por incidente. A etapa do LLM é `warroom_chat`.
- **Autor sem SSO (fase 1)**: a página pede um nome na primeira visita e guarda num cookie. Toda ação e toda mensagem registram esse nome. Não é autenticação: a página continua só na rede interna.
- **Teams**:
  - Cards curtos para IC assumido, severidade alterada por humano e mitigado.
  - O card de encerramento traz a duração, o motivo, a causa raiz e as ações registradas.
  - O chat não vai para o Teams.
- **Página**: formulários com HTMX. A timeline e o chat se atualizam por polling a cada 3 s. O cabeçalho mostra estado, severidade, duração, IC, TTA, TTM e TTR.

## Non-goals

- Acionamento do Spike.sh e o estado Acionado: ficam no S4b, em modo sombra.
- Separar ou juntar incidentes: fica para quando o agrupamento errar na prática.
- O agente propor mitigação ou encerramento sozinho, e os ciclos de acompanhamento: ficam no S5.
- O agente mudar estado, severidade ou hipóteses por conta própria a partir do chat: ele só responde. Quem muda é o humano.
- Upload de transcrição de call (S10), SSO (fase 2) e atualização ao vivo por SSE.

## Capabilities

### New Capabilities

- `war-room-actions`: ações humanas, ciclo de estados, autor e horários de TTA, TTM e TTR.
- `war-room-chat`: chat com o agente no war room, com contexto, ferramentas, limites e a garantia de que a resposta sempre chega.

### Modified Capabilities

- `incident-grouping`: incidentes encerrados ou descartados não recebem alertas novos.
- `incident-pages`: a página vira o war room (cabeçalho, ações, chat, timeline com autor).
- `incident-notifications`: cards das ações humanas e o card de encerramento.

## Impact

- **Código**:
  - domínio das ações e da máquina de estados;
  - caso de uso do chat e fila das respostas;
  - prompt do chat;
  - rotas e templates da api, com HTMX;
  - cards do Teams;
  - job do worker;
  - modo de chat no LLM fake;
  - cenários e2e.
- **Banco**: migração 0005, com os campos do IC e dos horários em `incidents` e a fila de respostas do chat.
- **Dependências**: o HTMX entra como arquivo estático servido pela api, sem build.
- **Custo**: cada pergunta é uma chamada ao LLM com ferramentas, com teto de tokens por resposta e por incidente.
