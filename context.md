# Contexto do projeto — Dash Corinthians

## O que é

Dashboard web da campanha do Corinthians. Ele reúne partidas de 2024, 2025 e 2026, calcula indicadores esportivos, compara temporadas e competições e projeta o Brasileirão com simulação Monte Carlo. A página também permite disparar uma atualização de dados, limitada a uma execução bem-sucedida por dia no horário de São Paulo.

O projeto é uma aplicação pequena e monolítica, sem framework web ou frontend compilado:

1. Fontes externas fornecem jogos, tabelas, detalhes e elenco.
2. O pacote `etl/` normaliza e agrega os dados com pandas/numpy.
3. `etl/build.py` grava um único artefato, `web/data/dashboard.json`.
4. `server.py` serve `web/` e oferece endpoints de status/atualização.
5. `web/app.js` lê o JSON e renderiza a interface e os gráficos ECharts.

## Estrutura relevante

| Caminho | Responsabilidade |
| --- | --- |
| `server.py` | Servidor estático, health check, status e execução assíncrona do ETL |
| `etl/config.py` | IDs, competições, temporadas, timezone, TTLs e parâmetros do modelo |
| `etl/build.py` | Orquestra coleta, normalização, métricas, simulação, elenco, análises e saída JSON |
| `etl/sources/` | Adaptadores ESPN, API-Football, football-data.org e TheSportsDB |
| `etl/http_cache.py` | Cliente HTTP com cache em disco, retry, TTL, throttle e orçamento |
| `etl/normalize.py` | Padronização das partidas na perspectiva do Corinthians |
| `etl/metrics.py` | KPIs e agregações consumidas pelo frontend |
| `etl/simulate.py` | Simulação do Brasileirão por Poisson, 10.000 execuções e seed fixa 42 |
| `etl/coaches.py` | Compara os cinco últimos trabalhos por período, competição e mando |
| `etl/squad_analysis.py` | Diagnóstico heurístico e explicável das carências do elenco |
| `etl/scouting.py` | Radar internacional por produção, força da liga e custo-benefício documentado |
| `etl/finance.py` | Receitas oficiais/parciais estimadas e indicador de risco de transfer ban |
| `data/manual/` | Técnicos, balanços, obrigações e valores de mercado com fontes editoriais |
| `etl/storage.py` | Persistência local e espelhamento opcional no Upstash |
| `etl/refresh_state.py` | Regra de uma atualização por dia em `America/Sao_Paulo` |
| `web/index.html` | Estrutura semântica do dashboard |
| `web/styles.css` | Layout responsivo, temas claro/escuro e componentes |
| `web/app.js` | Estado, filtros, renderização ECharts, atualização e tratamento de erro |
| `web/data/dashboard.json` | Artefato gerado e contrato efetivamente consumido pelo navegador |
| `data/raw/` | Cache HTTP por fonte; não versionado segundo `.gitignore` |
| `data/match_details.json` | Cache consolidado de resumos de partidas encerradas |
| `vercel.json` | Deploy estático de `web/`, sem instalar dependências nem executar o ETL |
| `scripts/update_dashboard.ps1` | Atualização local seguida da validação offline |

## Fontes e precedência

- ESPN é a fonte principal e funciona sem chave. Fornece calendário/resultados, tabela, detalhes das partidas, escalações/estatísticas e elenco.
- API-Football é opcional. Complementa partidas ausentes e pode fornecer elenco quando não há dados derivados da ESPN. Usa limite interno de 95 chamadas/dia.
- football-data.org é opcional e funciona como fallback para tabela e calendário do Brasileirão.
- TheSportsDB é consultado para fotos de jogadores quando necessário.
- Em falha de rede, `CachedClient` usa cache vencido se ele existir. Temporadas passadas e partidas encerradas podem ficar em cache sem expiração.

Variáveis em `.env.example`: `API_FOOTBALL_KEY`, `FOOTBALL_DATA_KEY`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`, `SEASONS` e `TARGET_RATE`. O `.env` é carregado por um leitor mínimo próprio em `etl/config.py`.

## Fluxo do ETL atual

`etl.build.main()` executa, nesta ordem:

1. Busca partidas do Corinthians na ESPN para cada temporada/competição e adiciona lacunas encontradas pela API-Football.
2. Normaliza mando, adversário, placar, resultado e competição; calcula descanso.
3. Busca ou restaura detalhes de jogos concluídos para gols, estatísticas e atletas.
4. Obtém tabelas por temporada e o calendário completo do Brasileirão atual.
5. Simula o restante do Brasileirão quando tabela, identificação do time e calendário estão disponíveis.
6. Monta visões por `temporada|competição|mando`, comparativos, clássicos, próximos jogos e faixas de adversários.
7. Agrega o elenco a partir das aparições/detalhes e tenta anexar fotos; usa API-Football como fallback de elenco.
8. Calcula comparativo de técnicos, diagnóstico do elenco, radar de mercado, receitas e risco de transfer ban.
9. Grava o dashboard e então marca a atualização diária como concluída.

Se nenhuma fonte fornecer partidas, o ETL encerra sem produzir um dashboard novo. Avisos de fontes indisponíveis entram em `meta.notes` (máximo de 30 no JSON).

## Contrato do dashboard

As chaves de topo atuais são:

- `meta`: geração, equipe, temporadas, competições, fontes, avisos e contagem de gols detalhados.
- `views`: objetos indexados por `season|comp_key|venue`; cada visão contém `kpis`, `series`, `distribution`, `last5`, `games`, `streaks`, `minutes`, `scorers`, `first_goal`, `rest` e `calendar`.
- `home_away`: comparativos por `season|comp_key`.
- `competitions`: cartões de campanha por temporada.
- `seasons_compare`: resumo e séries comparativas anuais.
- `classicos`: retrospecto contra Palmeiras, São Paulo e Santos.
- `upcoming`: próximos jogos.
- `standings`: tabela do Brasileirão da temporada atual.
- `opponent_tiers`: desempenho por faixa da tabela.
- `simulation`: probabilidades, distribuição de posição, cortes e tabela projetada.
- `squads`: listas indexadas por `season|comp_key`.
- `analysis`: blocos `coaches`, `squad`, `scouting`, `finance` e `quality` da Central de análises.

Cada jogo publicado em `views.*.games` inclui `event_id` e `source`, permitindo auditoria e ligação com o cache de origem.

O frontend mantém os filtros `season`, `competition` e `venue` em memória. O tema é persistido em `localStorage`. O ECharts e as fontes Inter/Oswald dependem de CDNs; a ausência do ECharts gera uma tela de erro explícita.

## Servidor e atualização

Rotas especiais:

- `GET /healthz`: retorna `{"ok": true}` sem tocar no Upstash.
- `GET /api/status`: informa execução, última atualização e próxima janela.
- `POST /api/refresh`: requer o cabeçalho `X-Dash-Refresh: 1`; retorna 202 ao iniciar, 409 se já estiver rodando e 429 se já houve atualização no dia.

Quando iniciado pelo botão local ou por `--auto`, o ETL roda em subprocesso, com timeout de 30 minutos e exclusão mútua em memória. Por padrão, `python server.py` apenas serve o dashboard e não atualiza dados sozinho. O bloqueio diário só é marcado no fim de um build bem-sucedido.

Localmente, os artefatos são gravados em disco. O fluxo recomendado executa o ETL no PC, valida e versiona `web/data/dashboard.json`; a Vercel serve apenas a pasta `web/`. Ela não executa Python, não recebe chaves e não oferece `/api/status` ou `/api/refresh`, fazendo o frontend ocultar o botão de atualização. O suporte a Upstash permanece apenas como persistência legada/opcional do servidor Python.

## Interface entregue

A página contém campanha e KPIs, cenários simulados, evolução, distribuição de resultados, casa/fora, cenário de chegada, jogos, competições, comparação de temporadas, clássicos, gols por minuto, artilheiros/assistências, calendário, descanso, faixas de adversários e sequências. A Central de análises acrescenta abas de técnicos, elenco, mercado, finanças/transfer bans e qualidade dos dados. Há tema claro/escuro, filtros e layout responsivo.

## Dados editoriais e limites das novas análises

`etl/manual.py`, `etl/finance.py`, `etl/coaches.py`, `etl/scouting.py` e `etl/squad_analysis.py` estão integrados ao pipeline. Os arquivos em `data/manual/` são versionáveis e precisam manter URL de origem e data/validade implícita ou explícita.

- O diagnóstico do elenco usa regras editáveis e mostra o número que disparou cada alerta; não equivale a avaliação definitiva.
- O scouting consulta Brasileirão, Argentina, Colômbia, Uruguai, Equador e Portugal. O índice compara jogadores da mesma posição e pondera a liga; defensores têm leitura limitada pela ausência de desarmes/interceptações/minutos na fonte.
- Custo-benefício só é calculado para atletas com valor cadastrado e fonte em `market_values.json`; valor de mercado não é preço de transferência nem salário.
- A estimativa financeira do ano corrente é parcial: soma apenas componentes configurados (bilheteria aproximada e premiações identificáveis). Receitas oficiais históricas prevalecem na apresentação.
- O indicador de transfer ban é editorial e explicável, não parecer jurídico. Um ban ativo força o nível máximo; obrigações e status exigem manutenção manual.
- A aba de qualidade mede presença de público, estádio, súmulas, estatísticas e fotos, além de duplicidades por `event_id`. Cobertura não certifica a precisão da fonte.
- O scouting remove duplicidades de atletas transferidos na mesma temporada e avalia custo-benefício dentro dos 30 melhores tecnicamente por setor antes de publicar até 10 nomes.

## Estado observado em 27/09/2026

- `web/data/dashboard.json` existe, tem aproximadamente 1,15 MB e declara geração em 27/09/2026.
- O artefato contém temporadas 2024–2026, 198 jogos concluídos (72 + 74 + 52) e 10 agendados.
- `data/last_refresh.json` registra conclusão em 27/09/2026 às 12:18:04 -03:00, duração de 3,1 s.
- A checagem `python -m compileall -q server.py etl` passou.
- Há um validador offline (`python -m etl.validate`) e CI no GitHub para sintaxe e contrato do snapshot; ainda não existe suíte de testes unitários, linter ou formatador.
- O projeto possui `README.md`, contribuição, licença MIT e configuração estática da Vercel.
- Este diretório não contém `.git`; comandos Git não funcionam aqui.
- Em 27/09/2026, o dashboard foi regenerado com 208 partidas totais, 448 eventos de gol e pool de scouting de 3.776 atletas (801 elegíveis após filtros).
- Cobertura observada: público 174/198, estádio 198/198, detalhes/estatísticas 198/198 e fotos do elenco atual 19/30.

## Limitações e riscos conhecidos

- Há caracteres Unicode já corrompidos como `�` em strings dos arquivos Python/JavaScript e no JSON gerado. Isso é conteúdo real dos arquivos, não apenas exibição do terminal.
- O endpoint de refresh não tem autenticação de usuário. O cabeçalho customizado dificulta POST cross-site simples, mas não é controle de acesso.
- O lock contra execuções simultâneas e o último erro vivem apenas na memória do processo; múltiplas instâncias não compartilham esse estado.
- O cache bruto e os artefatos gerados podem ser grandes e conter dados obsoletos. Datas e resultados nunca devem ser inferidos apenas pelo nome de arquivo.
- As APIs públicas e seus formatos podem mudar. Parsers devem falhar com aviso e preservar fallback/cache quando possível.

## Comandos úteis

```powershell
# instalar dependências
python -m pip install -r requirements.txt

# servir localmente sem disparar ETL automático
python server.py

# opcional: ativar tentativa automática diária
python server.py --auto

# executar o pipeline completo (pode chamar APIs e alterar artefatos)
python -m etl.build

# checagem de sintaxe offline
python -m compileall -q server.py etl

# validar o dashboard gerado sem chamar APIs
python -m etl.validate
```
