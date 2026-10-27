# agent-memory Specification

## Purpose
TBD - created by archiving change memoria-viva. Update Purpose after archive.
## Requirements
### Requirement: Base de conhecimento do ambiente
A memória do agente SHALL ser uma base de conhecimento sobre o ambiente que ele cuida, organizada por assunto. Cada assunto SHALL ser de um tipo:
- **ambiente**: um só, com a visão geral — serviços do domínio, alertas que o agente cuida, integrações, arquitetura de infraestrutura e como eles se ligam;
- **serviço**: um por serviço do domínio;
- **integração**: um por dependência descoberta nos traces (outro serviço, banco, fila, API externa).

Cada assunto SHALL ter um **dossiê**: texto vivo, escrito pelo agente, com tudo que importa para troubleshooting. O dossiê de serviço SHALL ter as seções: o que faz, repositório e documentação, pipeline e deploy, infraestrutura na nuvem, dependências e integrações, alertas (o que cada um significa e o que olhar primeiro), comportamento normal, fragilidades conhecidas e como investigar.

Além do dossiê, a memória SHALL guardar:
- **episódio**: um por incidente e serviço afetado, com sintomas, causa, o que resolveu, o que despistou e se era ruído, e o link para o incidente;
- **nota**: um fato curto sobre um assunto, até ser incorporado ao dossiê.

Todo registro SHALL ter fonte (estudo do ambiente, incidente, mensagem do chat ou pessoa do time, com o link quando houver), data de criação, data da última atualização e confiança `aprendido` ou `confirmado`. Todo registro SHALL estar ativo ou esquecido; registro esquecido SHALL NOT ser usado pelo agente.

#### Scenario: Nota com fonte
- **WHEN** o agente grava uma nota a partir de uma mensagem do chat
- **THEN** a nota guarda o incidente, o autor da mensagem e a data como fonte

#### Scenario: Registro esquecido
- **WHEN** alguém do time esquece uma nota
- **THEN** a nota some das consultas do agente e continua visível no histórico da página

### Requirement: Estudo inicial do ambiente
Com `memory.study` no YAML, ao subir com a base de conhecimento vazia, o worker SHALL estudar o ambiente nesta ordem:
1. **alertas**: as regras de alerta do domínio do time, isto é, as que casam com o `alert_filter` do YAML, com condição, severidade e serviço;
2. **serviços**: os serviços dessas regras, mais os com atividade na telemetria que tenham repositório;
3. **por serviço**, numa investigação com ferramentas e limite de tokens próprio:
   - o repositório: README e demais `.md`, e o que mais for preciso para entender o serviço;
   - as pipelines: definição do pipeline no repositório e os deploys recentes;
   - a infraestrutura na nuvem: os recursos do serviço e o estado deles;
   - as integrações: as dependências chamadas pelo serviço, vistas nos traces, com volume, erro e latência;
   - resultado: o dossiê do serviço;
4. **síntese**: numa chamada sem ferramentas, a partir dos dossiês dos serviços, das regras de alerta e da visão geral da nuvem, o dossiê do ambiente e um dossiê por integração.

O que o estudo escreve SHALL ficar com confiança `aprendido` e fonte "estudo do ambiente", com os links consultados. O estudo SHALL respeitar o limite de tokens da rodada; serviço não estudado por falta de orçamento SHALL ficar para a próxima rodada, antes dos outros. Falha num serviço SHALL ser registrada e SHALL NOT impedir os demais. A página SHALL permitir pedir um novo estudo completo.

#### Scenario: Primeira subida
- **WHEN** o worker sobe pela primeira vez com `memory.study` e o domínio tem 2 regras de alerta do `pedidos-api`
- **THEN** a memória ganha o dossiê do `pedidos-api` com repositório, pipeline, recursos na nuvem, dependências e o significado das 2 regras, e o dossiê do ambiente

#### Scenario: Integração descoberta nos traces
- **WHEN** os traces do `pedidos-api` mostram chamadas ao banco `grolla-lab-db`
- **THEN** a memória ganha o dossiê da integração `grolla-lab-db`, citando quem chama, volume e latência normais

#### Scenario: Orçamento acaba no meio
- **WHEN** o limite de tokens acaba no terceiro de cinco serviços
- **THEN** a síntese roda com os serviços estudados e os dois restantes são estudados primeiro na próxima rodada

#### Scenario: Estudo desligado
- **WHEN** o YAML não tem `memory.study`
- **THEN** nenhum estudo roda e o aprendizado ao encerrar incidentes continua ligado

### Requirement: Reaprender o ambiente do zero
Na página da memória, uma pessoa identificada SHALL poder pedir "reaprender o ambiente", com confirmação. O pedido SHALL:
- esquecer todos os dossiês e todas as notas `aprendido`, guardando uma versão com o autor do pedido e o motivo "reaprender o ambiente";
- manter os episódios, porque são o histórico do que aconteceu, e as notas `confirmado`, porque são o que o time ensinou;
- refazer o estudo inicial completo, e a síntese SHALL incorporar os episódios e as notas confirmadas nos dossiês novos.

Nada SHALL ser apagado de fato: o que foi esquecido continua no histórico e pode voltar. Enquanto o estudo não termina, a investigação SHALL usar o que já foi reescrito e os episódios.

#### Scenario: Reaprender depois de uma reorganização
- **WHEN** alguém pede "reaprender o ambiente" depois que dois serviços mudaram de repositório
- **THEN** os dossiês antigos ficam esquecidos com o autor do pedido, o estudo refaz os dossiês a partir dos repositórios novos e os episódios e notas confirmadas aparecem nos dossiês novos

#### Scenario: Desfazer
- **WHEN** alguém abre o histórico de um dossiê esquecido pelo reaprendizado
- **THEN** pode restaurar a versão anterior, que volta ativa

### Requirement: Estudo diário
Com `memory.study`, o worker SHALL refazer o estudo uma vez por dia, no horário do YAML: relê as regras de alerta do domínio; estuda primeiro os serviços novos e os não estudados, depois os de dossiê mais antigo, até o limite de tokens da rodada; e refaz a síntese quando algum dossiê mudou. Regra de alerta que sumiu SHALL ser retirada do dossiê na próxima consolidação.

#### Scenario: Regra de alerta nova
- **WHEN** o time cria uma regra de alerta para `captura-api`, que ainda não tem dossiê
- **THEN** no estudo do dia seguinte `captura-api` é estudado antes dos serviços já conhecidos

### Requirement: Aprende sozinho, o time corrige
O que o agente aprende SHALL entrar ativo na hora, com confiança `aprendido`, sem esperar aprovação. O que uma pessoa do time escreve, edita ou confirma SHALL ficar com confiança `confirmado`. Ao reescrever um dossiê, o agente SHALL preservar o que está confirmado e SHALL marcar como contradição, em vez de apagar, quando um estudo ou um incidente novo contrariar um fato confirmado.

#### Scenario: Correção do time prevalece
- **WHEN** o dossiê tem "tráfego zero de madrugada é normal" confirmado e um episódio novo mostra queda de madrugada que era incidente real
- **THEN** o dossiê consolidado mantém o fato e acrescenta a contradição com o link do incidente

### Requirement: Versões de cada registro
Toda mudança num registro SHALL gravar uma versão com o texto anterior, o autor (pessoa ou "Grolla") e o motivo. A página SHALL mostrar o histórico de versões de cada registro.

#### Scenario: Dossiê reescrito pelo agente
- **WHEN** o agente consolida o dossiê de `pedidos-api` depois de um incidente
- **THEN** a versão anterior fica no histórico com o autor "Grolla" e o motivo com o título do incidente

### Requirement: Aprender ao encerrar o incidente
Quando um incidente é resolvido ou descartado, o worker SHALL, numa única chamada ao LLM sem ferramentas, a partir do log de eventos do incidente:
- escrever o episódio de cada serviço afetado;
- consolidar o dossiê de cada serviço afetado, e o das integrações envolvidas, com o episódio e as notas novas.

O pedido de aprendizado SHALL ser gravado na mesma transação do encerramento. Incidente reaberto e encerrado de novo SHALL atualizar o mesmo episódio, com nova versão. Incidente descartado SHALL gerar episódio marcado como ruído. Falha ou estouro do limite de tokens SHALL ser registrado e SHALL NOT afetar o incidente; o pedido SHALL ser tentado de novo uma vez.

#### Scenario: Incidente resolvido vira episódio
- **WHEN** o IC encerra um incidente de `pedidos-api` com "rollback do build 20261026.1"
- **THEN** em até 1 min a memória tem o episódio do incidente com a causa e o que resolveu, e o dossiê de `pedidos-api` traz o aprendizado

#### Scenario: Alarme falso
- **WHEN** um incidente é descartado com o motivo "alarme falso: batch de conciliação"
- **THEN** o episódio fica marcado como ruído e o dossiê registra o alerta como ruído conhecido

#### Scenario: Aprendizado falhou
- **WHEN** o LLM falha duas vezes no aprendizado de um incidente
- **THEN** o pedido fica como falho, a página da memória mostra a falha e o incidente continua encerrado

### Requirement: Lembrar ao investigar
A análise inicial, a validação e o chat SHALL receber, como dado e nunca como instrução:
- o dossiê do ambiente;
- o dossiê de cada serviço do incidente e das integrações citadas neles;
- as notas ativas desses assuntos ainda não incorporadas;
- até 5 episódios parecidos, escolhidos primeiro por mesmo serviço e mesma regra de alerta e depois por busca textual nos sintomas e na causa.

#### Scenario: Caso parecido no histórico
- **WHEN** abre um incidente de `pedidos-api` com a mesma regra de alerta de um episódio passado
- **THEN** o episódio está entre os parecidos entregues à análise inicial

#### Scenario: Alerta explicado pelo dossiê
- **WHEN** dispara uma regra de alerta que o dossiê do serviço descreve com "o que olhar primeiro"
- **THEN** a análise inicial recebe essa descrição junto com o alerta

#### Scenario: Ambiente ainda não estudado
- **WHEN** abre um incidente e a memória está vazia
- **THEN** a investigação segue normalmente, sem dossiê e sem episódios

### Requirement: Aprender no chat
No chat do war room o agente SHALL ter a ferramenta `remember(subject, text)`, que grava uma nota ativa num assunto. Quando uma pessoa do time corrige ou explica algo sobre o ambiente, o agente SHALL gravar a nota com o autor da mensagem como fonte e a confiança `confirmado`; o que o próprio agente concluir SHALL ficar `aprendido`. A resposta do chat SHALL dizer em uma linha o que foi guardado.

#### Scenario: Time explica o normal
- **WHEN** alguém escreve no chat "esse p99 alto às 3h é o batch de conciliação, é normal"
- **THEN** a memória ganha a nota confirmada com esse autor como fonte, e a resposta do Grolla diz "Guardei: …"

### Requirement: Página da memória
A api SHALL servir `/memoria`, com o dossiê do ambiente, a lista de assuntos por tipo, o estado do último estudo e os aprendizados falhos, e `/memoria/<assunto>`, com o dossiê, os episódios e as notas do assunto. Na página, uma pessoa identificada SHALL poder:
- editar o dossiê ou uma nota;
- confirmar um registro aprendido;
- esquecer um registro;
- adicionar uma nota, inclusive num assunto ainda sem dossiê;
- restaurar uma versão anterior de um registro;
- pedir um novo estudo completo do ambiente, mantendo a memória;
- pedir para reaprender o ambiente do zero.

Cada registro SHALL mostrar a confiança, a fonte com link e a data.

#### Scenario: Semear conhecimento
- **WHEN** alguém adiciona em `/memoria/captura-api` a nota "depende do autorizador e do Redis de sessão"
- **THEN** a nota fica confirmada, com essa pessoa como fonte, e entra na próxima investigação de `captura-api`

#### Scenario: Corrigir o dossiê
- **WHEN** alguém edita o dossiê de `pedidos-api`
- **THEN** o dossiê fica confirmado, com nova versão com o autor, e o agente usa o texto novo

#### Scenario: Estudar de novo
- **WHEN** alguém pede um novo estudo completo na página
- **THEN** o próximo ciclo do worker refaz o estudo de todos os serviços, respeitando o limite de tokens da rodada

