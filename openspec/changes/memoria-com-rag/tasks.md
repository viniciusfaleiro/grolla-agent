> Change multiagente: domínio, persistência, integrações e e2e trabalham em paralelo a partir do commit de contrato. Sem mudança na web. O job `index_memory` fica com o `grolla-dominio`.

## 0. Contratos (agente pai)

- [x] 0.1 Domínio: `ChunkOrigin`, `Chunk`, `ChunkHit`, `chunk_text`, `MemoryContext.documents`, com corpos `NotImplementedError`.
- [x] 0.2 Application:
  - `TextEmbedder`, `EmbeddingError` e `Query`;
  - os métodos novos do `MemoryStore`;
  - `RepositoryDocument` e `ChangeHistory.documents`;
  - `Toolbox(embedder=)`;
  - `application.memory_retrieval`, com `RagPolicy` e os casos de uso;
  - `MemoryPolicy.rag`.
- [x] 0.3 Stubs no Postgres, no Azure DevOps e nos fakes; adapter `grolla.adapters.fastembed` vazio; migração `0007`.
- [x] 0.4 Dependência `fastembed` no `grolla-core` e `uv.lock`.
- [x] 0.5 Imagem `pgvector/pgvector:pg16` no `docker-compose.yml`, no `deploy/compose.yml` e no `conftest.py` da raiz.
- [x] 0.6 Config `memory.rag` (`size: small | large`), bootstrap (`WorkerContainer.embedder`) e Dockerfile do worker com o modelo embutido.
- [x] 0.7 `make check` verde com os stubs e commit de contrato.

## 1. grolla-dominio

- [x] 1.1 `chunk_text` (seções, corte por parágrafo, sem trecho vazio) e `memory_text` com o bloco `<documentacao>`.
- [x] 1.2 `index_memory` (lote, reindexação por modelo, limpeza), `index_documents` (no estudo, depois de cada serviço) e `query_for` (falha do embedder vira consulta textual).
- [x] 1.3 `memory_for` com a `Query` do incidente; `recall` com `search_chunks` quando há embedder, e com o formato de trecho (seção, texto, origem, link).
- [x] 1.4 Job `index_memory` no worker, a cada 30 s e só com `memory.rag`; o estudo recebe o embedder.
- [x] 1.5 `FakeTextEmbedder`, determinístico e sem rede (vetor de palavras normalizado, com sinônimos roteirizáveis para o teste de paráfrase). Fake do `MemoryStore` com trechos e RRF em Python. Suítes de contrato de `TextEmbedder`, `ChangeHistory.documents` e dos métodos novos do `MemoryStore`. Commit cedo.
- [x] 1.6 Pisos de cobertura e `make check` verde.

## 2. grolla-persistencia

- [x] 2.1 Migração 0007: extensão `vector` e `memory_chunks`, com `tsvector` em português e os índices.
- [x] 2.2 `unindexed`, `replace_chunks`, `replace_documents` e `drop_stale_chunks`.
- [x] 2.3 Busca híbrida em SQL, com CTEs de vetor e texto e RRF: `search_chunks`, e `context_for` com `Query`, para os episódios depois do determinístico e os `documents`.
- [x] 2.4 Integração passando nas suítes de contrato; README do core; `make check` verde.

## 3. grolla-integracoes

- [x] 3.1 `grolla.adapters.fastembed`:
  - `FastembedTextEmbedder(model, cache_dir)`, só com arquivos locais;
  - erro claro se o modelo não está no cache;
  - `embed` em thread;
  - vetores normalizados;
  - prefixos de query/passage quando o modelo pede.
- [x] 3.2 Suíte de contrato do `TextEmbedder` com o modelo real, num teste de integração marcado que pula sem o modelo no cache; teste de paráfrase em português.
- [x] 3.3 Azure DevOps: `documents`:
  - listagem recursiva por `scopePath` e `recursionLevel=Full`;
  - filtro `.md`;
  - menores primeiro, até os limites;
  - link de cada arquivo.

  `grolla-fakes` com `docs/runbook.md` (seção "Pool de conexões esgotado").
- [x] 3.4 READMEs do core e do `e2e/fakes`; `make check` verde.

## 4. grolla-e2e

- [x] 4.1 Cenário `memoria_rag_runbook.yaml`: o estudo indexa o runbook, e um alerta de timeout de conexão leva o trecho do runbook à requisição de análise ao LLM fake.
- [x] 4.2 Cenário de paráfrase: um episódio escrito com outras palavras aparece no contexto da análise de um incidente novo, com `memory.rag` ligado; `make check` verde.

## 5. Integração (agente pai)

- [x] 5.1 Juntar os branches e resolver conflitos.
- [x] 5.2 Fazer o code review do todo entregue, com os ajustes que couberem.
- [x] 5.3 Eval de recuperação (`evals/retrieval.py`, `make evals-rag`):
  - corpus de episódios com distratores;
  - consultas com a posição do certo na textual e na híbrida;
  - critério de aceite da spec.

  O caso "palavras diferentes" entra também no eval da análise com o LLM real.
- [x] 5.4 Laboratório:
  - imagem pgvector no `grolla-local`;
  - `memory.rag` ligado;
  - indexação da memória existente e da documentação do `pedidos-api` (um `docs/runbook.md` no repositório do laboratório);
  - um incidente que recupere o trecho.
- [x] 5.5 `deploy/README.md` com a troca da imagem do banco; `CLAUDE.md` "Estado atual"; roadmap.
- [x] 5.6 Registrar no `design.md` os desvios de contrato.
- [x] 5.7 Revisar e atualizar os READMEs do pai e de cada componente.
- [x] 5.8 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 5.9 Rodar `make fmt && make check` e garantir tudo verde.
