## ADDED Requirements

### Requirement: Comandos no console
Quando a resposta do console sugere comandos, o agente SHALL devolvê-los como passo a passo em ordem (até 5, cada um com onde rodar), pelas mesmas regras do plano da validação, e o console SHALL mostrá-los como no war room: "Onde rodar: <lugar>" e o bloco escuro com Copiar, com o selo de destrutivo quando for o caso. O console não tem "Apliquei": fora de um incidente não há timeline onde registrar. Quando faltar um dado que o agente não alcança, a resposta SHALL pedir à pessoa exatamente o que falta e onde conseguir.

#### Scenario: Como desfazer uma mudança
- **WHEN** "Vinicius" pergunta no console "como volto o limite de CPU da estoque-api?" e `cluster_changes` mostrou o limite anterior de 500m no namespace loja do cluster lab-k3s
- **THEN** a resposta traz o comando com o limite de 500m e "Onde rodar: cluster lab-k3s, namespace loja", com o botão Copiar
