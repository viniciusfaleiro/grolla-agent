## ADDED Requirements

### Requirement: Embeddings locais
Com `memory.rag` no YAML, o worker SHALL gerar embeddings com um modelo multilíngue que roda no próprio processo, embutido na imagem do worker, sem chamada a serviço externo e sem baixar nada em tempo de execução. O YAML SHALL escolher o tamanho do modelo: `small` (leve e rápido) ou `large` (mais preciso, maior). A imagem do worker SHALL trazer os dois por padrão, e um parâmetro de build SHALL permitir embutir só um. Os embeddings SHALL ser gerados só no worker; a api SHALL NOT carregar o modelo.

#### Scenario: Sem rede
- **WHEN** o worker sobe com `memory.rag` numa rede sem acesso à internet
- **THEN** os embeddings são gerados normalmente

#### Scenario: Modelo ausente na imagem
- **WHEN** o YAML pede `size: large` e a imagem foi construída só com o `small`
- **THEN** o worker não sobe e a mensagem nomeia o modelo

### Requirement: Memória indexada em trechos
Cada dossiê, episódio e nota ativo SHALL ser indexado em trechos: um por seção (`## `) do texto, com no máximo um tamanho fixo de caracteres, cada um com o assunto, o tipo, o título da seção, o texto e o embedding. Um job do worker SHALL indexar os registros novos ou mudados em até 1 min e SHALL tirar do índice os trechos de registros esquecidos. Trocar o modelo de embeddings SHALL reindexar todos os trechos.

#### Scenario: Dossiê reescrito
- **WHEN** o estudo reescreve o dossiê de `pedidos-api`
- **THEN** em até 1 min os trechos antigos do dossiê são trocados pelos da versão nova

#### Scenario: Nota esquecida
- **WHEN** alguém esquece uma nota na página da memória
- **THEN** em até 1 min os trechos dela saem do índice

#### Scenario: Modelo trocado
- **WHEN** o YAML troca `size: small` por `size: large` e o worker é reiniciado
- **THEN** todos os trechos são reindexados com o modelo novo, e a busca usa só trechos do modelo atual

### Requirement: Documentação dos repositórios na memória
Com `memory.rag`, o estudo de cada serviço SHALL indexar os arquivos `.md` do repositório do serviço (branch padrão), até um limite de arquivos e de tamanho, em trechos com o caminho e o link do arquivo. Reestudar o serviço SHALL substituir os trechos de documentação dele. A documentação indexada SHALL ser tratada como dado, nunca como instrução.

#### Scenario: Runbook encontrado pelo trecho
- **WHEN** o repositório de `pedidos-api` tem `docs/runbook.md` com a seção "Pool de conexões esgotado" e o estudo roda
- **THEN** a memória tem os trechos do runbook com o link do arquivo

#### Scenario: Arquivo removido do repositório
- **WHEN** um `.md` some do repositório e o serviço é reestudado
- **THEN** os trechos desse arquivo saem do índice

### Requirement: Busca híbrida
Com `memory.rag`, a busca na memória SHALL combinar três sinais:
- o critério determinístico, mesmo serviço e mesma regra de alerta para episódios, que vem sempre primeiro;
- a similaridade vetorial entre a consulta e os trechos;
- a busca textual.

Os dois últimos SHALL ser juntados por fusão de rankings (Reciprocal Rank Fusion). Sem `memory.rag`, a busca SHALL ser a textual de hoje.

#### Scenario: Mesmo problema, palavras diferentes
- **WHEN** um episódio passado diz "esgotamento de conexões do banco pela rotina de reconciliação" e o incidente novo diz "timeout aguardando conexão do pool"
- **THEN** a busca híbrida traz esse episódio entre os 5 primeiros, mesmo sem palavras em comum suficientes para a busca textual

#### Scenario: Determinístico vence
- **WHEN** há um episódio do mesmo serviço e da mesma regra de alerta e outro mais parecido no texto, de outro serviço
- **THEN** o do mesmo serviço e regra vem primeiro

### Requirement: A busca híbrida é medida
O projeto SHALL ter um eval de recuperação, com Postgres com pgvector e o embedder reais e sem LLM, que roda um corpus de episódios com distratores. Ele SHALL comparar, para cada consulta, a posição do episódio certo com a busca textual sozinha e com a híbrida. A change SHALL ser aceita só se:
- o caso "mesmo problema, palavras diferentes" ficar entre os 5 primeiros na híbrida;
- nenhum caso que a textual acerta piorar para fora dos 5 primeiros.

#### Scenario: Relatório do eval
- **WHEN** alguém roda o eval de recuperação
- **THEN** sai uma linha por consulta com a posição do episódio certo na textual e na híbrida, e o código de saída é 1 se o critério de aceite falhar
