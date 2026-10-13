# CineRank

Ranking dos filmes lançados desde 1990, com notas do IMDb ajustadas pelo volume de votos, metodologia pública e recomendação personalizada no navegador.

Produção: <https://projetofilmesranking.vercel.app>

## O que faz

- **Ranking.** ~53 mil filmes com 500+ votos no IMDb, ordenados pelo **CineScore**. Filtros por década, ano e gênero, busca por título, diretor ou ator, paginação.
- **Ficha do filme.** Nota, votos, posição no ranking, direção, roteiro, elenco e links para o IMDb e para a busca do trailer.
- **Conexões.** Grafo interativo que liga filmes por gênero, direção, roteiro e elenco em comum, com o motivo de cada ligação.
- **Descobrir.** Triagem de um filme por vez (vi e gostei, vi e não gostei, quero ver, sem interesse) com recomendação aprendida no navegador: **LinUCB** (bandit contextual), **EMA** (gosto recente) ou sem personalização. Cada cartão explica por que apareceu. Implementação em [static/js/reco.js](static/js/reco.js).
- **Cores.** Fundo branco, texto preto e uma cor primária escolhida pelo usuário (com aviso de contraste).

## CineScore

`CineScore = (v × nota + m × C) ÷ (v + m)`, com a nota do IMDb em 0–100, `v` = votos do filme, `m` = 10.000 e `C` = nota média do catálogo (recalculada a cada carga).

O ajuste forte é necessário em escala: sem ele, títulos com poucos milhares de votos e nota ~9,9 dominam o topo. Filmes com menos de 1.000 votos são sinalizados. Implementação em [scoring.py](scoring.py).

## Dados

O catálogo vem dos [datasets públicos do IMDb](https://developer.imdb.com/non-commercial-datasets/), atualizados diariamente e baixados em bulk, sem chave de API.

```bash
pip install -r requirements.txt
python -m ingest.imdb --since 1990        # ~5 min: baixa ~1,9 GB e carrega 431 mil filmes em data/cinerank.db
python -m ingest.export --min-votes 500   # ~10 s: gera data/catalog.db (≈53 mil filmes, 46 MB), o banco publicado
```

| Arquivo | Conteúdo | Versionado |
|---|---|---|
| `data/raw/*.tsv.gz` | Datasets brutos do IMDb | Não |
| `data/cinerank.db` | Banco de trabalho completo (431 mil filmes) | Não |
| `data/catalog.db` | Banco somente leitura do site (500+ votos) | Sim |

Os datasets do IMDb são licenciados apenas para **uso pessoal e não comercial**. O site exibe a atribuição exigida.

**Opcional:** [ingest/tmdb.py](ingest/tmdb.py) enriquece o banco de trabalho com sinopse em português, pôster, palavras-chave e onde assistir no Brasil, via API do TMDB (chave gratuita em `TMDB_API_KEY`). Ainda não está ligado ao site.

## Executar localmente

Requer Python 3.10+.

```bash
pip install -r requirements.txt
python app.py              # http://127.0.0.1:5000
```

O site lê `data/catalog.db`, que já vem no repositório.

## API

| Rota | Descrição |
|---|---|
| `GET /api/movies` | Ranking paginado. Parâmetros: `decade` (ex.: `1990`), `year`, `genre` (chave do IMDb, ex.: `Drama`), `search`, `sort` (`cinescore`, `imdb`, `votes`, `newest`), `min_votes`, `page`, `page_size` (até 500) |
| `GET /api/movies/<imdb_id>` | Ficha com direção, roteiro e elenco |
| `GET /api/movies/<imdb_id>/graph` | Grafo centrado no filme (`min_sim`, `limit`) |
| `GET /api/graph/default` | Grafo do 1º colocado |
| `GET /api/facets` | Anos e gêneros com contagens |
| `GET /api/stats` | Totais e parâmetros da metodologia |

## Estrutura

```
api/index.py          Entrypoint WSGI da Vercel
app.py                Servidor Flask e rotas
catalog.py            Consultas ao catálogo publicado e grafo de afinidade
scoring.py            CineScore
ingest/               Ingestão em bulk (IMDb), enriquecimento opcional (TMDB) e export
data/catalog.db       Banco publicado
templates/index.html  Marcação da interface
static/css/style.css  Estilos
static/js/app.js      Ranking, ficha, grafo e Descobrir
```

## Documentação

- [ARCHITECTURE.md](ARCHITECTURE.md): pipeline de dados, CineScore, afinidade e propostas de evolução.
- [ROADMAP.md](ROADMAP.md): próximos passos.
- [CHANGELOG.md](CHANGELOG.md): histórico de versões.

## Atribuição

Information courtesy of IMDb (<https://www.imdb.com>). Used with permission.
