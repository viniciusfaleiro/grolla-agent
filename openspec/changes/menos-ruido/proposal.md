## Why

O agrupamento é determinístico por serviço e janela. Quando o mesmo problema derruba dois serviços (o banco caiu e tanto `pedidos-api` quanto `captura-api` alertam), nascem dois incidentes, dois war rooms e dois IC. E alerta que nenhuma regra classifica cai na severidade padrão, mesmo quando é grave (roadmap S8, "Menos ruído").

## What Changes

- **Sugestão de junção pelo agente.** Quando um incidente novo abre com outro incidente aberto de outro serviço na janela, um job pede ao modelo, sem ferramentas e com a memória (dossiês e integrações), se é o mesmo problema. Com "sim", vai para a timeline, o war room e o canal "Parece o mesmo problema que <incidente> — juntar?", com o motivo.
- **Juntar é humano.** Nova ação do war room "Juntar com…": o incidente absorvido vira descartado com o motivo "juntado em <outro>", os alertas, serviços e a severidade passam para o principal (a severidade só sobe), e os dois guardam o evento.
- **Severidade sugerida quando nenhuma regra casa.** O incidente aberto pela severidade padrão recebe, no mesmo job, a sugestão de severidade do modelo com o motivo. Na dúvida, o nível mais alto. A sugestão **só sobe** a severidade, aplicada automaticamente e registrada na timeline com "sugerida pelo agente: <motivo>"; nunca abaixa.
- **Desligável** pela seção `noise` do YAML.

## Capabilities

### New Capabilities
- `noise-reduction`: sugestão de junção e severidade sugerida.

### Modified Capabilities
- `war-room-actions`: ação "Juntar com…".
- `team-config`: seção `noise`.

## Non-goals

- **Juntar sozinho.** O modelo sugere; quem junta é uma pessoa.
- **Separar incidentes.** Fica para depois (sem caso de uso no piloto).
- **Abaixar severidade pelo modelo.**
- **Agrupar no webhook.** O webhook continua determinístico e rápido; a triagem roda no worker.

## Impact

- `grolla-core`: domínio da triagem (sugestões, junção, severidade só sobe), caso de uso `run_next_triage`, fila no Postgres (migração 0010), `LanguageModel.triage` (Anthropic), card no Teams.
- `grolla-worker`: job da triagem.
- `grolla-api`: aviso de junção e ação "Juntar com…" no war room.
- `e2e/fakes`: LLM fake com a etapa `triage`.
- Custo: uma chamada curta, sem ferramentas, por incidente novo que tenha outro aberto na janela ou que abriu sem regra.
