## MODIFIED Requirements

### Requirement: Consulta à memória
O LLM SHALL ter a ferramenta neutra `recall(subject, query)` na investigação, na validação e no chat. Ela SHALL devolver o que a busca da memória achar, mais relevantes primeiro, com tipo, confiança, data e link; `subject` vazio busca em todos os assuntos.
- Com `memory.rag`, a busca é a híbrida e devolve trechos: o título da seção, o texto do trecho e a origem, que pode ser um registro da memória ou um arquivo de documentação com o link.
- Sem `memory.rag`, devolve os registros que casam com a busca textual, confirmados e mais recentes primeiro.

Cada chamada SHALL virar avaliação como as outras ferramentas.

#### Scenario: Buscar caso antigo
- **WHEN** o LLM chama `recall` para `pedidos-api` com "pool de conexões"
- **THEN** recebe os episódios e notas do serviço que citam pool de conexões, com o link de cada incidente

#### Scenario: Buscar no runbook
- **WHEN** com `memory.rag`, o LLM chama `recall` para `pedidos-api` com "como liberar conexões presas"
- **THEN** recebe o trecho do runbook sobre pool de conexões, com o link do arquivo, mesmo sem as mesmas palavras

#### Scenario: Nada na memória
- **WHEN** o LLM chama `recall` para um assunto sem memória
- **THEN** recebe "nada na memória sobre isso" em vez de um erro
