> Change de agente único: o agente pai implementa, porque o escopo é pequeno.

## 1. Implementação

- [x] 1.1 Duração no resumo da normalização (`decide_resolved`), com testes.
- [x] 1.2 `receive_alerts` anexa a validação ao card de normalização, com testes usando os fakes.
- [x] 1.3 Card de normalização no Teams com causa raiz, plano, o que faltou ou validação em andamento; testes.
- [x] 1.4 Cenário e2e: incidente com causa raiz confirmada, alerta resolvido e card de normalização com "Causa raiz".

## 2. Fechamento

- [x] 2.1 Revisar e atualizar os READMEs dos componentes e do pai.
- [x] 2.2 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 2.3 Rodar `make fmt && make check` e garantir tudo verde.
