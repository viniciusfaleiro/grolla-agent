## MODIFIED Requirements

### Requirement: Lembrar ao investigar
A análise inicial, a validação e o chat SHALL receber, como dado e nunca como instrução:
- o dossiê do ambiente;
- o dossiê de cada serviço do incidente e das integrações citadas neles;
- as notas ativas desses assuntos ainda não incorporadas;
- até 5 episódios parecidos, escolhidos primeiro por mesmo serviço e mesma regra de alerta e depois pela busca da memória nos sintomas e na causa (híbrida com `memory.rag`, textual sem ela);
- com `memory.rag`, até 3 trechos da documentação dos repositórios dos serviços do incidente mais relevantes para o alerta, com o link do arquivo.

#### Scenario: Caso parecido no histórico
- **WHEN** abre um incidente de `pedidos-api` com a mesma regra de alerta de um episódio passado
- **THEN** o episódio está entre os parecidos entregues à análise inicial

#### Scenario: Caso parecido com outras palavras
- **WHEN** abre um incidente cujo alerta descreve com outras palavras o mesmo problema de um episódio de outra regra, e `memory.rag` está ligado
- **THEN** o episódio está entre os parecidos entregues à análise inicial

#### Scenario: Trecho do runbook na análise
- **WHEN** abre um incidente de `pedidos-api` com timeout de conexão e o runbook indexado tem a seção "Pool de conexões esgotado"
- **THEN** a análise inicial recebe esse trecho com o link do runbook

#### Scenario: Alerta explicado pelo dossiê
- **WHEN** dispara uma regra de alerta que o dossiê do serviço descreve com "o que olhar primeiro"
- **THEN** a análise inicial recebe essa descrição junto com o alerta

#### Scenario: Ambiente ainda não estudado
- **WHEN** abre um incidente e a memória está vazia
- **THEN** a investigação segue normalmente, sem dossiê e sem episódios
