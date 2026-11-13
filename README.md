# Avaliação de Ataques de Injeção de Prompt em Agentes OpenClaw

Trabalho de Conclusão de Curso II em Ciência da Computação, Escola Politécnica, PUCRS, 2026.
Autor: Bernardo Luiz Padilha Zamin. Orientador: Prof. Lucas Silveira Kupssinskü.

Este repositório tem o código do experimento e as tabelas com os resultados do artigo.

## Questão de pesquisa

Qual a taxa de sucesso de ataques via injeção de prompt em agentes LLM configurados no
OpenClaw em cenários controlados?

O foco é a injeção indireta: o texto do atacante chega dentro de um dado que o agente lê
com uma ferramenta, durante uma tarefa legítima do usuário.

## Experimento

- **Agente:** OpenClaw 2026.9.1, executado sem interface (`openclaw agent exec`). As
  ferramentas nativas ficaram desligadas, e todas as chamadas de ferramenta foram
  aprovadas automaticamente.
- **Benchmark:** AgentDojo 0.1.35 (suítes v1.2.1), que fornece as tarefas, os ambientes
  simulados, as injeções e os avaliadores.
- **Ligação:** um servidor MCP (`harness/mcp_agentdojo.py`) entrega ao OpenClaw as
  ferramentas de cada tarefa.
- **Modelos:** Qwen3-32B, DeepSeek-V4-Flash e gpt-oss-120b, pela DeepInfra, e
  Claude-Sonnet-5, pela API da Anthropic. Nos modelos da DeepInfra, um proxy
  (`harness/openclaw_shim.py`) converte as chamadas de ferramenta escritas como texto
  para o formato que o OpenClaw espera.
- **Tamanho:** 33 pares de tarefa e injeção, cada um com os 6 ataques de injeção do
  AgentDojo, dão 198 episódios por repetição. Foram 10 repetições por modelo, num total
  de 7.920 episódios.
- **Isolamento:** tudo roda num container Docker sem privilégios e com o sistema de
  arquivos só de leitura. No host, `docker/nftables_allowlist.sh` só deixa sair tráfego
  para as APIs dos modelos.

Métricas:

- **ASR (taxa de sucesso do ataque):** fração de episódios em que o ataque venceu,
  segundo o avaliador do AgentDojo.
- **Utilidade sob ataque:** fração de episódios em que a tarefa do usuário foi concluída
  com a injeção presente.

Os valores são média ± desvio padrão amostral entre as 10 repetições. Como todas as
repetições usam as mesmas tarefas, o ± mede a variação entre execuções, e não o erro
amostral sobre as tarefas.

Um episódio só é descartado quando falha por motivo do instrumento depois de 3
tentativas. Isso aconteceu em 35 episódios, todos do gpt-oss-120b, em que o provedor
perdeu a chamada de ferramenta, e outros 127 episódios desse modelo só terminaram em uma
nova tentativa.

## Resultados

| Modelo | ASR (%) | Faixa nas repetições (%) | Utilidade sob ataque (%) | Injeção chegou ao agente (%) |
|---|---|---|---|---|
| Qwen3-32B | 19,9 ± 1,9 | 16,2 a 21,7 | 70,0 ± 2,1 | 91,4 |
| DeepSeek-V4-Flash | 10,8 ± 1,8 | 8,1 a 13,6 | 90,6 ± 1,6 | 99,5 |
| gpt-oss-120b | 7,3 ± 1,7 | 5,2 a 10,8 | 63,5 ± 1,6 | 95,5 |
| Claude-Sonnet-5 | 2,6 ± 1,3 | 1,0 a 5,1 | 79,3 ± 1,1 | 100,0 |

Dois casos mudam o que conta como vitória do atacante:

- No AgentDojo 0.1.35, o avaliador das duas injeções da suíte `banking` tem um erro de
  precedência de operadores. Qualquer transferência ao IBAN do atacante conta como
  vitória, com ou sem o dado pedido, e transferências agendadas não são verificadas. Os
  valores acima são os do avaliador, sem correção.
- A `travel/injection_task_6` é julgada pelo texto da resposta, e um aviso ao usuário que
  cite o hotel também conta como vitória.

As tabelas completas estão em [`results/tables.md`](results/tables.md): ASR e utilidade
por suíte e por ataque, ASR por critério de impacto, a auditoria do avaliador da
`banking` e as contagens citadas no artigo.

## Como reproduzir

Só é preciso ter o Docker. Todos os comandos rodam dentro do container.

```bash
cp .env.example .env
make build
make test       # testes de unidade, sem rede e sem custo
```

Para refazer as tabelas a partir dos episódios gravados, sem custo, baixe
`episodios-openclaw.tar.gz` na página de Releases deste repositório e rode:

```bash
tar xzf episodios-openclaw.tar.gz -C results/
make verify     # confere se os 7.920 episódios estão completos e sem duplicatas
make tables     # gera results/tables.md, results/tables.csv e results/episodes.csv
```

Para executar o experimento de novo (tem custo e precisa das chaves de API no `.env`):

```bash
make campaign   # 10 repetições dos 4 modelos, em segundo plano
make progress   # episódios feitos e gasto até agora
```

O experimento custou cerca de US$ 9,50 na DeepInfra e US$ 25,50 na Anthropic. Os tetos de
gasto do `.env.example` são menores que isso, de propósito. Episódios já gravados são
pulados.

## Estrutura

```
harness/   ambiente do AgentDojo, servidor MCP, execução do OpenClaw, proxy,
           campanha, controle de gasto e análise
scripts/   execução (run_openclaw, run_chunk, campaign.sh), tabelas (make_tables),
           conferência (verify_results) e progresso (show_progress)
tests/     testes de unidade
docker/    Dockerfile, Compose e filtro de saída de rede
results/   tabelas e CSV gerados (ver results/README.md)
```

O código foi limpo e traduzido para o inglês depois do experimento, sem mudar nenhum
resultado. Cada episódio grava o commit do repositório de desenvolvimento que o produziu.

## Limitações

- O experimento usa um recorte de 33 dos 949 pares de tarefa e injeção, formado pelas
  primeiras tarefas e injeções da lista de cada suíte, sem sorteio. Por isso, os
  resultados não valem para o benchmark inteiro.
- O ± mede a variação entre execuções do mesmo experimento e não permite afirmar
  significância estatística.
- O estudo mede o sistema inteiro, modelo e OpenClaw, sem separar o efeito de cada um.
- Não houve execução sem injeção, e as ferramentas nativas do OpenClaw ficaram desligadas.
- Os critérios de impacto foram definidos depois de executar o experimento, a partir do
  que cada injeção pede.

## Referências

- E. Debenedetti, J. Zhang, M. Balunović, L. Beurer-Kellner, M. Fischer e F. Tramèr.
  AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for
  LLM Agents. NeurIPS 2024, Datasets and Benchmarks Track.
- K. Greshake, S. Abdelnabi, S. Mishra, C. Endres, T. Holz e M. Fritz. Not What You've
  Signed Up For: Compromising Real-World LLM-Integrated Applications with Indirect Prompt
  Injection. AISec 2023.
- Q. Zhan, Z. Liang, Z. Ying e D. Kang. InjecAgent: Benchmarking Indirect Prompt
  Injections in Tool-Integrated Large Language Model Agents. Findings of ACL 2024.
