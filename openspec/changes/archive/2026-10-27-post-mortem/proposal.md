## Why

Depois de resolvido, o incidente vira só um log de eventos. O post mortem, que a spec de produto
pede no mesmo dia, ainda é escrito à mão, quando é escrito. O Grolla já tem tudo para o
rascunho: timeline real, hipóteses confirmadas e descartadas, causa raiz, ações do time, ciclos
de acompanhamento, chat e TTA/TTM/TTR (spec de produto, "Post mortem"; roadmap S6).

## What Changes

- **Rascunho automático ao resolver.** Quando um incidente é resolvido (não descartado), um job
  gera o post mortem com o template da spec. O formato é blameless e fala em fatores
  contribuintes.
  - Seções determinísticas, montadas pelo núcleo sem LLM: métricas (TTD, TTA, TTM e TTR) e
    timeline.
  - Seções escritas pelo modelo numa chamada sem ferramentas: resumo executivo, impacto,
    detecção, fatores contribuintes, hipóteses, o que funcionou, o que não funcionou e onde
    tivemos sorte, plano de ação e avaliação do agente.
- **Edição por seção, com versões e autor.** Toda seção é editável na página, e cada mudança
  guarda a versão anterior.
- **Regenerar seção.** O botão pede ao agente que reescreva uma seção com o que há de novo,
  preservando a edição humana como dado.
- **Plano de ação estruturado.** Cada item tem descrição, dono, prazo, tipo (prevenir, detectar
  antes, mitigar mais rápido) e situação.
- **Ciclo de vida.** Rascunho (agente) → Em revisão (humano) → Aprovado (humano; todo item do
  plano precisa de dono e prazo) → Encerrado (humano; todo item concluído ou aceito).
- **PDF da versão aprovada**, por um port novo, `DocumentRenderer`.
- **Lista de post mortems** com filtros por status, severidade, serviço e período, e alerta de
  itens do plano vencidos.

## Capabilities

### New Capabilities

- `post-mortem`: rascunho, seções, versões, regeneração, plano de ação, ciclo de vida, PDF e
  lista.

### Modified Capabilities

- `team-config`: etapa `post_mortem` em `llm.stages` e o limite de tokens do rascunho.

## Non-goals

- **Work items no Azure DevOps** a partir do plano de ação (fase 2 da spec).
- **Post mortem de incidente descartado.** Alarme falso já vira episódio de ruído na memória.
- **Post mortem alimentando a memória e as métricas de acerto** (S7, slice seguinte).
- **Editor rico.** As seções são markdown simples, renderizado com o `rich_text` que já existe.
- **Assinatura ou fluxo de aprovação com papéis.** Qualquer pessoa identificada aprova (fase 1,
  como o war room).

## Impact

- `grolla-core`:
  - domínio do post mortem (seções, versões, ciclo de vida, métricas e timeline);
  - casos de uso de gerar, regenerar, editar, mudar status e itens do plano;
  - port `PostMortemStore` (Postgres, migração 0009) e port `DocumentRenderer` (adapter `fpdf`);
  - `LanguageModel.draft_post_mortem` e `regenerate_section` (Anthropic).
- `grolla-worker`: jobs do rascunho e da regeneração.
- `grolla-api`: `/postmortems`, `/postmortems/{incident}`, o PDF e o link na página do incidente.
- `e2e/fakes`: o LLM fake responde a etapa `post_mortem`.
- Dependência nova: `fpdf2`, puro Python.
- Custo: uma chamada sem ferramentas por incidente resolvido e uma por regeneração.
