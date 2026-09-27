# AGENTS.md

## Antes de alterar o projeto

- Leia `context.md`. Ele descreve a arquitetura, o fluxo de dados, os contratos principais e as limitações já confirmadas.
- Trate o código e os artefatos atuais como fonte da verdade. Quando documentação e implementação divergirem, valide no código e atualize `context.md` se a mudança for estrutural.
- Não invente dados esportivos, financeiros ou cadastrais. Se uma informação não estiver nas fontes, no cache ou em arquivos manuais, exponha a ausência de forma explícita.
- Preserve mudanças do usuário e mantenha o escopo da tarefa. Este diretório não é atualmente um repositório Git; não presuma histórico ou possibilidade de restauração via Git.

## Visão operacional

- Backend/servidor: Python 3.12+, biblioteca padrão HTTP e `requests`/`pandas`.
- Frontend: HTML, CSS e JavaScript puro; ECharts 5.5.1 é carregado por CDN.
- Entrada principal local: `python server.py`.
- ETL manual: `python -m etl.build`.
- Saída consumida pela interface: `web/data/dashboard.json`.
- Deploy: Vercel estática, conforme `vercel.json`; somente `web/` é publicado.
- O ETL é local. `web/data/dashboard.json` é gerado no PC, validado, versionado e publicado via Git.

## Regras para mudanças

- Mantenha o frontend sem etapa de build, salvo pedido explícito para mudar a arquitetura.
- Preserve o contrato entre `etl/build.py` e `web/app.js`. Se alterar a forma do JSON, atualize produtor, consumidor e `context.md` juntos.
- Centralize competições, temporadas, IDs, TTLs, meta e parâmetros de simulação em `etl/config.py`.
- Integrações externas devem continuar usando `CachedClient` em `etl/http_cache.py`, respeitando cache, TTL, retry, throttling e orçamento diário.
- Não faça chamadas reais às APIs nem regenere o dashboard apenas para validar uma alteração que possa ser testada offline. O ETL pode consumir cotas, alterar caches, `web/data/dashboard.json` e o estado diário de atualização.
- Não edite manualmente arquivos em `data/raw/`; eles são cache. Não trate `web/data/dashboard.json`, `data/match_details.json`, `data/player_photos.json` ou `data/last_refresh.json` como dados autoritativos escritos à mão.
- Segredos pertencem ao `.env`, que é ignorado. Nunca grave chaves no código, no JSON público ou na documentação.
- Preserve UTF-8. Há caracteres `�` já existentes em textos de Python/JavaScript e no JSON gerado; não propague nem faça substituições em massa sem conferir o texto correto.
- Os módulos `etl/manual.py`, `finance.py`, `coaches.py`, `scouting.py` e `squad_analysis.py` alimentam a Central de análises. Preserve suas regras explicáveis e mantenha fontes nos registros de `data/manual/`.
- Valores de mercado são referências editoriais, não preços de contratação. Não calcule custo-benefício sem `value_eur_m` e `source` documentados.
- O risco de transfer ban é um indicador informativo, não parecer jurídico; sempre exiba fatores e fontes, e atualize a data/status dos casos ao revisar esses dados.

## Validação mínima

- Para mudanças Python: `python -m compileall -q server.py etl`.
- Para mudanças no servidor: iniciar com `python server.py`, verificar `/healthz`, `/api/status` e o carregamento dos arquivos estáticos. O ETL automático é desativado por padrão.
- Para mudanças no ETL: prefira funções puras e dados locais/cacheados. Só execute `python -m etl.build` quando a tarefa justificar efeitos em dados e chamadas externas.
- Depois de mudanças no contrato ou nos dados gerados: `python -m etl.validate`.
- Para mudanças no frontend: valide no navegador os temas claro/escuro, filtros de temporada/competição/mando, responsividade, gráficos e estados vazio/erro.
- Não há suíte automatizada de testes, linter ou formatador configurado. Registre no handoff exatamente o que foi verificado.

## Critérios de conclusão

- A aplicação continua abrindo sem etapa de compilação do frontend.
- Ausências de dados degradam com aviso/estado vazio, não com valores fabricados.
- Alterações de esquema são compatíveis de ponta a ponta.
- Novas variáveis de ambiente aparecem em `.env.example`. Segredos do ETL nunca devem ser configurados na Vercel estática.
- Mudanças arquiteturais relevantes são refletidas em `context.md`.
