## Why

Hoje cada investigação do Grolla começa do zero. Ele não conhece o ambiente que cuida (serviços,
repositórios, pipelines, infraestrutura, integrações), não lembra de nenhum incidente e não guarda
o que o time explica no chat. O que fazia do Grolla humano o melhor SRE do time era conhecer o
ambiente a fundo e lembrar do que já tinha visto. Este slice dá ao agente uma memória viva: uma
base de conhecimento do ambiente que ele monta sozinho ao subir, mantém em dia e reaprende quando
pedirem, e que cresce a cada incidente e conversa (spec de produto, seção "Aprendizado contínuo e
memória do ambiente").

## What Changes

- **Base de conhecimento por assunto** (ambiente, serviço, integração). Cada assunto tem um
  **dossiê** vivo com tudo que importa para troubleshooting: o que faz, repositório e
  documentação, pipeline e deploy, infraestrutura na nuvem, dependências e integrações, alertas (o
  que cada um significa e o que olhar primeiro), comportamento normal, fragilidades e como
  investigar. Também guarda **episódios** (um por incidente encerrado) e **notas** (fatos curtos do
  chat ou do time). Cada registro tem fonte, data, confiança e versões.
- **Estudo inicial do ambiente** na primeira subida, na ordem:
  1. alertas do domínio do time;
  2. serviços desses alertas;
  3. para cada serviço: repositório (README e `.md`), pipelines, infraestrutura AWS e integrações
     vistas nos traces;
  4. síntese: dossiê do ambiente e das integrações.
- **Estudo diário**: relê os alertas, estuda primeiro o que é novo e depois o mais antigo, com teto
  de tokens por rodada.
- **Reaprender do zero** pela página: esquece dossiês e o que o agente aprendeu sozinho, mantém
  episódios e o que o time confirmou, e refaz o estudo. Nada é apagado de fato; tudo pode ser
  restaurado pelo histórico.
- **Aprende sozinho, o time corrige.** O que o agente aprende entra ativo como "aprendido"; o que
  um humano escreve ou confirma fica "confirmado" e prevalece.
- **Aprender ao encerrar.** Incidente resolvido ou descartado vira episódio e atualiza os dossiês,
  numa chamada sem ferramentas.
- **Aprender no chat** com a ferramenta `remember`, com o autor humano como fonte.
- **Lembrar ao investigar.** Análise, validação e chat recebem o dossiê do ambiente, dos serviços e
  das integrações, as notas e até 5 episódios parecidos. Com um caso parecido, a análise mostra
  **"já vi isso"** com o incidente passado.
- **Ferramentas neutras novas:** `recall`, `read_file`, `service_dependencies` e `cloud_overview`.
- **Página "Memória do Grolla"** (`/memoria`): dossiês, episódios e notas, com editar, confirmar,
  esquecer, restaurar, adicionar conhecimento, estudar de novo e reaprender do zero.
- **Roadmap**: este slice (S3.2) entra antes do S4, substitui o S7 e a parte "incidentes
  parecidos" do S5.

## Capabilities

### New Capabilities

- `agent-memory`: base de conhecimento do ambiente — assuntos e registros, estudo inicial e
  diário, reaprender do zero, aprender ao encerrar e no chat, lembrar ao investigar e a página
  `/memoria`.

### Modified Capabilities

- `investigation-tools`: ferramentas `recall`, `read_file`, `service_dependencies` e
  `cloud_overview`.
- `incident-analysis`: a análise recebe a memória e pode apontar "já vi isso" com o incidente
  passado.
- `team-config`: seção `memory` (limites do aprendizado e do estudo, horário do estudo diário).

## Non-goals

- **Post mortems como fonte.** O S6 não existe ainda; quando existir, aprovar um post mortem passa
  a alimentar a memória pelo mesmo caminho do encerramento.
- **Embeddings e busca semântica.** Só Anthropic é liberado e ele não oferece embeddings; a busca
  é full-text do Postgres com o LLM escolhendo.
- **Memória mudar regras.** A memória nunca muda severidade, agrupamento nem aciona ninguém.
- **Card "já vi isso" separado no Teams.** A menção vai no card de análise que já existe.
- **Métricas de acerto do agente por período.** Dependem do post mortem (S6).
- **Episódios dos incidentes já encerrados antes deste slice.** Não são gerados automaticamente,
  para não gastar crédito sem pedido.
- **Indexar repositórios inteiros ou copiar código.** O agente lê sob demanda com `read_file`.
- **Estudar fora do domínio do time.** Só os serviços dos alertas do time e os com atividade e
  repositório.

## Impact

- `grolla-core`: domínio da memória; casos de uso de estudo, reaprendizado, aprendizado e página;
  ports `MemoryStore`, `LearningQueue` e `StudyQueue` (Postgres); `Telemetry` ganha regras de
  alerta, serviços ativos e dependências; `CloudInventory` ganha a visão geral; `ChangeHistory`
  ganha `read_file`; `LanguageModel` ganha `learn`, `study_service` e `synthesize`; prompts e
  ferramentas novas; adapters SigNoz, AWS, Azure DevOps e Anthropic.
- `grolla-core/adapters/postgres`: migração 0006.
- `grolla-worker`: jobs de aprendizado e de estudo.
- `grolla-api`: páginas `/memoria`; "já vi isso" na página do incidente.
- `e2e/fakes`: SigNoz (regras de alerta, serviços, dependências), AWS (visão geral), Azure DevOps
  (arquivos) e LLM fake com as etapas novas.
- Custo de LLM: estudo inicial com teto por rodada e por serviço; uma chamada por incidente
  encerrado; estudo diário com o mesmo teto.
