## Why

Muito do que se decide num incidente acontece na call: "vamos fazer rollback", "o banco voltou às 03:07", "o Pedro vai olhar o pool". Isso não entra no registro sem alguém digitar, e o post mortem sai sem essas decisões (roadmap S10, "Transcrição de call").

## What Changes

- **Upload da transcrição** no war room: texto colado ou arquivo `.txt`, `.vtt` ou `.srt`, de qualquer ferramenta de reunião, com quem enviou.
- **Extração pelo agente.** Numa chamada sem ferramentas, o modelo extrai decisões, ações, horários e participantes, e cada item vira um evento na timeline com a origem "transcrição", o horário dito na call (quando houver) e o trecho.
- **Reavaliação das hipóteses.** A extração diz o que a call confirmou ou descartou da análise, e isso entra na timeline como evidência. Não muda a análise nem o estado: quem decide é o time.
- **Post mortem com a call.** A timeline do post mortem mostra os itens com a origem "transcrição".

## Capabilities

### New Capabilities
- `call-transcript`: upload, extração, eventos com origem e reavaliação das hipóteses.

### Modified Capabilities
- `post-mortem`: origem "transcrição" na timeline.

## Non-goals

- **Gravar a call ou transcrever áudio.** O time envia o texto que a ferramenta de reunião já gera.
- **Integração direta com o Teams ou o Meet.** Upload manual, sem conta nova.
- **Mudar estado ou severidade pela transcrição.** Ela vira registro e evidência.

## Impact

- `grolla-core`: domínio da transcrição, caso de uso de upload, fila e extração, Postgres (migração 0013), `LanguageModel.extract_transcript` (Anthropic).
- `grolla-worker`: job da extração.
- `grolla-api`: upload no war room e itens na timeline.
- `e2e/fakes`: o LLM fake com a etapa `transcript`.
- Custo: uma chamada por transcrição enviada, com teto de tamanho e de tokens.
