## Context

A primeira linha de defesa já existe: as ferramentas do agente são de leitura, no código, e as credenciais também
(token de leitura no histórico de mudanças, lista fechada no MCP de telemetria, role de leitura na nuvem e RBAC de
leitura no cluster). Mesmo convencido, o modelo não tem como executar nada. Faltam quatro coisas:

1. **Escopo:** chat e console respondem qualquer assunto.
2. **Injeção indireta:** resultados de ferramenta e memória entram no contexto sem marca de origem.
3. **Envenenamento da memória:** `remember` grava como `confirmado` o que a pessoa escreveu no chat, e a nota passa a
   pesar em toda investigação.
4. **Saída:** o comando sugerido aparece com o botão de copiar, sem aviso quando é destrutivo.

O login (Entra ID) fica no S12. Até lá, a defesa de entrada é o limite por pessoa e a rede (documentado no `deploy/`).

## Goals / Non-Goals

**Goals:** recusar fora do domínio e pedido de execução com texto fixo; marcar dados externos; impedir nota-ordem;
selo em comando destrutivo; limite por pessoa; conferir a capacidade de leitura no `make check`; evals de ataque;
recusas em `/metricas`; prompts neutros de domínio.

**Non-Goals:** autenticação (S12); um classificador separado antes de cada mensagem; filtrar a saída do modelo com
outro modelo; bloquear o comando destrutivo (o time precisa poder rodá-lo, só com aviso).

## Decisions

- **Escopo na própria resposta, não num classificador.** O schema de saída do chat e do console ganha `scope`. O
  modelo já lê a pergunta para responder, então classificar custa poucos tokens. O núcleo, determinístico, decide o
  que mostrar: fora de `dominio`, troca o texto pela recusa fixa. Assim, um modelo convencido a "obedecer" ainda
  precisaria mentir no `scope` e, ao mesmo tempo, produzir algo útil ao atacante. Como ele não tem ferramenta de
  escrita, o pior caso fica em texto, sem ação.
  - *Alternativa descartada:* um modelo pequeno classifica antes. Custa uma chamada a mais por mensagem e não protege
    mais do que o `scope`, porque o texto do atacante chega ao classificador do mesmo jeito.
- **Recusas fixas no domínio.** `REFUSALS[Scope]` em `grolla.domain.safety`. Texto fixo não vaza nada e não
  depende do modelo.
- **Marca de dado externo no toolbox, não no adapter.** O `Toolbox` da aplicação embrulha todo `ToolResult` com
  `untrusted(fonte, texto)`. Assim vale para qualquer fornecedor de LLM, e o vocabulário continua neutro. O bloco
  `<memoria>` já é declarado como dado; a regra vira uma linha comum (`_DATA_RULES`) em todos os prompts.
  - O fechamento `</dados_externos>` dentro do texto é neutralizado (trocado por `</dados-externos>`), para um log não
    "fechar" o bloco e escrever fora dele.
- **Nota do chat entra `aprendido`.** É a menor mudança que fecha o envenenamento: a nota continua útil para o
  agente, mas uma correção confirmada do time prevalece sobre ela, e o time confirma na página da memória (que já tem
  o botão "Confirmar"). `looks_like_order(text)` recusa as óbvias, por regra fixa e sem LLM.
  - *Alternativa descartada:* fila de aprovação antes de gravar. Exige tela e estado novos, e o `aprendido` já existe
    para isso.
- **Selo destrutivo na api.** `is_destructive(command)` fica em `grolla.domain.safety` (regra pura) e a api usa a
  função ao montar `Step` e ao renderizar código nas mensagens. Copiar pede `confirm()` só nos destrutivos.
- **Limite por pessoa na borda, contando o que já está guardado.** Um port pequeno, `MessageCounter.sent_since`,
  lê as duas tabelas (chat e console) de uma vez, sem tabela nova. A api chama `ensure_under_limit` antes de gravar a
  pergunta; `ask` e `ask_console` não mudam de assinatura.
  - *Alternativa descartada:* um método de contagem em `ChatQueue` e outro em `ConsoleStore`. O limite é um só para
    as duas telas, e cada caso de uso precisaria dos dois ports.
- **Teste de capacidade lendo o código.** Um teste em `packages/grolla-core/tests/architecture/` lista as ferramentas
  do `Toolbox` contra uma lista fixa. Ele também varre com `ast` os adapters `aws` e `kubernetes`: chamadas boto3 só com
  prefixo de leitura e `httpx` só com `get`. É simples, roda sem rede e falha no PR que tentar escrever.
- **Evals de ataque separados.** `evals/seguranca.py` reutiliza o `ClaudeLanguageModel` e os fakes do toolbox
  (`grolla.testing`). Roda só por `make evals-seguranca`, porque gasta crédito.
- **Alternativa mais simples considerada:** só reforçar os prompts. Descartada porque prompt sozinho não dá prova nem
  número: o `scope` com recusa fixa, a nota `aprendido` e o teste de capacidade são determinísticos e testáveis.

## Contratos

```python
# grolla.domain.safety
class Scope(StrEnum): DOMAIN = "dominio"; OFF_TOPIC = "fora_do_escopo"; EXECUTION = "pedido_de_execucao"
REFUSALS: Mapping[Scope, str]   # texto fixo de OFF_TOPIC e EXECUTION
NOT_REMEMBERED: str             # resultado do `remember` recusado
def is_destructive(command: str) -> bool
def looks_like_order(text: str) -> bool
def untrusted(source: str, text: str) -> str   # <dados_externos fonte="..."> ... </dados_externos>

# grolla.domain.chat / console
ChatDraft.scope / ConsoleDraft.scope: Scope = Scope.DOMAIN
ChatMessage.refusal / ConsoleMessage.refusal: Scope | None = None
# grolla.domain.metrics
MetricsFacts.refusals: tuple[datetime, ...]; PeriodMetrics.refused: int

# grolla.application.safety
class MessageCounter(Protocol):
    async def sent_since(self, author: str, since: datetime) -> list[datetime]
class RateLimitedError(Exception): limit: int; retry_at: datetime
async def ensure_under_limit(counter, author, now, per_hour) -> None
# AnalysisPolicy.chat_messages_per_hour = 30  (YAML: investigation.chat_messages_per_hour)

# grolla.adapters.postgres.PostgresMessageCounter; grolla.testing.messages.FakeMessageCounter
# ApiContainer.messages, Services.messages e Services.chat_messages_per_hour na api
# migração 0014: refusal em chat_messages e console_messages; índices (author, at)
# schema da resposta do chat e do console (adapter do LLM): "scope" enum [dominio, fora_do_escopo, pedido_de_execucao]
```

## Risks / Trade-offs

- **O modelo pode errar o `scope`.** Uma pergunta legítima pode ser recusada. A recusa fica registrada e aparece em
  `/metricas`, e os evals medem os dois lados (ataque recusado e pergunta legítima respondida).
- **A regra de "cara de ordem" pode dar falso positivo.** É uma lista curta de padrões. Recusar uma nota legítima só
  pede reescrever como fato ("o p99 sobe às 3h por causa do batch").
- **Notas antigas do chat continuam `confirmado`.** A change não reescreve o passado. O time revê em `/memoria`, que
  mostra a fonte "chat" de cada uma.
