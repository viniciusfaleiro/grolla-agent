## MODIFIED Requirements

### Requirement: Página do incidente é o war room
A página do incidente SHALL ter uma barra do incidente no topo, fixa ao rolar no computador, com a severidade, o título, os serviços, a pílula do alarme, o estado, o ciclo, o IC, a duração e TTA, TTM e TTR, e o botão do próximo passo. Abaixo, nesta ordem de leitura:
1. o cartão "Agora": a causa raiz confirmada em destaque, com um título curto em letra grande (o título da hipótese confirmada, até a primeira vírgula) e a explicação completa em texto normal logo abaixo, ou, sem ela, a hipótese mais provável com a confiança, também com o título em destaque e o resumo abaixo, "validando as hipóteses", "o Grolla está investigando" ou "causa raiz não confirmada" com o que faltou; os números do incidente (desde quando, serviços afetados, a taxa de erro do último ciclo de acompanhamento quando houver, o impacto quando houver); o primeiro passo do plano em destaque, com o título "Para voltar ao normal" quando a causa está confirmada, e os outros passos recolhidos; todo passo com comandos mostra o passo a passo numerado, e cada comando vem com "Onde rodar: <lugar>", o botão Copiar e o botão "Apliquei"; um comando já confirmado mostra "aplicado por <autor> às HH:MM" (verificação antiga, sem comandos próprios, usa o primeiro trecho entre crases da ação, sem "onde rodar"); e as sugestões do Grolla (juntar com outro incidente, já vi isso);
2. "Como o Grolla chegou lá": as hipóteses com o veredito, as evidências e as consultas;
3. a linha do tempo, com o registro do que a pessoa fez no topo;
4. os alertas.

O chat com o agente SHALL ficar ao lado, acompanhando a rolagem no computador.

O war room SHALL pedir um nome na primeira visita e guardá-lo num cookie, que identifica o autor das ações e mensagens.

O chat e a timeline SHALL se atualizar sozinhos a cada poucos segundos, sem recarregar a página, sem apagar o que a pessoa está digitando nos formulários.

A linha do tempo SHALL poder ser filtrada por pessoas, alertas e Grolla, e os eventos de sistema (cards enviados, pedidos de análise e de validação) SHALL ficar recolhidos atrás de "mostrar N eventos do sistema".

#### Scenario: Primeira visita
- **WHEN** alguém abre o war room sem ter informado o nome
- **THEN** a página pede o nome antes de permitir ações e mensagens

#### Scenario: Resposta do agente aparece sozinha
- **WHEN** o agente responde uma pergunta enquanto a página está aberta
- **THEN** a resposta aparece no chat em poucos segundos, sem recarregar a página

#### Scenario: Alerta resolvido aparece sozinho
- **WHEN** um alerta do incidente resolve enquanto a página está aberta
- **THEN** em poucos segundos a lista de alertas mostra o alerta como resolvido, sem recarregar a página

#### Scenario: Agora antes da investigação
- **WHEN** alguém abre um incidente com causa raiz confirmada e plano de ação
- **THEN** o cartão "Agora" mostra a causa uma vez só, acima de "Como o Grolla chegou lá", com "Para voltar ao normal" e o primeiro passo

#### Scenario: Eventos de sistema recolhidos
- **WHEN** a linha do tempo tem 6 eventos de cards enviados e pedidos de análise
- **THEN** eles ficam escondidos atrás de "Mostrar 6 eventos do sistema"

#### Scenario: Causa raiz com título curto
- **WHEN** a validação confirma a hipótese "Limite de CPU muito baixo causando throttling" e escreve a causa raiz em um parágrafo longo
- **THEN** o cartão "Agora" mostra "Limite de CPU muito baixo causando throttling" em letra grande e o parágrafo da causa raiz em texto normal abaixo

#### Scenario: Causa sem hipótese confirmada
- **WHEN** a validação escreve a causa raiz sem nenhum veredito "confirmada"
- **THEN** o título em destaque é a primeira frase da causa raiz e o texto completo fica abaixo

#### Scenario: Título longo é cortado na vírgula
- **WHEN** a hipótese confirmada é "Limite de CPU muito baixo causando throttling, provavelmente por mudança manual fora do pipeline"
- **THEN** o título em destaque é "Limite de CPU muito baixo causando throttling" e a explicação completa fica abaixo

#### Scenario: Comando com onde rodar
- **WHEN** a causa está confirmada e a primeira ação traz o comando `kubectl scale deploy/antifraude-api --replicas=2 -n loja` para rodar no "cluster loja-prod, namespace loja"
- **THEN** o "Agora" mostra "Para voltar ao normal", a ação, o comando com o botão Copiar e "Onde rodar: cluster loja-prod, namespace loja"

#### Scenario: Passo com dois comandos
- **WHEN** a primeira ação traz dois comandos, o primeiro no "Azure DevOps, pipeline pedidos-api-cd" e o segundo no "cluster loja-prod, namespace loja"
- **THEN** o passo mostra os dois numerados, nessa ordem, cada um com o seu "Onde rodar" e o seu botão Copiar

#### Scenario: Comando já aplicado
- **WHEN** "Ana" confirmou às 15:31 o primeiro comando do passo
- **THEN** o comando mostra "aplicado por Ana às 15:31", e os outros comandos do passo seguem com "Apliquei"
