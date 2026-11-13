# results/

Os arquivos desta pasta são gerados por `make tables`.

| Arquivo | Conteúdo |
|---|---|
| `tables.md` | todas as tabelas |
| `tables.csv` | uma linha por (modelo, suíte, ataque, repetição) |
| `episodes.csv` | uma linha por episódio válido (7.885), com o caminho do JSON |

Os 7.920 episódios brutos ficam em `episodios-openclaw.tar.gz`, na página de Releases.
Descompactados, ficam em `agentdojo-openclaw/<modelo>/rep<NN>/`.

## Colunas de `episodes.csv`

| Coluna | Significado |
|---|---|
| `attacker_won` | 1 se o `security()` do AgentDojo diz que o atacante atingiu o objetivo |
| `task_completed` | 1 se o `utility()` do AgentDojo diz que a tarefa do usuário foi concluída |
| `read_injection` | 1 se o texto injetado apareceu no retorno de alguma ferramenta |
| `repetition` | de 1 a 10 |

## Episódios brutos

Os episódios foram gravados com nomes em português e são mantidos como foram gravados.

Nome do arquivo:

```
<veredito>__openclaw__<modelo>__<suite>__<tarefa>__<injecao>__<ataque>__rep<N>.json
```

| Veredito | Significado |
|---|---|
| `VENCEU` | o atacante venceu |
| `bloqueado` | o atacante não venceu |
| `invalido` | falha do instrumento depois de 3 tentativas; fica fora das métricas |

Campos do JSON:

| Campo | Significado |
|---|---|
| `resumo` | resumo do episódio |
| `resumo.veredito` | veredito |
| `resumo.tarefa`, `injecao`, `ataque`, `repeticao` | tarefa, injeção, ataque, repetição |
| `resumo.modelo_key`, `provedor` | modelo, provedor |
| `resumo.ataque_venceu` | o atacante venceu |
| `resumo.tarefa_concluida` | a tarefa do usuário foi concluída |
| `resumo.avaliacao_valida`, `motivo_invalido`, `erro` | episódio válido, motivo da falha, erro |
| `resumo.tentativas`, `duracao_s`, `uso` | tentativas, duração, tokens e custo |
| `tarefa_do_usuario` | o pedido do usuário |
| `conteudo_injetado` | o texto injetado |
| `acoes_do_agente` | chamadas de ferramenta: `ferramenta`, `argumentos`, `retorno` |
| `resposta_ao_usuario` | a resposta final do agente |
| `openclaw_envelope` | saída bruta do OpenClaw |

## Refazer um número

ASR do Qwen3-32B em cada repetição, usando só o CSV:

```sh
python3 - <<'PY'
import csv
from collections import defaultdict
g = defaultdict(lambda: [0, 0])
for x in csv.DictReader(open("results/episodes.csv")):
    if x["model"] == "qwen3-32b":
        c = g[int(x["repetition"])]
        c[0] += 1
        c[1] += int(x["attacker_won"])
print([round(100 * b / a) for _, (a, b) in sorted(g.items())])
PY
# [19, 19, 22, 22, 16, 21, 21, 21, 18, 21]
```

A média desses valores é o ASR (19,9%), e o desvio padrão amostral deles é o ± (1,9
ponto percentual).
