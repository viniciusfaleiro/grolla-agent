## ADDED Requirements

### Requirement: Configuração do RAG
A seção `memory` do YAML SHALL aceitar a subseção opcional `rag` com:
- `size`: o tamanho do modelo de embeddings, `small` (padrão) ou `large`, que precisa estar embutido na imagem do worker;
- `chunk_chars`: o tamanho máximo de um trecho;
- `max_doc_files` e `max_doc_bytes`: os limites da documentação indexada por serviço.

Sem `rag`, a memória SHALL funcionar sem embeddings, só com a busca textual. Valores fora do intervalo SHALL impedir o processo de subir, com a mensagem apontando o campo.

#### Scenario: RAG desligado
- **WHEN** o YAML não tem `memory.rag`
- **THEN** nenhum embedding é gerado e `recall` usa a busca textual

#### Scenario: Trecho pequeno demais
- **WHEN** o YAML tem `memory.rag.chunk_chars: 10`
- **THEN** o processo não sobe e a mensagem aponta o campo
