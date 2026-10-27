## Why

No laboratório, o war room tinha 4.340 px de altura no desktop e 8.335 px no celular. A causa raiz
aparecia só depois de todo o painel de ações, as hipóteses saíam duas vezes (na análise e na
validação), havia dois planos de ação e a linha do tempo misturava "card enviado ao canal" com o que
as pessoas fizeram. Quem está no plantão precisa ler, de cima para baixo e num relance: o que está
acontecendo, quanto dói, o que fazer agora e como o Grolla chegou lá. A proposta visual aprovada pelo
usuário está no canvas "War room do Grolla — proposta de redesenho" (`docs/initial-spec.md`, war
room).

## What Changes

- **Barra do incidente fixa no topo.** Severidade, título e serviços; à direita, a pílula do alarme
  (disparando pulsando, normalizado, voltou), a duração, o IC, **um botão** com o próximo passo e o
  menu "⋯" com as ações raras (assumir no lugar de alguém, reabrir, severidade, juntar com…,
  descartar, enviar transcrição). Abaixo, o ciclo numa linha fina, com o horário de cada etapa.
- **Cartão "Agora".** A causa raiz confirmada numa frase grande ou, sem ela, a hipótese mais provável
  ou "investigando"; os números do incidente (desde quando, serviços afetados, taxa de erro do último
  ciclo, impacto); o primeiro passo do plano em destaque, com o comando copiável quando a ação traz
  um, e os outros passos recolhidos; e as sugestões do Grolla (juntar, já vi isso) como linhas
  discretas.
- **"Como o Grolla chegou lá".** Cada hipótese aparece uma vez, com o veredito da validação, a
  confiança, uma linha de motivo e as evidências recolhidas. O descarte da hipótese continua nela.
  As consultas da análise e da validação ficam juntas, recolhidas.
- **Linha do tempo** com "Registre o que você fez" no topo, filtros (Tudo, Pessoas, Alertas, Grolla)
  e os eventos de sistema escondidos atrás de "mostrar N eventos do sistema". Os alertas ficam numa
  lista compacta logo abaixo, para onde a pílula do alarme leva.
- **Chat fixo à direita**, acompanhando a rolagem.
- **Celular:** atalhos para as seções (Agora, Investigação, Linha do tempo, Grolla) e o botão do
  próximo passo fixo embaixo.
- **Visual:** ícones desenhados no lugar de emojis, cor só onde significa algo, mais respiro; mesma
  identidade (mascote, fontes, cores, tema escuro).

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `incident-pages`: a organização do war room (barra, "Agora", investigação, linha do tempo), a
  pílula do alarme no lugar da faixa, as hipóteses com o veredito num lugar só.

## Non-goals

- **Mudar o que o agente produz.** O plano continua numa lista só (a separação entre normalizar e
  corrigir foi cancelada pelo usuário); a tela destaca o primeiro passo.
- **Gráfico da taxa de erro no cartão "Agora".** O número vem do último ciclo de acompanhamento;
  desenhar a série pediria uma consulta à telemetria a cada carga da página.
- **Mudar rotas, eventos ou o polling.** Só a apresentação muda.

## Impact

- `grolla-api`: `templates/` do war room (`incident.html`, `_war_room.html`, `_live.html`,
  `_timeline.html`, `_chat.html`), `war_room.py` (o que a tela precisa já calculado),
  `static/war-room.css` e `static/war-room.js`, e os testes da página.
- `e2e/scenarios`: só os trechos de texto que mudaram.
