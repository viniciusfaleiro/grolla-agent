## MODIFIED Requirements

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

As duas páginas SHALL mostrar a lista de assuntos ao lado, cada um com a data do último registro ou "sem estudo" quando ainda não tem dossiê, e um campo para achar um assunto pelo nome.

#### Scenario: Semear conhecimento
- **WHEN** alguém adiciona em `/memoria/captura-api` a nota "depende do autorizador e do Redis de sessão"
- **THEN** a nota fica confirmada, com essa pessoa como fonte, e entra na próxima investigação de `captura-api`

#### Scenario: Corrigir o dossiê
- **WHEN** alguém edita o dossiê de `pedidos-api`
- **THEN** o dossiê fica confirmado, com nova versão com o autor, e o agente usa o texto novo

#### Scenario: Estudar de novo
- **WHEN** alguém pede um novo estudo completo na página
- **THEN** o próximo ciclo do worker refaz o estudo de todos os serviços, respeitando o limite de tokens da rodada

#### Scenario: Assunto sem estudo
- **WHEN** a memória tem o assunto `checkout-api` só com episódios, sem dossiê
- **THEN** a lista de assuntos mostra `checkout-api` como "sem estudo"
