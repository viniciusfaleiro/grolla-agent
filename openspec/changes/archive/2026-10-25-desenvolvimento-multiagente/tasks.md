> Change de um agente só: só toca documentação e configuração do harness.

## 1. Regras

- [x] 1.1 Seção "Desenvolvimento com múltiplos agentes" no `CLAUDE.md`: quando usar, papéis e caminhos, commit de contrato, arquivos do agente pai, comunicação por mensagem, `make check` por agente, integração e e2e pelo agente pai.
- [x] 1.2 Regras novas em `openspec/config.yaml`: seção Contratos no design; grupos de tarefas por agente, grupo 0 de contratos e grupo final de integração; declaração de agente único.
- [x] 1.3 Definições `.claude/agents/grolla-{dominio,persistencia,integracoes,web,e2e}.md`.

## 2. Fechamento

- [x] 2.1 Revisar o README do pai (como contribuir) e o `CLAUDE.md`.
- [x] 2.2 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 2.3 Rodar `make fmt && make check` e garantir tudo verde.
