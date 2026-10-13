# CineRank — Roteiro de continuidade

Manual de transferência para quem for dar continuidade ao projeto.

## 1. Status atual (v4.0.0)

| Módulo | Status | Descrição |
|---|---|---|
| **Ingestão em bulk (IMDb)** | Concluído | `python -m ingest.imdb`: 138 mil filmes desde 2020 em ~4 min, sem chave. |
| **Banco publicado** | Concluído | `python -m ingest.export`: `data/catalog.db` (≈30 mil filmes, 26 MB), somente leitura. |
| **Ranking** | Concluído | CineScore bayesiano (m = 10.000), filtros de ano e gênero, busca, paginação. |
| **Conexões** | Concluído | Grafo por gênero, direção, roteiro e elenco, calculado sob demanda em SQL + Python. |
| **Descobrir** | Concluído | Triagem em 4 direções sobre os 500 mais bem ranqueados (1.000+ votos), perfil local. |
| **Enriquecimento TMDB** | Pronto, não ligado | `ingest/tmdb.py` testado só com dados simulados; requer `TMDB_API_KEY`. |
| **Deploy (Vercel)** | Produção | <https://projetofilmesranking.vercel.app> |
| **Repositório** | GitHub | <https://github.com/jusolu/cinerank-streaming> |

## 2. Rodar localmente

```bash
pip install -r requirements.txt
python app.py                      # usa data/catalog.db já versionado
```

Para recriar o catálogo do zero:

```bash
python -m ingest.imdb && python -m ingest.export
```

## 3. Próximos passos

### Fase 1: Atualização automática do catálogo
GitHub Actions semanal: `ingest.imdb` → `ingest.export` → commit de `data/catalog.db` → deploy automático da Vercel.
Cada commit do banco adiciona ~10 MB comprimidos ao histórico do git; com frequência semanal isso é aceitável por um tempo. Se crescer, migrar o banco para fora do repositório (fase 3).

### Fase 2: Notas de crítica
Adicionar Rotten Tomatoes e Metacritic ao CineScore para os títulos mais votados:
- Wikidata (CC0, consulta em bulk via SPARQL) para os filmes com nota registrada.
- OMDb para o restante, respeitando a cota (1.000/dia grátis).
Os pesos já existem em `scoring.SOURCE_WEIGHTS`.

### Fase 3: Banco gerenciado (opcional)
Se o catálogo precisar ser atualizado sem novo deploy, mover para Postgres (Supabase free tier, 500 MB). O esquema de `ingest/db.py` evita recursos específicos do SQLite.

### Fase 4: Enriquecimento via TMDB (opcional)
Rodar `ingest/tmdb.py` traria sinopse em português, pôsteres, palavras-chave (melhorando o grafo) e **onde assistir no Brasil**. Exige chave gratuita e implica a interface voltar a exibir imagens.

### Fase 5: Recomendação
Substituir a ordenação por gêneros do Descobrir por LinUCB ou EMA de gosto (ver ARCHITECTURE.md, seção 3).

## 4. Estrutura

```
projeto_filmes_ranking/
├── api/index.py          Entrypoint WSGI da Vercel
├── app.py                Rotas Flask
├── catalog.py            Consultas ao catálogo publicado e grafo de afinidade
├── scoring.py            CineScore
├── ingest/
│   ├── db.py             Esquema do banco de trabalho
│   ├── imdb.py           Carga em bulk dos datasets do IMDb
│   ├── tmdb.py           Enriquecimento opcional via TMDB
│   └── export.py         Gera data/catalog.db
├── data/catalog.db       Banco publicado (versionado)
├── templates/index.html  Marcação
├── static/css/style.css  Estilos (tokens em :root)
├── static/js/app.js      Frontend
└── vercel.json           Roteamento da Vercel
```
