## Context

O cartão "Agora" mostra `Situation.text` com a classe `agora-text` (fonte de título). Com causa confirmada, o texto é
`verification.root_cause`, um parágrafo longo. Os vereditos da validação já trazem `hypothesis`, um título de uma linha.

## Goals / Non-Goals

**Goals:** título curto em destaque e o texto completo abaixo, para todas as situações que têm texto.

**Non-Goals:** pedir ao LLM um campo novo de título; mudar o card do Teams ou o post mortem.

## Decisions

- **Título = título da hipótese confirmada.** O veredito "confirmada" já traz um título curto. Sem veredito confirmado, a primeira
  frase da causa raiz. Na hipótese mais provável, o título da hipótese do topo, com o resumo abaixo. Quando o título e o texto
  são iguais, o texto não se repete.
- **Título até a primeira vírgula.** No lab, os títulos das hipóteses trazem uma segunda oração depois da vírgula
  ("…, provavelmente por mudança manual fora do pipeline") e ocupam duas linhas. O título é cortado ali quando sobram
  pelo menos 20 caracteres; a explicação completa continua abaixo, então nada se perde.
- **Alternativa mais simples considerada:** só diminuir a fonte do parágrafo. Descartada porque o usuário quer ler a causa
  num relance, e isso pede uma frase curta, não letra menor.
- **Alternativa descartada:** um campo `root_cause_title` pedido ao LLM. Muda prompt, schema, banco, fakes e evals, e custa
  tokens, para algo que o título da hipótese já entrega.

## Risks / Trade-offs

- Um título sem vírgula pode ainda ter duas linhas. Ele continua bem menor que o parágrafo.
