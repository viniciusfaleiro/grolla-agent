> Change multiagente: domínio, persistência, integrações e e2e trabalham em paralelo a partir do commit de contrato. Sem mudança na web. O job `index_memory` fica com o `grolla-dominio`.

## 0. Contratos (agente pai)

- [ ] 0.1 Domínio: `ChunkOrigin`, `Chunk`, `ChunkHit`, `chunk_text`, `MemoryContext.documents`, com corpos `NotImplementedError`.
- [ ] 0.2 Application:
  - `TextEmbedder`, `EmbeddingError` e `Query`;
  - os métodos novos do `MemoryStore`;
  - `RepositoryDocument` e `ChangeHistory.documents`;
  - `Toolbox(embedder=)`;
  - `application.memory_retrieval`, com `RagPolicy` e os casos de uso;
  - `MemoryPolicy.rag`.
- [ ] 0.3 Stubs no Postgres, no Azure DevOps e nos fakes; adapter `grolla.adapters.fastembed` vazio; migração `0007`.
- [ ] 0.4 Dependência `fastembed` no `grolla-core` e `uv.lock`.
- [ ] 0.5 Imagem `pgvector/pgvector:pg16` no `docker-compose.yml`, no `deploy/compose.yml` e no `conftest.py` da raiz.
- [ ] 0.6 Config `memory.rag`, bootstrap (`WorkerContainer.embedder`) e Dockerfile do worker com o modelo embutido.
- [ ] 0.7 `make check` verde com os stubs e commit de contrato.

## 1. grolla-dominio

- [ ] 1.1 `chunk_text` (seções, corte por parágrafo, sem trecho vazio) e `memory_text` com o bloco `<documentacao>`.
- [ ] 1.2 `index_memory` (lote, reindexação por modelo, limpeza), `index_documents` (no estudo, depois de cada serviço) e `query_for` (falha do embedder vira consulta textual).
- [ ] 1.3 `memory_for` com a `Query` do incidente; `recall` com `search_chunks` quando há embedder, e com o formato de trecho (seção, texto, origem, link).
- [ ] 1.4 Job `index_memory` no worker, a cada 30 s e só com `memory.rag`; o estudo recebe o embedder.
- [ ] 1.5 `FakeTextEmbedder`, determinístico e sem rede (vetor de palavras normalizado, com sinônimos roteirizáveis para o teste de paráfrase). Fake do `MemoryStore` com trechos e RRF em Python. Suítes de contrato de `TextEmbedder`, `ChangeHistory.documents` e dos métodos novos do `MemoryStore`. Commit cedo.
- [ ] 1.6 Pisos de cobertura e `make check` verde.

## 2. grolla-persistencia

- [ ] 2.1 Migração 0007: extensão `vector` e `memory_chunks`, com `tsvector` em português e os índices.
- [ ] 2.2 `unindexed`, `replace_chunks`, `replace_documents` e `drop_stale_chunks`.
- [ ] 2.3 Busca híbrida em SQL, com CTEs de vetor e texto e RRF: `search_chunks`, e `context_for` com `Query`, para os episódios depois do determinístico e os `documents`.
- [ ] 2.4 Integração passando nas suítes de contrato; README do core; `make check` verde.

## 3. grolla-integracoes

- [ ] 3.1 `grolla.adapters.fastembed`:
  - `FastembedTextEmbedder(model, cache_dir)`, só com arquivos locais;
  - erro claro se o modelo não está no cache;
  - `embed` em thread;
  - vetores normalizados;
  - prefixos de query/passage quando o modelo pede.
- [ ] 3.2 Suíte de contrato do `TextEmbedder` com o modelo real, num teste de integração marcado que pula sem o modelo no cache; teste de paráfrase em português.
- [ ] 3.3 Azure DevOps: `documents`:
  - listagem recursiva por `scopePath` e `recursionLevel=Full`;
  - filtro `.md`;
  - menores primeiro, até os limites;
  - link de cada arquivo.

  `grolla-fakes` com `docs/runbook.md` (seção "Pool de conexões esgotado").
- [ ] 3.4 READMEs do core e do `e2e/fakes`; `make check` verde.

## 4. grolla-e2e

- [ ] 4.1 Cenário `memoria_rag_runbook.yaml`: o estudo indexa o runbook, e um alerta de timeout de conexão leva o trecho do runbook à requisição de análise ao LLM fake.
- [ ] 4.2 Cenário de paráfrase: um episódio escrito com outras palavras aparece no contexto da análise de um incidente novo, com `memory.rag` ligado; `make check` verde.

## 5. Integração (agente pai)

- [ ] 5.1 Juntar os branches e resolver conflitos.
- [ ] 5.2 Fazer o code review do todo entregue, com os ajustes que couberem.
- [ ] 5.3 Eval de recuperação (`evals/retrieval.py`, `make evals-rag`):
  - corpus de episódios com distratores;
  - consultas com a posição do certo na textual e na híbrida;
  - critério de aceite da spec.

  O caso "palavras diferentes" entra também no eval da análise com o LLM real.
- [ ] 5.4 Laboratório:
  - imagem pgvector no `grolla-local`;
  - `memory.rag` ligado;
  - indexação da memória existente e da documentação do `pedidos-api` (um `docs/runbook.md` no repositório do laboratório);
  - um incidente que recupere o trecho.
- [ ] 5.5 `deploy/README.md` com a troca da imagem do banco; `CLAUDE.md` "Estado atual"; roadmap.
- [ ] 5.6 Registrar no `design.md` os desvios de contrato.
- [ ] 5.7 Revisar e atualizar os READMEs do pai e de cada componente.
- [ ] 5.8 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 5.9 Rodar `make fmt && make check` e garantir tudo verde.
