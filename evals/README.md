<div align="center">

<img src="../docs/assets/grolla.svg" alt="Grolla" width="96">

# evals

**Casos roteirizados que medem se a investigação do agent-grolla chega à hipótese certa com o modelo de linguagem real — e o que a memória viva acrescenta.**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Kind](https://img.shields.io/badge/tipo-eval-lightgrey)](#-o-que-é)
[![Custo](https://img.shields.io/badge/custo-chave%20real-red)](#-como-rodar)

</div>

---

## ✨ O que é

Cada caso é um incidente com telemetria e histórico de mudanças roteirizados. O eval liga o mesmo
`Toolbox` da produção a esses fakes, roda `ClaudeLanguageModel.investigate` e confere a hipótese
de maior confiança. Usa o prompt, as ferramentas e o schema de verdade. Só o mundo lá fora é
fake.

Fica **fora do `make check`**: chama a API real, custa dinheiro e não é determinístico. Os testes
em `tests/` cobrem a correção e os casos com o modelo roteirizado, sem rede, e esses rodam no
`make check`.

## 🚀 Casos

| Caso | O roteiro mostra | Passa quando a hipótese principal |
|---|---|---|
| **deploy ruim** | erro sobe de 0,2% para 8% dois minutos depois do deploy 2.4.1, com exceção nova nos logs | cita o deploy ou a versão 2.4.1 |
| **dependência lenta** | p99 de 4,2 s, timeouts e traces gastando o tempo em `antifraude-api`, sem deploy | cita a `antifraude` |
| **sem causa clara** | vazão em queda gradual, sem deploy, sem erro nem trace lento | não passa de 50% de confiança |
| **repetido, sem memória** | erro de 0,3% para 6% às 03:02, logs de pool de conexões esgotado, sem deploy; nada diz quem esgota o pool | cita o pool de conexões e **não** inventa "já vi isso" |
| **repetido, com memória** | o mesmo roteiro, mais o episódio de um incidente passado: o job de conciliação esgotou o pool às 03:00 | aponta "já vi isso" para aquele incidente e cita a conciliação |

## 🔎 Recuperação (memória com RAG)

`evals/retrieval.py` mede a **busca**, não o modelo: sobe um Postgres com pgvector
(testcontainers), grava 15 episódios do `pedidos-api` (5 casos certos e 10 distratores), indexa
com o embedder local e compara, para cada consulta, a posição do episódio certo com a busca
textual sozinha e com a híbrida. Sem LLM e sem custo.

| Consulta | Tipo | Aceite |
|---|---|---|
| pool, disco, tls | paráfrase: o incidente novo descreve o problema com outras palavras | híbrida no top 5 |
| desconto, rds | termo exato (`valor_desconto`, `grolla-lab-db`) | a híbrida não perde o que a textual acha |

```bash
GROLLA_RAG_CACHE=~/.cache/grolla-models make evals-rag          # modelo small
GROLLA_RAG_CACHE=~/.cache/grolla-models uv run python evals/retrieval.py --size large
```

## ⚡ Como rodar

```bash
export GROLLA_ANTHROPIC_API_KEY=...
uv run python evals/run.py                       # modelo de llm.stages.investigation do e2e/config.yaml
uv run python evals/run.py --config deploy/credenciadora.yaml
```

Esse comando é o alvo `make evals`. Ele imprime `ok` ou `FALHOU` por caso, com o motivo, e sai
com código 1 se algum caso falhar.

## 🧪 Testes

```bash
uv run pytest evals/tests     # correção e casos com o ScriptedLanguageModel, sem rede
```

## 🗂 Layout

```
run.py    casos, toolbox roteirizado, correção e o entrypoint
tests/    a correção e os casos sem rede
```

## 🤝 Como contribuir

Caso novo entra em `CASES`, com o roteiro de cada ferramenta e os termos esperados. Antes de
mudar prompt ou ferramenta, rode `make evals` e compare com o resultado anterior.
