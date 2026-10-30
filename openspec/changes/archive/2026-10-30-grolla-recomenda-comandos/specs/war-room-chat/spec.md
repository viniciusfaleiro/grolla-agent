## MODIFIED Requirements

### Requirement: Resposta curta e direta
A resposta do agente no chat SHALL ser curta e direta, para não atrapalhar o war room, e SHALL ter como objetivo ajudar o time a resolver o incidente o mais rápido possível:
- a primeira linha responde a pergunta numa frase;
- no máximo três linhas de evidência, com número, horário ou versão e o link quando houver;
- as ações que ajudam a sair da crise, em ordem, quando houver; os comandos dessas ações SHALL vir no passo a passo da resposta (cada um com onde rodar), não no meio do texto;
- quando faltar um dado que o agente não alcança, o pedido ao time de exatamente o que falta e onde conseguir (por exemplo, a saída de um comando ou um print de um painel), para seguir investigando;
- até cerca de 60 palavras de texto, salvo quando pedirem detalhes ou um resumo; o passo a passo de comandos não conta nesse limite.

A resposta SHALL NOT repetir a pergunta, o contexto do incidente ou a análise, nem explicar como o agente investigou. O agente SHALL fazer só as consultas necessárias para responder.

#### Scenario: Pergunta de sim ou não
- **WHEN** "Ana" pergunta "o rollback resolveu?"
- **THEN** a primeira linha responde sim ou não com o número que sustenta, seguida de no máximo três evidências curtas

#### Scenario: Pede um dado que o agente não alcança
- **WHEN** a causa depende de um log que nenhuma ferramenta do agente lê
- **THEN** a resposta diz o que falta e pede ao time para colar aquele log, dizendo onde conseguir

## ADDED Requirements

### Requirement: Comandos no chat
Quando a resposta sugere comandos, o agente SHALL devolvê-los como passo a passo em ordem (até 5, cada um com onde rodar), pelas mesmas regras do plano da validação: só nomes e valores vistos, sem `<placeholder>`, e o núcleo descarta comando de várias linhas, com mais de 300 caracteres ou sem onde rodar. O chat SHALL mostrar cada comando como no plano do war room: "Onde rodar: <lugar>", o bloco escuro com Copiar e o botão "Apliquei", que registra a ação feita com o comando e o lugar. Comando destrutivo SHALL ter o selo e a confirmação ao copiar. Resposta recusada SHALL NOT trazer comandos.

#### Scenario: Como sair da crise
- **WHEN** "Ana" pergunta "quais comandos eu posso executar pra voltar ao normal e aonde?" num incidente com o antifraude-api escalado a zero no cluster lab-k3s
- **THEN** a resposta traz, em ordem, `kubectl scale deployment antifraude-api --replicas=2 -n loja` e `kubectl rollout status deployment/antifraude-api -n loja`, cada um com "Onde rodar: cluster lab-k3s, namespace loja", o botão Copiar e o "Apliquei"
