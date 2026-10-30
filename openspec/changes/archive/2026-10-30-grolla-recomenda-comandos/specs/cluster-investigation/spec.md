## MODIFIED Requirements

### Requirement: Vários clusters
As ferramentas do cluster SHALL consultar todos os clusters com `enabled: true`, cada um com o acesso e os namespaces dele. Cada item da resposta SHALL dizer de que cluster veio, mesmo com um cluster só, para o plano dizer onde rodar um comando. A falha de um cluster SHALL virar a linha "cluster <nome>: <erro>" sem esconder os outros; só quando todos falham a ferramenta SHALL falhar. Nos clusters com `show_values: false`, o adapter SHALL NOT devolver valores de ConfigMap nem de variáveis de ambiente, só os nomes.

#### Scenario: PCI e não PCI
- **WHEN** o `pedidos-api` roda nos clusters `nao-pci` e `pci` e o LLM chama `cluster_state`
- **THEN** a resposta traz os pods "no cluster nao-pci" e "no cluster pci"

#### Scenario: Um cluster fora do ar
- **WHEN** o cluster `pci` recusa a credencial
- **THEN** a resposta traz o `nao-pci` e termina com "cluster pci: não foi possível consultar"

#### Scenario: Cluster desligado
- **WHEN** o cluster `pci` está com `enabled: false`
- **THEN** nenhuma consulta vai ao `pci`

#### Scenario: Um cluster só
- **WHEN** só o cluster `loja-prod` está ligado e o LLM chama `cluster_state`
- **THEN** os pods vêm "no cluster loja-prod"

## ADDED Requirements

### Requirement: Réplicas do último apply
Quando um Deployment ou StatefulSet tem a anotação do último `apply` e as réplicas dela diferem das réplicas atuais, `cluster_state` SHALL dizer as réplicas do último apply ao lado das atuais. Um `scale` manual não deixa no cluster o valor anterior, e esse é o dado que o plano precisa para sugerir o comando que volta ao normal.

#### Scenario: Escalado a zero à mão
- **WHEN** o Deployment `antifraude-api` foi aplicado com 2 réplicas e alguém o escalou para 0
- **THEN** `cluster_state` traz "0 de 0 réplicas prontas (2 no último apply)"
