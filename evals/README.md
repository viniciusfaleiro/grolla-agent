# evals

Avaliação da qualidade do LLM do agent-grolla. **Vazio no slice 1**; é preenchido no slice da
investigação.

A ideia: replay de incidentes de referência (alertas, respostas dos fakes de telemetria e de
mudanças) contra o ciclo de investigação, medindo se a hipótese principal bate com a causa real,
em quanto tempo e a que custo. Diferente do `make e2e`, que verifica comportamento com respostas
roteirizadas, aqui o modelo real é chamado — por isso os evals rodam sob demanda, não no
`make check`.
