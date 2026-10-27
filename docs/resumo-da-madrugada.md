# Resumo da madrugada (27/10/2026)

O que foi entregue enquanto você dormia, cada slice com o e2e verde, arquivado no openspec,
versionado e com push na `master`. As decisões com o racional estão em
[`decisoes.md`](decisoes.md) (linhas 19 a 45).

| Versão | Slice | O que ficou pronto | e2e |
|---|---|---|---|
| 0.0.11 | S5 · acompanha a crise | ciclos em P0/P1, card só quando muda, propostas de mitigar e encerrar, impacto estimado | ✅ |
| 0.0.12 | S6 · post mortem | rascunho ao resolver, seções versionadas, regenerar, plano de ação, ciclo de vida, PDF | ✅ |
| 0.0.13 | S7 · post mortem alimenta a memória | episódio confirmado, "era o mesmo problema?", avaliação do agente, `/metricas` | ✅ |
| 0.0.14 | S8 · menos ruído | triagem do incidente novo, sugestão de junção, "Juntar com…", severidade sugerida | ✅ |
| 0.0.15 | S9 · console de operação | `/console` fora de incidente, gráficos desenhados no servidor, abrir incidente pela conversa | ✅ 40/40 |
| 0.0.16 | S10 · transcrição de call | `.vtt`/`.srt`/texto no war room vira decisões, ações, horários e hipóteses na timeline e no post mortem | ✅ 41/41 |
| 0.0.17 | S11 · AWS completa | ECS/EKS, AWS Health, cotas e várias contas por role somente leitura | ✅ + laboratório |

Correção que apareceu no caminho: o LLM fake dizia "já vi isso" para episódio de outro serviço, e
um cenário do e2e ficava instável quando rodava depois do console. O fake agora exige o mesmo
serviço, como o prompt real, e a análise ignora o episódio do próprio incidente.

## O que ficou para depois e o que preciso de você

1. **S4 · on-call acionado sozinho (Spike.sh).** Não dá para testar ponta a ponta sem:
   - conta no Spike.sh e uma API key (vai em variável de ambiente, nunca no YAML);
   - uma política de escalonamento e o serviço/integração que o agente vai acionar;
   - o webhook de ack/resolve do Spike apontando para a api do Grolla (URL pública ou túnel).
2. **Várias contas AWS de verdade (S11).** O adapter foi testado no laboratório com o Health, as
   cotas, os recursos e o isolamento de falha (role inexistente → "conta outra: AccessDenied" sem
   esconder a principal). Não criei a role `grolla-somente-leitura` nem um usuário IAM: criar
   identidade e chave na conta é decisão sua, e o permissionamento bloqueou. A conta do lab é
   usada como root, e root não assume role. Para fechar o teste: crie a role com
   `deploy/iam-somente-leitura.json` (sem o statement de AssumeRole) e a confiança de
   `deploy/iam-confianca-role.json`, use um usuário ou role não-root com a política completa, e
   liste a conta em `cloud.accounts`.

## Laboratório

Desligado no fim: EC2 `i-0def690e1aea64498` parada e RDS `grolla-lab-db` parado (a AWS religa um
RDS parado sozinha depois de 7 dias). O grolla-local e o túnel continuam parados. Nada foi
apagado.
