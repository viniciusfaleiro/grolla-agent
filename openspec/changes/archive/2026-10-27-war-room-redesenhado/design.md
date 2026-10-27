## Context

O war room é `incident.html`: o fragmento `#war-room` (`_war_room.html`, devolvido pelas rotas de
ação), o chat (`_chat.html`), os alertas e a timeline (`_timeline.html`, com polling de 3 s que traz
fora do lugar o bloco de estado, a faixa do alarme, os alertas, os botões do próximo passo e o
acompanhamento). O desenho aprovado está no canvas da proposta.

## Goals / Non-Goals

**Goals:** a ordem de leitura "o que acontece → quanto dói → o que fazer → como chegou lá → o que já
foi feito", um botão de próximo passo, as hipóteses com o veredito num lugar só, menos ruído na
timeline e a tela usável no celular.

**Non-Goals:** mudar o que o agente produz, rotas, eventos ou o polling.

## Decisions

### 1. A barra do incidente é o bloco de estado que já é trocado ao vivo

`#incident-status` passa a ser a barra: severidade, título, serviços, a pílula do alarme, duração,
IC e o ciclo. `#next-step-main` passa a ter só o botão do próximo passo (`header.primary`) e o "⋯",
que abre um popover com as outras ações permitidas. Os dois continuam vindo no polling da timeline.

- *Alternativa:* uma barra nova com troca própria. Descartada porque a troca ao vivo do estado já
  existe e foi testada no laboratório.
- A barra é `position: sticky` no desktop; no celular ela rola, e o botão do próximo passo fica fixo
  embaixo.

### 2. A faixa do alarme vira uma pílula na barra

Mesmo `alarm_of` e mesmos estados (disparando pulsando, normalizado, voltou), mesmo `#alarm` com
`data-alarm` (o aviso e o título piscando do `war-room.js` continuam iguais). A pílula leva para os
alertas (`#alertas`) e mostra quantos estão disparando; a lista de alertas disparando com o link
"abrir o alerta" fica na seção de alertas.

### 3. "Agora" monta o que já existe, sem consulta nova

`war_room.situation_of(...)` devolve o que o cartão mostra:

- a frase: a causa raiz da validação; sem ela, o resumo da análise com "hipótese mais provável" e a
  confiança da primeira hipótese; com a análise rodando, "o Grolla está investigando"; sem causa
  confirmada no fim, "causa raiz não confirmada" e o que faltou;
- os números: desde (detecção e duração), serviços afetados, a taxa de erro do último ciclo terminado
  (quando há ciclo) e o impacto (quando há);
- o plano: as ações da validação; sem elas, o plano da análise, rotulado "o que o Grolla investigaria";
  o primeiro passo em destaque e os outros recolhidos;
- o comando do passo: o primeiro trecho entre crases da ação, com o botão "Copiar".

- *Alternativa:* série da taxa de erro com gráfico. Descartada (proposta, Non-goals).

### 4. Uma lista de hipóteses com o veredito

Para cada hipótese da análise, o veredito da validação com o mesmo título (comparação sem caixa e
sem espaços nas pontas). O motivo é o `reasoning` do veredito; sem veredito, a primeira evidência.
As evidências da hipótese e do veredito ficam recolhidas em "N evidências". Veredito sem hipótese
correspondente aparece no fim, para nada se perder.

### 5. Linha do tempo com filtro no navegador

Cada evento ganha `data-kind` (`pessoa`, `alerta`, `grolla`, `sistema`). Os filtros são botões que o
`war-room.js` usa para esconder os outros tipos; os de sistema (`notification_*`, `analysis_requested`,
`verification_requested`) começam escondidos atrás de "mostrar N eventos do sistema". Sem JS, tudo
aparece.

- *Alternativa:* filtro no servidor com query string. Descartada: o polling troca a timeline a cada
  evento e perderia o filtro.

### 6. Ações raras no "⋯"

Severidade, juntar com…, descartar, transcrição, reabrir e "assumir no lugar de" ficam no popover do
"⋯". Os popovers de motivo (encerrar, descartar, juntar, transcrição) continuam os mesmos.

## Contratos

Sem port, evento, rota ou migração. No `grolla_api.war_room`:

```python
@dataclass(frozen=True)
class Situation:
    kind: str  # "causa", "hipotese", "investigando", "sem_causa", "sem_analise"
    text: str
    confidence: int | None  # % da hipótese mais provável
    missing: str
    steps: tuple[Step, ...]  # primeiro em destaque
    steps_from_analysis: bool


@dataclass(frozen=True)
class Step:
    what: str
    target: str
    expected: str
    link: str | None
    command: str | None  # primeiro trecho entre crases


def situation_of(analysis, verification) -> Situation: ...
def hypothesis_rows(analysis, verification) -> tuple[HypothesisRow, ...]: ...
def event_kind(event) -> str: ...  # pessoa, alerta, grolla, sistema
```

## Risks / Trade-offs

- [Muitos testes da página conferem textos antigos] → Cada um é revisto; o comportamento conferido
  continua, só a cópia muda.
- [Barra sticky alta em telas baixas] → A barra tem duas linhas compactas; no celular ela não é
  sticky.
