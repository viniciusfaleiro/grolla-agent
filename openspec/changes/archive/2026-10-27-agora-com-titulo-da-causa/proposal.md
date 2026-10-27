## Why

No cartão "Agora" do war room, a causa raiz confirmada aparece inteira em letra grande: são quatro ou cinco linhas de texto de título,
difíceis de ler num relance. O usuário pediu um título bem resumido em destaque e o detalhe abaixo.

## What Changes

- O cartão "Agora" mostra um **título curto** em letra grande e a **explicação completa** em texto normal abaixo.
- Com causa confirmada, o título é o da hipótese que a validação confirmou (já é curto, escrito pelo LLM na validação). Sem
  veredito confirmado, é a primeira frase da causa raiz.
- Na hipótese mais provável (validando, sem causa, hipótese), o título é o da hipótese do topo e o resumo da análise fica abaixo.
- Nenhuma chamada nova ao LLM, nenhuma mudança de prompt, de schema ou de banco.

## Capabilities

### New Capabilities

### Modified Capabilities

- `incident-pages`: o cartão "Agora" separa título e explicação.

## Impact

- `grolla_api.war_room` (`Situation` ganha `title`), `_war_room.html`, `war-room.css`, testes do war room.
