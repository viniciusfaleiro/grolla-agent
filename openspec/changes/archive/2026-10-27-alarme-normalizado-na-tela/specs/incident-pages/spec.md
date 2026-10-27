## ADDED Requirements

### Requirement: Faixa do alarme no war room
O war room de um incidente aberto que já teve alerta SHALL mostrar, no topo, uma faixa com a situação do alarme:
- **normalizado**: nenhum alerta do incidente está disparando. A faixa diz "Alertas normalizados às HH:MM", com o horário da última normalização e há quanto tempo foi, e traz a ação em destaque do estado: "Assumir como IC" (detectado ou informado), "Marcar mitigado" (em tratamento) ou "Encerrar" (mitigado, abrindo o pedido de motivo);
- **voltou a disparar**: um alerta disparou depois de uma normalização. A faixa diz "Voltou a disparar às HH:MM", com o horário do alerta, sem botão.

Enquanto o alarme dispara e nunca normalizou, SHALL NOT haver faixa. Incidente encerrado ou descartado, ou que nunca teve alerta, SHALL NOT ter faixa. A faixa só sugere: o estado do incidente SHALL NOT mudar sem uma pessoa.

A faixa SHALL se atualizar sozinha no mesmo ciclo da timeline. Quando ela muda com a página aberta, a página SHALL mostrar um aviso na tela uma vez e piscar o título da aba ("✅ Normalizado · <título>" ou "⚠️ Voltou a disparar · <título>") até a pessoa voltar para a aba. Ao abrir a página, SHALL NOT haver aviso, só a faixa.

#### Scenario: Alarme normaliza com a página aberta
- **WHEN** o último alerta disparando de um incidente em tratamento resolve às 15:03 e o war room está aberto
- **THEN** em poucos segundos aparece a faixa "Alertas normalizados às 15:03" com "Marcar mitigado", o aviso na tela e o título da aba piscando

#### Scenario: Mitigado e normalizado
- **WHEN** alguém abre um incidente mitigado cujos alertas pararam
- **THEN** a faixa "Alertas normalizados" traz "Encerrar", e não há aviso nem título piscando

#### Scenario: Alarme volta
- **WHEN** um alerta do incidente dispara de novo às 15:10, depois de normalizado
- **THEN** a faixa passa a "Voltou a disparar às 15:10", com o aviso na tela

#### Scenario: Incidente encerrado
- **WHEN** alguém abre um incidente resolvido
- **THEN** não há faixa do alarme

## MODIFIED Requirements

### Requirement: Lista de incidentes
O `grolla-api` SHALL servir `GET /incidents`, uma página HTML com os incidentes do mais recente para o mais antigo, mostrando título, severidade, estado, serviços e horário de detecção, cada um com link para a página dele.

Um incidente aberto que teve alerta e não tem nenhum disparando SHALL mostrar o selo "alarme normalizado".

#### Scenario: Dois incidentes
- **WHEN** existem dois incidentes
- **THEN** a página lista os dois, o mais recente primeiro

#### Scenario: Selo de alarme normalizado
- **WHEN** um incidente em tratamento teve o último alerta resolvido e outro incidente ainda dispara
- **THEN** só o primeiro mostra "alarme normalizado"

### Requirement: Página do incidente é o war room
A página do incidente SHALL mostrar um cabeçalho com estado, severidade, duração, IC e TTA, TTM e TTR. Abaixo do cabeçalho, SHALL mostrar as ações permitidas no estado atual, o chat com o agente, a análise, a validação, os alertas e a timeline com autor.

O war room SHALL pedir um nome na primeira visita e guardá-lo num cookie, que identifica o autor das ações e mensagens.

O chat e a timeline SHALL se atualizar sozinhos a cada poucos segundos, sem recarregar a página. Quando chega um evento novo, a tabela de alertas e o bloco de estado do cabeçalho (estado, etapas, IC, duração, TTA, TTM e TTR) SHALL se atualizar junto, sem apagar o que a pessoa está digitando nos formulários.

#### Scenario: Primeira visita
- **WHEN** alguém abre o war room sem ter informado o nome
- **THEN** a página pede o nome antes de permitir ações e mensagens

#### Scenario: Resposta do agente aparece sozinha
- **WHEN** o agente responde uma pergunta enquanto a página está aberta
- **THEN** a resposta aparece no chat em poucos segundos, sem recarregar a página

#### Scenario: Alerta resolvido aparece sozinho
- **WHEN** um alerta do incidente resolve enquanto a página está aberta
- **THEN** em poucos segundos a tabela de alertas mostra o alerta como resolvido, sem recarregar a página
