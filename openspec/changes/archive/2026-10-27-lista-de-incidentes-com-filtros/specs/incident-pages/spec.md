## MODIFIED Requirements

### Requirement: Lista de incidentes
O `grolla-api` SHALL servir `GET /incidents`, uma página HTML com os incidentes do mais recente para o mais antigo, cada um com link para a página dele.

Os incidentes abertos SHALL aparecer primeiro, em "Precisa de você agora", cada um com a severidade, o título, a situação do alarme (disparando, ou normalizado há quanto tempo), a causa raiz confirmada ou a hipótese mais provável numa linha, o estado, o IC e o próximo passo. Um incidente aberto há mais de 24 horas sem IC SHALL ser marcado como possivelmente esquecido.

Os encerrados SHALL aparecer numa tabela com a severidade, o título, a causa raiz, a duração, a situação do post mortem e quando encerraram, os 20 mais recentes, com a opção de mostrar todos.

A lista SHALL ser filtrável por severidade, estado, serviço, situação do alarme, IC (os meus ou sem IC), período da detecção e texto no título ou no serviço, com os filtros na URL. A página SHALL mostrar quantos incidentes os filtros deixaram e um jeito de limpar os filtros. Valor de filtro inválido SHALL valer como sem filtro.

Um incidente aberto que teve alerta e não tem nenhum disparando SHALL mostrar o selo "alarme normalizado".

A navegação SHALL mostrar, na aba de incidentes, quantos incidentes abertos têm alarme disparando.

#### Scenario: Dois incidentes
- **WHEN** existem dois incidentes
- **THEN** a página lista os dois, o mais recente primeiro

#### Scenario: Selo de alarme normalizado
- **WHEN** um incidente em tratamento teve o último alerta resolvido e outro incidente ainda dispara
- **THEN** só o primeiro mostra "alarme normalizado"

#### Scenario: Filtrar por serviço e severidade
- **WHEN** alguém abre `/incidents?servico=pagamentos-api&sev=P1`
- **THEN** a página mostra só os incidentes P1 da pagamentos-api e a contagem deles

#### Scenario: Esquecido
- **WHEN** um incidente detectado há 38 horas segue sem IC
- **THEN** o cartão dele diz que pode ter sido esquecido e sugere encerrar
