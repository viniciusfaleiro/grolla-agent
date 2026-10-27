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
| 30 | 27/10 | S8 | Triagem no worker, não no webhook | O caminho do alerta continua determinístico e rápido; a sugestão chega segundos depois |
| 31 | 27/10 | S8 | O agente sugere juntar; quem junta é uma pessoa. O juntado vira descartado com motivo | Mexe no war room de duas pessoas; reaproveita estado e páginas sem um estado novo |
| 32 | 27/10 | S8 | Severidade sugerida só quando nenhuma regra casou, e só sobe | Segue as regras do time ("só sobe", "na dúvida, o mais alto"); nunca baixa |
| 33 | 27/10 | S9 | Console fora de incidente numa fila própria do worker (`console`, concorrência 1) | Uma pergunta de exploração nunca atrasa a investigação de um incidente real |
| 34 | 27/10 | S9 | Nos gráficos, o modelo só pede serviço, sinal e janela; o servidor busca a série e desenha o SVG | Os números vêm do dado, nunca do modelo; SVG no servidor dispensa biblioteca de gráficos |
| 35 | 27/10 | S9 | Incidente aberto pelo console passa pelo mesmo núcleo (`decide_manual`): estado pela severidade, card e análise | Um caminho só para incidente; o console não ganha regras próprias |
| 36 | 27/10 | S9 | Teto diário de tokens do console, além do teto por resposta | Conversa livre é o uso mais fácil de estourar o crédito; custo previsível |
| 37 | 27/10 | S10 | Transcrição por upload de texto (`.vtt`/`.srt`/colado), sem integrar com a ferramenta de reunião | Funciona com Teams, Meet ou Zoom sem conta nova; todas exportam `.vtt` |
| 38 | 27/10 | S10 | O que a call diz das hipóteses é só evento; não muda análise nem estado | "O núcleo decide": quem descarta uma hipótese no war room é a pessoa |
| 39 | 27/10 | S10 | Transcrição longa é cortada no limite e marcada, não recusada | Na crise, metade da call vale mais que nada; o corte aparece na timeline |
| 40 | 27/10 | S10 | Transcrição vai ao modelo entre `<transcricao>` como dado, com defesa explícita no prompt | A fala é texto de terceiros: prompt injection não pode mudar o que o agente faz |
| 41 | 27/10 | S11 | Lista fechada de cotas no adapter (instâncias e armazenamento de RDS, vCPUs sob demanda, ALBs) | Poucas cotas derrubam serviço; listar todas é ruído e custo de API |
| 42 | 27/10 | S11 | AWS Health tolerante ao plano: sem Business/Enterprise, a resposta diz "indisponível" e a investigação segue | A conta do laboratório e muitas contas reais não têm o plano |
| 43 | 27/10 | S11 | Várias contas por STS AssumeRole numa role `grolla-somente-leitura`, com cache da credencial e falha isolada por conta | Nenhuma credencial no YAML; uma conta quebrada não cega o agente nas outras |
| 44 | 27/10 | S11 | Conta citada em cada item só quando há mais de uma conta | Com uma conta, as respostas e os evals de antes não mudam |
| 45 | 27/10 | S11 | Teste de várias contas no laboratório sem criar identidade IAM; a role de verdade fica para o time | Criar usuário e chave na conta é decisão do dono da conta; o isolamento de falha foi provado com uma role inexistente |
| 46 | 27/10 | cluster | Port próprio `ClusterInventory`, fora do port de nuvem | O mesmo adapter serve EKS, AKS, k3s ou kind; o cluster não é da AWS |
| 47 | 27/10 | cluster | Mudanças pelo `managedFields` e pelas revisões do ReplicaSet, não pelo audit log | Existem em qualquer cluster e dizem quando e por qual ferramenta; o audit log (quem) fica para depois |
| 48 | 27/10 | cluster | Vários clusters num adapter só, em paralelo, rotulados e com falha isolada; role por cluster no EKS | Separa contas PCI e não PCI sem mapa serviço → cluster; o rótulo do serviço já diz onde ele roda |
| 49 | 27/10 | cluster | `show_values: false` oculta os valores no próprio adapter | O valor do PCI nunca sai do adapter: nem memória, nem avaliação, nem LLM. Filtrar no prompt confiaria no modelo |
| 50 | 27/10 | cluster | Ligar e desligar cluster só pelo YAML (`enabled`) | Pedido do usuário; um interruptor na web exigiria estado e auditoria |
| 51 | 27/10 | cluster | Secrets nunca lidos, nem os metadados | Listar Secret entrega os valores; a troca aparece como revisão ou reinício |
| 52 | 27/10 | cluster | Token do EKS gerado no adapter (URL pré-assinada do STS), sem AWS CLI na imagem | Imagem menor e um caminho só de credencial, o da nuvem |
| 53 | 27/10 | alarme | A faixa do alarme, a tabela de alertas e o estado viajam fora do lugar na resposta da timeline, sem rota nova | Normalizar e voltar a disparar já são eventos; o polling que já existe só responde quando há evento novo |
| 54 | 27/10 | alarme | Só o bloco de estado é trocado ao vivo, nunca os formulários (`display: contents` mantém o grid) | Trocar o war room inteiro apagaria o que a pessoa digita |
| 55 | 27/10 | alarme | Aviso e título piscando só na transição com a página aberta; notificação do navegador fora | Abrir a página não é novidade; a notificação pede permissão por pessoa e o título já avisa a aba em segundo plano |
| 56 | 27/10 | alarme | Selo da lista por `firing` vazio e `last_alert_at > detected_at`, sem ler eventos | Uma consulta por linha seria cara; incidente aberto pelo console sem alerta não ganha o selo |
