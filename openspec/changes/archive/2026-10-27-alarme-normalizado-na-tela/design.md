## Context

O war room (`GET /incidents/{id}`) é montado uma vez. Depois, só o chat e a timeline se atualizam:
o `_timeline.html` consulta `GET /incidents/{id}/timeline?visto=N` a cada 3 s, o servidor responde
204 quando não há evento novo e, quando há, devolve a timeline junto com o acompanhamento, o
impacto e a proposta trocados fora do lugar (`hx-swap-oob`).

Alerta resolvido (`alert_resolved`), normalização (`incident_normalized`), alerta recebido ou
agrupado (`alert_received`, `alert_grouped`) e volta de mitigado (`alert_reopened`) já são eventos
do log. O `Incident` tem `firing` (as fingerprints disparando), `detected_at` e `last_alert_at`.
Nada disso chama a atenção hoje: a tabela de alertas e o cabeçalho ficam parados até recarregar a
página.

## Goals / Non-Goals

**Goals:**
- A faixa do alarme (normalizado ou voltou a disparar) com o próximo passo, atualizada sozinha.
- Aviso na tela e título piscando só na transição, com a página aberta.
- Tabela de alertas e bloco de estado ao vivo, sem apagar o que a pessoa digita.
- Selo "alarme normalizado" na lista.

**Non-Goals:**
- Notificação do navegador, som, mudança automática de estado e lista ao vivo (ver a proposta).

## Decisions

### 1. Pegar carona no ciclo da timeline, sem rota nova

A situação do alarme só muda quando chega um evento, e todo evento já faz a timeline responder.
A resposta da timeline passa a levar também, fora do lugar, a faixa (`#alarm`), a tabela de
alertas (`#alerts`) e o bloco de estado (`#incident-status`).

- *Alternativa:* uma rota `/incidents/{id}/estado` com polling próprio. Descartada porque dobraria
  as consultas por aba e repetiria a lógica do "só troca quando há novidade".
- *Alternativa:* SSE. Descartada porque o projeto já resolve o "ao vivo" com polling do HTMX, e
  trocar isso não é o escopo.

### 2. Trocar só o bloco de estado, nunca os formulários

O cabeçalho passa a ter um contêiner `#incident-status` com os chips de estado, a linha de sinal,
as etapas e os fatos (IC, duração, TTA, TTM, TTR). Os formulários (registrar ação, severidade,
encerrar, juntar, chat) ficam fora dele. Assim o texto que a pessoa digita não some.

- *Alternativa:* trocar o `#war-room` inteiro. Descartada pelo motivo acima.
- Os botões de ação do cabeçalho não entram na troca. Se outra pessoa muda o estado, o botão errado
  responde com o erro de ação inválida que já existe, e a faixa e o bloco de estado mostram o
  estado novo. Aceito.

### 3. Situação do alarme calculada no web, a partir do incidente e dos eventos

`grolla_api.war_room` ganha `alarm_of(incident, events) -> Alarm | None`:

- `None` quando o incidente está encerrado ou descartado, ou quando nunca houve alerta (nenhum
  evento `alert_received` ou `alert_grouped`).
- `normalizado` quando `incident.firing` está vazio e há um `incident_normalized`, com `at` igual ao
  horário do último deles.
- `voltou` quando `incident.firing` não está vazio e houve um `alert_received` ou `alert_grouped`
  depois do último `incident_normalized`, com `at` igual ao horário desse alerta.
- `None` nos outros casos: disparando sem nunca ter normalizado.
- O botão da faixa normalizada é a ação em destaque do estado (`header.primary`: assumir, mitigar
  ou encerrar), a mesma do cabeçalho. O domínio só aceita mitigar a partir de em tratamento. A
  faixa "voltou" não tem botão.

É apresentação: fica no `grolla-api`, junto do `header_of`, sem regra nova no domínio.

### 4. Transição no navegador: aviso e título piscando

A faixa leva `data-alarm="normalizado|voltou|"` e o título do incidente. O `war-room.js` guarda o
valor da carga da página. A cada `htmx:oobAfterSwap` do `#alarm`, se o valor mudou para
`normalizado` ou `voltou`, ele:

- mostra um aviso fixo no canto (`role="status"`), que some em 8 s ou no clique;
- troca o título da aba alternando com o original a cada segundo, até `visibilitychange` ou
  `focus`.

Ao abrir a página não há aviso, porque não há transição. Usa só HTML, CSS e JS do projeto, sem
biblioteca.

### 5. "Há X min" sem polling extra

A faixa traz `<time datetime="...">` com o horário. O `war-room.js` atualiza o "há X min" a cada
30 s, a partir desse atributo. Sem JS, a faixa mostra só "às HH:MM".

### 6. Selo na lista sem carregar eventos

A lista mostra "alarme normalizado" quando o incidente está aberto, `firing` está vazio e
`last_alert_at > detected_at`. Um alerta resolvido atualiza `last_alert_at`. Incidente aberto pelo
console sem alerta tem `last_alert_at == detected_at`, então não ganha o selo. A regra fica em
`war_room.alarm_normalized(incident)`, usada pela lista.

- *Alternativa:* `alarm_of` com os eventos de cada incidente da lista. Descartada porque faria uma
  consulta por linha; o campo do incidente já basta.

## Contratos

Modo: um agente só (web e o cenário e2e). Sem commit de contrato.

```python
# grolla_api.war_room
class AlarmKind(StrEnum):
    NORMALIZED = "normalizado"
    RETURNED = "voltou"


@dataclass(frozen=True)
class Alarm:
    kind: AlarmKind
    at: datetime  # da última normalização, ou do alerta que voltou


def alarm_of(incident: Incident, events: Sequence[IncidentEvent]) -> Alarm | None: ...
def alarm_normalized(incident: Incident) -> bool: ...  # para o selo da lista
```

- Fragmentos fora do lugar na resposta da timeline: `#alarm`, `#alerts` e `#incident-status`.
- Sem port, evento, endpoint, migração ou card novo.

## Risks / Trade-offs

- [O e2e não roda JS] → O e2e confere a faixa e o selo pelo HTML da página e da resposta da
  timeline. O aviso e o título piscando ficam cobertos por um teste da página que confere os
  atributos, e pela verificação no laboratório com o navegador headless.
- [Botão de ação desatualizado, quando outra pessoa muda o estado] → A ação inválida já responde
  com erro, e a faixa e o estado mostram o estado novo. Atualizar os botões é trocar o `#war-room`
  inteiro, o que apagaria o que a pessoa digita.
- [`last_alert_at > detected_at` também vale para um alerta agrupado e resolvido] → É o caso
  desejado: o incidente teve alerta e nenhum dispara.
