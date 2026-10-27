## Context

A faixa do alarme (`alarme-normalizado-na-tela`) tem os estados normalizado e voltou, calculados
por `alarm_of(incident, events)` no `grolla-api`. Enquanto o alarme dispara sem nunca ter
normalizado, ela não aparece. A tabela de alertas, com status e link, já chega ao vivo pelo polling
da timeline.

## Goals / Non-Goals

**Goals:** a faixa sempre presente num incidente aberto com alerta, vermelha enquanto dispara, com
os alertas e os links.

**Non-Goals:** aviso quando começa a disparar; mudar a tabela de alertas.

## Decisions

### 1. Um estado a mais em `alarm_of`

`AlarmKind.FIRING = "disparando"`, com `at` igual ao horário do primeiro alerta recebido. Ele vale
quando há alerta disparando e nenhuma normalização. O resto segue igual.

- *Alternativa:* ler o estado da tabela de alertas no template. Descartada porque a regra ficaria
  espalhada; `alarm_of` já é o lugar único da situação do alarme.

### 2. Os alertas da faixa saem da lista que o contexto já tem

O contexto ao vivo já traz `alerts`, e a faixa mostra os de `incident.firing` (regra e
`source_url`). Não há consulta nova.

### 3. Sem aviso para "disparando"

O `war-room.js` só avisa para normalizado e voltou. "Disparando" é o estado de abertura; a mudança
de disparando para normalizado avisa como antes.

### 4. Faixa ao lado do título, dentro do bloco de estado, sem botão

Pedido no teste: a faixa no topo ficou feia, e o botão repetia o painel de ações. Ela passa para a
direita do título (abaixo no celular), dentro de `#incident-status`, e deixa de ter troca própria:
vem junto com o bloco de estado. Sem botão.

### 5. Botões do próximo passo também ao vivo

No teste de ponta a ponta, o alarme voltou e reabriu o incidente mitigado, mas os botões continuaram
os de mitigado (Encerrar e Reabrir). A linha de botões (`#next-step-main`, sem campo de texto)
passa a vir no polling da timeline, só para quem tem nome. O campo "Registrar ação" continua fora.

### 6. Alerta listado uma vez

O histórico guarda uma linha por recebimento do alerta; a faixa lista cada fingerprint uma vez.

## Contratos

```python
class AlarmKind(StrEnum):
    FIRING = "disparando"  # novo
    NORMALIZED = "normalizado"
    RETURNED = "voltou"
```

Fragmentos fora do lugar: `#incident-status` (com a faixa `#alarm`), `#alerts` e `#next-step-main`.
Sem port, evento, endpoint ou migração.

## Risks / Trade-offs

- [Muitos alertas disparando alongam a faixa] → A faixa mostra até 3 e "e mais N"; a lista
  completa fica na tabela.
