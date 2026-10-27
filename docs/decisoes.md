# Registro de decisões

As decisões que atravessam o projeto, com o racional. O detalhe de cada uma está no `design.md` da
change arquivada em `openspec/changes/archive/`. Novas decisões entram no fim, com data e change.

| # | Data | Change | Decisão | Racional |
|---|---|---|---|---|
| 1 | 25/10 | fundação | Arquitetura hexagonal com ports por capacidade e um adapter por fornecedor, imposta por import-linter | As ferramentas (SigNoz, Teams, Spike.sh, AWS) podem mudar; trocar um fornecedor não pode mexer no núcleo, nos prompts nem nos evals |
| 2 | 25/10 | fundação | Núcleo determinístico decide estado, severidade e acionamento; o LLM só investiga | Previsibilidade e auditoria: o que aciona pessoas não pode depender de um modelo |
| 3 | 25/10 | fundação | Agente somente leitura: sugere e nunca executa; quem encerra é um humano | Segurança num ambiente de pagamentos; reduz o raio de um erro do modelo |
| 4 | 25/10 | S2 | Log de eventos imutável no Postgres e outbox para efeitos externos | Auditoria completa, entrega com retentativa e a base da memória do agente |
| 5 | 25/10 | S3 | Só Anthropic como LLM, com modelo por etapa no YAML | Único fornecedor liberado no trabalho do autor; trocar de modelo é config |
| 6 | 25/10 | S3 | Toda falha da investigação vira análise parcial, nunca silêncio | O time sempre recebe o que deu para avaliar |
| 7 | 26/10 | war room | Chat do agente com teto de tokens por resposta e por incidente, e fechamento no orçamento | Custo previsível e resposta que sempre chega |
| 8 | 26/10 | war room | Respostas do chat curtas: uma frase, até 3 evidências e uma sugestão | Pedido do usuário: texto demais atrapalha durante a crise |
| 9 | 27/10 | memória viva | Memória por assunto (ambiente, serviço, integração) com dossiê vivo, mais episódios e notas | Um caderno por sistema é o que um SRE veterano mantém; uma lista de fatos vira contexto ruim |
| 10 | 27/10 | memória viva | Aprende sozinho e o time corrige (aprendido × confirmado), sem fila de aprovação | Pedido do usuário; a memória fica viva desde o primeiro dia, e o confirmado prevalece |
| 11 | 27/10 | memória viva | Estudo do ambiente orquestrado pelo código: alertas do domínio, serviços, repositórios, pipelines, AWS e traces | Custo previsível por serviço, retomada e falha isolada; um agente solto não teria teto |
| 12 | 27/10 | memória viva | Nada é apagado: esquecer e reaprender guardam versões | O time pode desfazer qualquer coisa que o agente ou alguém fez |
| 13 | 27/10 | RAG | Embeddings locais (fastembed/ONNX) com os modelos embutidos na imagem | Sem fornecedor novo e sem rede em produção; a Anthropic não tem embeddings |
| 14 | 27/10 | RAG | pgvector no Postgres existente, com distância exata e sem índice ANN | Um banco, uma transação e um backup; com milhares de trechos a busca exata leva milissegundos |
| 15 | 27/10 | RAG | Busca híbrida: determinístico primeiro, depois RRF de vetor e texto | No incidente, mesmo serviço e regra é o sinal mais forte; o vetor acha a paráfrase e o texto acha o nome exato |
| 16 | 27/10 | RAG | Texto da busca com OU e sem os nomes do serviço e da regra | Com E nada casava; com OU sem filtro, as palavras genéricas afogavam o vetor. Medido no eval |
| 17 | 27/10 | RAG | Modelo `small` como padrão | Medido: o e5-large comprime as similaridades em português técnico e falhou no aceite |
| 18 | 27/10 | S4 | Acionamento do on-call (Spike.sh) fica para depois | Precisa de conta, API key, política de escalonamento e webhook de ack para testar ponta a ponta |
| 19 | 27/10 | S5 | Ciclos de acompanhamento por varredura (`schedule_cycles` a cada 30 s), não por timer por incidente | Reiniciar o worker não perde nada, e a antecipação por alerta ou mensagem é só um campo |
| 20 | 27/10 | S5 | O modelo devolve dados (tendência, taxa de erro); o núcleo decide card, proposta e impacto | Mantém "o núcleo decide": o agente propõe mitigar ou encerrar, e a pessoa confirma |
| 21 | 27/10 | S5 | Mitigado volta para em tratamento só quando um alerta volta a disparar | É fato objetivo; o "piorou" do modelo é julgamento e só vira card |
| 22 | 27/10 | S5 | Proposta de mitigar só em tratamento | Mitigar pressupõe alguém conduzindo, como na ação humana |
| 23 | 27/10 | S5 | Impacto financeiro estimado por valor por minuto no YAML vezes a taxa de erro | Simples, auditável e sempre rotulado "estimado"; métrica de negócio real fica para quando existir |
| 24 | 27/10 | S6 | Métricas (TTD, TTA, TTM, TTR) e timeline do post mortem sem LLM | São fatos do log; números e horários são os primeiros a sair errados de um modelo |
| 25 | 27/10 | S6 | PDF com fpdf2 (puro Python, texto em latin-1) atrás de `DocumentRenderer` | Sem dependências nativas na imagem slim, como Cairo e Pango, que o WeasyPrint exigiria |
| 26 | 27/10 | S6 | Ciclo de vida no domínio: aprovar exige dono e prazo; encerrar exige itens concluídos ou aceitos | Regra de negócio pura, testável, e a página só mostra a mensagem |
| 27 | 27/10 | S6 | Falha do modelo no rascunho grava o post mortem com as seções determinísticas | O time sempre tem o documento; "regenerar" completa depois |
| 28 | 27/10 | S7 | Métricas de acerto só de respostas humanas (hipótese certa, minutos, memória ajudou, "era o mesmo") | Auditável, sem um segundo modelo julgando o primeiro |
| 29 | 27/10 | S7 | Post mortem aprovado reescreve o episódio como confirmado, pelo mesmo caminho do aprendizado | É a fonte mais confiável; sem fila nova |
