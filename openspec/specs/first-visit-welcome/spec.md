# first-visit-welcome Specification

## Purpose
TBD - created by archiving change grolla-se-apresenta. Update Purpose after archive.
## Requirements
### Requirement: Grolla se apresenta na primeira visita
Toda página HTML do `grolla-api` que usa o layout comum SHALL trazer o modal de boas-vindas.

O modal SHALL abrir sozinho quando o navegador ainda não guardou que a apresentação foi vista. Ao fechar o modal, por **Pular**, **Começar** ou Esc, o navegador SHALL guardar que a apresentação foi vista, e o modal não SHALL abrir sozinho de novo naquele navegador.

Se o navegador não deixar guardar (armazenamento bloqueado), o modal SHALL funcionar do mesmo jeito e só voltar a abrir na próxima visita.

O modal SHALL ter `role="dialog"`, ser rotulado pela fala e levar o foco para o botão de avançar ao abrir.

#### Scenario: Primeira visita
- **WHEN** alguém abre `/incidents` num navegador que nunca viu a apresentação
- **THEN** o modal abre com a primeira fala do Grolla por cima da lista

#### Scenario: Segunda visita
- **WHEN** a pessoa fechou o modal e depois abre `/console`
- **THEN** o modal não abre sozinho

### Requirement: Falas e cenas
O modal SHALL mostrar o mascote do Grolla e um balão de fala com 6 falas curtas, nesta ordem:
1. quem ele é;
2. o que acontece quando toca um alarme (incidente, severidade, aviso no Teams);
3. o que ele investiga (métricas, logs, traces, deploys, AWS e cluster);
4. a causa raiz com o comando e onde rodar, deixando claro que quem aplica é o time;
5. o chat do war room;
6. normalizado, post mortem e memória, terminando com o convite para começar.

Embaixo do balão, cada fala SHALL mostrar uma cena com um pedaço da tela que ela descreve. A fala 4 SHALL mostrar o comando num bloco escuro com "Onde rodar:".

Enquanto a fala é digitada, a boca do mascote SHALL abrir e fechar. Na primeira fala, ele SHALL acenar. Parado, ele SHALL piscar de vez em quando.

Um indicador SHALL mostrar a etapa atual entre as 6, e clicar numa etapa SHALL ir direto para ela.

#### Scenario: Fala sobre o comando
- **WHEN** a pessoa chega na quarta fala
- **THEN** o balão diz que o Grolla passa o comando e onde rodar, e a cena mostra o comando no bloco escuro com "Onde rodar:" e "Apliquei"

### Requirement: Navegação rápida
O clique no balão, no mascote ou em **Próximo** durante a digitação SHALL mostrar a fala inteira na hora. O clique depois da fala inteira SHALL passar para a próxima.

As teclas SHALL funcionar assim:
- seta para a direita e Enter fazem o mesmo que o clique;
- seta para a esquerda volta uma fala;
- Esc fecha o modal.

**Pular** SHALL fechar o modal em qualquer etapa. Na última fala, o botão de avançar SHALL dizer **Começar** e fechar o modal.

#### Scenario: Clique durante a digitação
- **WHEN** a pessoa clica no balão enquanto a segunda fala ainda está sendo digitada
- **THEN** a fala aparece inteira, e o próximo clique leva à terceira fala

#### Scenario: Pular
- **WHEN** a pessoa clica em **Pular** na segunda fala
- **THEN** o modal fecha e a página por baixo fica utilizável

### Requirement: Rever a apresentação
A barra do topo SHALL ter o link "Conhecer o Grolla", que abre o modal na primeira fala, mesmo depois de a apresentação ter sido vista.

#### Scenario: Rever
- **WHEN** quem já viu a apresentação clica em "Conhecer o Grolla"
- **THEN** o modal abre na primeira fala

### Requirement: Não interromper uma crise
O modal não SHALL abrir sozinho na página de um incidente que ainda não foi resolvido nem descartado. Nesse caso, a apresentação continua como não vista e abre sozinha na próxima página com o layout comum que não seja de incidente aberto. O link "Conhecer o Grolla" SHALL funcionar também ali.

#### Scenario: Chegou pelo card do Teams
- **WHEN** alguém que nunca viu a apresentação abre o war room de um incidente em tratamento
- **THEN** o war room aparece sem o modal, e o modal abre quando essa pessoa abre a lista de incidentes

### Requirement: Menos movimento
Com `prefers-reduced-motion: reduce`, o modal SHALL mostrar cada fala inteira de uma vez, sem digitação, e o mascote SHALL ficar parado, sem boca mexendo, aceno, piscada nem balanço.

#### Scenario: Reduzir movimento
- **WHEN** o sistema pede menos movimento e o modal abre
- **THEN** a primeira fala aparece inteira e o mascote não se mexe

