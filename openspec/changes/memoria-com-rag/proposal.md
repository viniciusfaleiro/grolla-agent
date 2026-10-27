## Why

A memória viva (S3.2) acha casos e conhecimento por critério determinístico (mesmo serviço, mesma
regra) e por busca textual do Postgres. Ela erra quando o mesmo problema volta descrito com
outras palavras ("timeout aguardando conexão" × "esgotamento do pool por rotina de
reconciliação") e não alcança a documentação dos repositórios, que o agente só lê sob demanda,
arquivo por arquivo. Este projeto também é um laboratório do autor para RAG: o objetivo é ter um
RAG completo funcionando de ponta a ponta, medido por eval, mesmo que seja mais do que o volume
atual exige.

## What Changes

- **Busca semântica com embeddings locais.** Um modelo de embeddings multilíngue roda dentro do
  worker, embutido na imagem (sem download em produção e sem fornecedor novo). Fica atrás de um
  port novo, `TextEmbedder`.
- **pgvector no Postgres que já existe.** A imagem do banco passa a `pgvector/pgvector:pg16` no
  compose, no deploy e nos testes (no RDS, a extensão já vem). Migração 0007 cria a extensão e a
  tabela de trechos.
- **Trechos indexados.** Dossiês, episódios e notas são recortados por seção e indexados por um job
  do worker sempre que mudam; esquecer tira os trechos do índice.
- **Documentação dos repositórios na memória.** No estudo de cada serviço, os `.md` do repositório
  são indexados em trechos com o link do arquivo, e o agente passa a recuperar o trecho certo de um
  runbook ou README sem ler o arquivo inteiro.
- **Busca híbrida.** A busca combina:
  - o critério determinístico atual, que continua primeiro;
  - a similaridade vetorial;
  - a busca textual, juntadas por fusão de rankings.

  Vale para os episódios parecidos entregues à análise, a validação e o chat, para os trechos de
  documentação relevantes ao alerta e para a ferramenta `recall`, que passa a devolver trechos.
- **Desligável.** Sem a seção `memory.rag` no YAML, tudo funciona como hoje (busca textual).
- **Eval de recuperação como critério de aceite.** O caso "mesmo problema, palavras diferentes"
  roda num corpus com distratores. O vetor precisa achar o episódio certo entre os 5 primeiros
  onde a busca textual sozinha não acha, sem piorar os outros casos. O eval da análise com o modelo
  real recebe o contexto escolhido pela busca.

## Capabilities

### New Capabilities

- `memory-retrieval`: indexação em trechos, embeddings locais, documentação dos repositórios e
  busca híbrida da memória.

### Modified Capabilities

- `agent-memory`: "Lembrar ao investigar" passa a usar a busca híbrida e a entregar trechos da
  documentação.
- `investigation-tools`: `recall` devolve trechos (memória e documentação) pela busca híbrida.
- `team-config`: seção `memory.rag`.

## Non-goals

- **Fornecedor de embeddings ou banco vetorial gerenciado** (Voyage AI, Pinecone, Qdrant): fora,
  porque só a Anthropic é liberada no trabalho e o pgvector resolve.
- **Reordenação por cross-encoder ou por uma chamada extra ao LLM.** O próprio modelo da
  investigação escolhe entre os candidatos; entra só se o eval pedir.
- **Indexar código-fonte.** Só os `.md`. O código continua sob demanda com `read_file`.
- **Indexar outras fontes** (wiki, Confluence, tickets).
- **Trocar o modelo de embeddings sem reindexar.** Trocar o modelo exige reindexar tudo; o job faz
  isso sozinho quando o nome do modelo muda, mas não há dois índices ao mesmo tempo.
- **Busca vetorial na api.** A página da memória continua com a busca atual; só o worker gera
  embeddings.

## Impact

- `grolla-core`:
  - port `TextEmbedder` (adapter `fastembed`);
  - `ChangeHistory.documents`, para listar e ler os `.md` do repositório;
  - casos de uso de indexação e de busca híbrida;
  - `MemoryStore` com trechos e consulta híbrida;
  - migração 0007.
- `grolla-worker`: job `index_memory`; imagem com o modelo embutido, cerca de 200 a 300 MB a mais.
- Imagem do Postgres `pgvector/pgvector:pg16` no `docker-compose.yml`, no `deploy/compose.yml`,
  no testcontainers e no laboratório.
  - **BREAKING** para quem já tem um banco: o deploy troca a imagem antes de subir a versão nova.
    O volume é compatível, porque é o mesmo Postgres 16 com a extensão a mais.
- `e2e/fakes`: Azure DevOps com mais de um `.md`; o e2e usa o embedder real.
- Evals: eval de recuperação (com Postgres e embedder reais, sem LLM, sem custo) e o caso da
  análise com o contexto recuperado.
- Dependência nova: `fastembed` (ONNX Runtime) no worker.
