# Como contribuir

Obrigado por considerar uma contribuição ao Dash Corinthians.

## Princípios

- Não invente dados. Toda informação editorial deve ter fonte e data-base.
- Preserve a separação entre ETL local e dashboard estático.
- Não inclua chaves, `.env`, caches brutos ou dados privados em commits.
- Diferencie fatos, estimativas estatísticas e heurísticas editoriais.
- Mantenha o frontend acessível, responsivo e sem etapa de compilação.

Leia também [`AGENTS.md`](AGENTS.md) e [`context.md`](context.md) antes de alterar o projeto.

## Preparação

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

As chaves são opcionais. Sem elas, o ETL usa principalmente a ESPN pública e os caches locais disponíveis.

## Fluxo recomendado

1. Faça uma alteração pequena e focada.
2. Preserve o contrato entre `etl/build.py` e `web/app.js`.
3. Se necessário, regenere os dados com `python -m etl.build`.
4. Execute as validações:

```powershell
python -m compileall -q server.py etl
python -m etl.validate
node --check web/app.js
```

5. Teste localmente com `python server.py` e abra `http://127.0.0.1:8000`.

## Dados editoriais

Arquivos em `data/manual/` devem conter links diretos para as fontes. Valores de mercado são referências, não preços de transferência; o risco de transfer ban é informativo, não parecer jurídico.

## Pull requests

Descreva:

- o problema resolvido;
- o que mudou;
- como foi validado;
- eventuais limitações ou dados ausentes.

