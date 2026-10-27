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
