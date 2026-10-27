## Context

A memória viva guarda dossiês, episódios e notas no Postgres e os encontra por critério
determinístico (serviço, regra de alerta) e por `tsvector` em português. Os `.md` dos
repositórios só são lidos sob demanda (`read_file`). O usuário quer um RAG completo, como
laboratório: embeddings, banco vetorial, recorte em trechos, documentação indexada e busca
híbrida, medido por eval. Restrições: só Anthropic como fornecedor de LLM, e a Anthropic não tem
API de embeddings; nada de serviço pago novo; o mesmo desenho precisa funcionar no trabalho, onde
a internet da produção pode ser restrita.

## Goals / Non-Goals

**Goals:**
- Embeddings locais atrás de um port, com o modelo embutido na imagem do worker.
- pgvector no Postgres existente; trechos com embedding e `tsvector`.
- Indexação incremental da memória por um job; documentação dos repositórios indexada no estudo.
- Busca híbrida (determinístico → RRF de vetor e texto) no contexto da investigação e no `recall`.
- Eval de recuperação sem custo, que decide o aceite.

**Non-Goals:** os da proposta.

## Contratos

```python
# grolla.domain.memory (acréscimos)
class ChunkOrigin(StrEnum):
    MEMORY = "memoria"  # trecho de um dossiê, episódio ou nota
    DOCUMENT = "documento"  # trecho de um .md do repositório


@dataclass(frozen=True)
class Chunk:
    origin: ChunkOrigin
    subject: str  # assunto do registro; serviço dono do repositório no documento
    heading: str  # título da seção ("" quando o texto não tem seção)
    text: str
    memory_id: UUID | None = None  # origem MEMORY
    kind: MemoryKind | None = None  # origem MEMORY
    path: str | None = None  # origem DOCUMENT: caminho no repositório
    url: str | None = None  # origem DOCUMENT: link do arquivo


@dataclass(frozen=True)
class ChunkHit:
    chunk: Chunk
    score: float  # maior é melhor; só para ordenar


def chunk_text(body: str, limit: int) -> list[tuple[str, str]]:
    """(título da seção, texto) por seção `## `/`# `; seção maior que `limit` é cortada em
    parágrafos; nunca devolve trecho vazio."""


# MemoryContext ganha documents: tuple[ChunkHit, ...] = () (trechos de documentação), e
# memory_text os mostra num bloco <documentacao> com o caminho e o link.


# grolla.application.memory_store (acréscimos)
EmbeddingKind = Literal["query", "passage"]


class TextEmbedder(Protocol):
    """Embeddings de texto; roda localmente. Mesmo modelo para consulta e trecho."""

    @property
    def model(self) -> str: ...

    async def embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]:
        """Um vetor normalizado por texto, na mesma ordem; levanta EmbeddingError."""


class EmbeddingError(Exception): ...


@dataclass(frozen=True)
class Query:
    text: str
    vector: Sequence[float] | None  # None: só a busca textual (RAG desligado)
    model: str | None  # o modelo do vetor; só trechos desse modelo entram


# MemoryStore ganha:
async def context_for(
    self, services, alert_names, text, query: Query | None = None
) -> MemoryContext:
    """Como hoje; com `query.vector`, os episódios depois do determinístico vêm por RRF de vetor
    e texto, e `documents` traz até DOCUMENT_LIMIT trechos de documentação dos `services`."""


async def search_chunks(self, subject: str | None, query: Query, limit: int) -> list[ChunkHit]:
    """Busca híbrida em todos os trechos (memória e documentação) ativos do modelo da query."""


async def unindexed(self, model: str, limit: int) -> list[Memory]:
    """Registros ativos sem trechos deste modelo ou mudados depois da última indexação."""


async def replace_chunks(
    self, memory_id: UUID, model: str, chunks: Sequence[tuple[Chunk, Sequence[float]]], at: datetime
) -> None: ...
async def replace_documents(
    self, service: str, model: str, chunks: Sequence[tuple[Chunk, Sequence[float]]], at: datetime
) -> None:
    """Troca todos os trechos de documentação do serviço (arquivo removido some)."""


async def drop_stale_chunks(self, model: str) -> int:
    """Apaga trechos de registros esquecidos e de outro modelo; devolve quantos."""


# grolla.application.investigation (acréscimos)
@dataclass(frozen=True)
class RepositoryDocument:
    path: str
    url: str | None
    content: str


# ChangeHistory ganha:
async def documents(self, service: str, max_files: int, max_bytes: int) -> list[RepositoryDocument]:
    """Os `.md` do repositório do serviço no branch padrão, menores primeiro, até os limites;
    serviço sem repositório devolve []; levanta ToolUnavailableError."""


# Toolbox ganha embedder: TextEmbedder | None (keyword); com ele, `recall` usa search_chunks.


# grolla.application.memory_retrieval (novo)
@dataclass(frozen=True)
class RagPolicy:
    model: str
    chunk_chars: int = 1200
    max_doc_files: int = 30
    max_doc_bytes: int = 300_000
    batch: int = 50  # registros por rodada do job


async def index_memory(
    *, memory: MemoryStore, embedder: TextEmbedder, clock: Clock, policy: RagPolicy
) -> int:
    """Indexa até `batch` registros pendentes e limpa os trechos velhos; devolve quantos."""


async def index_documents(
    *,
    service: str,
    memory: MemoryStore,
    changes: ChangeHistory,
    embedder: TextEmbedder,
    clock: Clock,
    policy: RagPolicy,
) -> int:
    """Indexa os `.md` do serviço; devolve quantos trechos."""


async def query_for(embedder: TextEmbedder | None, text: str) -> Query | None:
    """A consulta com vetor; None sem embedder. Falha do embedder vira consulta só textual."""


# memory_for(memory, incident, alerts, embedder=None) monta a Query do incidente.
# MemoryPolicy ganha rag: RagPolicy | None. run_next_study chama index_documents depois de cada
# serviço estudado, quando há RAG.
```

**Schema (migração 0007):**
- `CREATE EXTENSION IF NOT EXISTS vector`.
- `memory_chunks`:
  - colunas: `id bigserial`, `origin`, `subject`, `memory_id uuid REFERENCES memories`, `kind`,
    `path`, `url`, `heading`, `text`, `model`, `embedding vector`, `indexed_at`, mais
    `search tsvector` gerado em português com índice GIN;
  - índices em `(memory_id)` e em `(origin, subject)`.
- Sem índice ANN: o volume é de milhares de trechos, e a distância exata (`<=>`) leva
  milissegundos. A coluna `vector` sem dimensão aceita trocar de modelo, e só se compara com
  trechos do mesmo `model`.

**Config (`memory.rag`):** `model` (padrão
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensões), `chunk_chars`
(200 a 8000, padrão 1200), `max_doc_files` (padrão 30) e `max_doc_bytes` (padrão 300000).

**Worker:** job `index_memory` a cada 30 s, na fila `analysis`, com lock próprio, só com
`memory.rag`. O modelo é baixado no build da imagem para `/opt/grolla/models`, e o adapter carrega
só os arquivos locais.

**Imagens:** `pgvector/pgvector:pg16` no `docker-compose.yml`, no `deploy/compose.yml`, no
testcontainers (`conftest.py` da raiz) e no compose do laboratório.

## Decisions

**1. Embeddings locais com fastembed (ONNX), atrás de `TextEmbedder`.** Roda em CPU, sem torch, a
imagem cresce de 200 a 300 MB e os textos não saem da máquina.
- *Alternativa:* Voyage AI. Descartada porque é outro fornecedor, pago e não liberado no
  trabalho.
- *Alternativa:* sentence-transformers com torch. Descartada por pesar 1 GB ou mais na imagem.
- O port isola a escolha: trocar o modelo ou o motor não muda casos de uso nem SQL.

**2. pgvector no Postgres atual.** Mantém tudo num banco, numa transação e num backup.
- *Alternativa:* um banco vetorial dedicado (Qdrant, Chroma). Descartada: é mais um serviço para
  um volume pequeno.
- *Mais simples considerada:* vetores em `jsonb` com a similaridade calculada em Python.
  Descartada: o laboratório é justamente o RAG de verdade, e o pgvector já resolve a distância no
  SQL.

**3. Distância exata, sem índice ANN.** Com milhares de trechos, a busca exata é rápida e não erra.
HNSW e IVFFlat exigem dimensão fixa e calibração. Entram só se o volume crescer.

**4. Indexação assíncrona por job no worker, não no `save`.** A api grava notas e edições e não
carrega o modelo. O job pega o que mudou (`updated_at` > `indexed_at`) e reindexa em lote. O
atraso de até 30 s é aceitável.
*Alternativa:* embedding na transação do `save`. Descartada porque exige o modelo na api e deixa
o pedido HTTP mais lento.

**5. Recorte por seção.** Os dossiês já têm seções fixas (`## O que faz`, `## Alertas`…). Um trecho
por seção dá unidades com sentido; seções longas são cortadas por parágrafo. Episódios e notas são
curtos e viram um trecho cada.
*Alternativa:* janelas fixas de tokens com sobreposição. Descartada: quebra seções e piora a
leitura do trecho.

**6. Busca híbrida em SQL, com RRF.** As duas listas saem de CTEs: os k mais próximos pelo vetor e
os k melhores por `ts_rank`. A fusão é `score = Σ 1/(60 + posição)`. O critério determinístico
continua antes, como hoje.
*Alternativa:* soma ponderada de scores. Descartada: as escalas de cosseno e `ts_rank` não se
comparam, e o RRF dispensa calibração.

**7. Documentação indexada no estudo.** O estudo já percorre cada serviço; indexar os `.md` ali não
cria outro gatilho.
*Alternativa:* um job de sincronização dos repositórios. Descartada por não haver segundo uso;
reestudar ou reaprender já atualiza.

**8. RAG desligável.** Sem `memory.rag` não há modelo, job nem vetor, e a busca é a textual de hoje.
Assim o e2e antigo, os testes e quem não quiser a imagem maior continuam como estão.

**9. Eval de recuperação sem LLM.** O que se mede é a recuperação. O eval sobe Postgres com pgvector
(testcontainers), carrega um corpus fixo de episódios com distratores e compara a posição do
certo na textual e na híbrida. Custa zero e roda em minutos. O eval da análise com o LLM real ganha
o caso de palavras diferentes com o contexto que a busca escolheu.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| `grolla-dominio` | `Chunk`, `ChunkHit`, `chunk_text`, `MemoryContext.documents` e `memory_text`; `application.memory_retrieval`; `memory_for` e `recall` com a busca híbrida; `index_documents` no estudo; fakes (`FakeTextEmbedder` determinístico, `MemoryStore` em memória com trechos), suítes de contrato de `TextEmbedder`, `ChangeHistory.documents` e dos métodos novos do `MemoryStore`; job `index_memory` |
| `grolla-persistencia` | migração 0007, métodos novos do `MemoryStore` com o SQL híbrido |
| `grolla-integracoes` | adapter `grolla.adapters.fastembed` (só arquivos locais); `ChangeHistory.documents` no Azure DevOps e no fake dele, com `docs/runbook.md` |
| `grolla-e2e` | cenário: o runbook indexado aparece na análise e o `recall` acha trecho com outras palavras |

O agente pai faz:
- o contrato e as dependências (`fastembed` no `grolla-core`, `uv.lock`);
- as imagens do Postgres;
- o Dockerfile do worker com o modelo embutido;
- a config e o bootstrap;
- o eval de recuperação;
- o laboratório e a integração.

## Risks / Trade-offs

- [Imagem do worker maior e build mais lento] → o modelo pequeno (cerca de 220 MB) é baixado
  numa camada própria, que fica em cache.
- [Modelo fraco em termos de domínio (pool, 5xx)] → a busca é híbrida, então a textual cobre os
  termos exatos e o vetor cobre a paráfrase; o eval mede, e trocar o modelo é config + reindexação.
- [Prompt injection pela documentação indexada] → os trechos vão num bloco marcado como dado,
  como o `read_file`; a documentação não muda regra nem aciona ninguém.
- [Troca da imagem do banco no deploy] → é o mesmo Postgres 16; o `deploy/README.md` explica a
  troca antes de subir a versão. No RDS, a extensão já existe.
- [CPU da t3.small] → os lotes do job são pequenos e o estudo é diário; embedding de consulta
  leva milissegundos.

## Migration Plan

1. Trocar a imagem do Postgres para `pgvector/pgvector:pg16` (o volume continua).
2. Subir a versão: a migração 0007 cria a extensão e a tabela.
3. Ligar `memory.rag` no YAML: o job indexa a memória existente, e o próximo estudo indexa a
   documentação.

**Rollback:** tirar `memory.rag` volta à busca textual; a tabela fica sem uso.

## Open Questions

- O modelo padrão é bom o suficiente em português técnico? O eval de recuperação decide. A
  alternativa maior é `intfloat/multilingual-e5-large`, com 1024 dimensões e cerca de 2 GB.
