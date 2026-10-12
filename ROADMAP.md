# CineRank & CineSwipe — Roteiro de Continuidade para Agentes

Este documento é o **manual de transferência de bastão** para qualquer agente de IA ou desenvolvedor que for dar continuidade a este projeto.

---

## 1. Status Atual do Repositório (v2.2.0)

| Módulo | Status | Descrição |
|---|---|---|
| **Catálogo & Ranking** | Concluído | Filmes em streaming com notas TMDB, RT, IMDb, filtros de plataforma, período e busca. |
| **Grafo de Conexões** | Concluído | Canvas 2D interativo com física de molas calculando afinidade de roteiro e equipe. |
| **CineSwipe (Tinder 4 eixos)** | Concluído | Descoberta por gestos (Esquerda, Direita, Cima, Baixo), selos dinâmicos, teclado e Watchlist. |
| **Deploy Serverless (Vercel)** | Ativo em Produção | [https://projetofilmesranking.vercel.app](https://projetofilmesranking.vercel.app) |
| **Repositório GitHub** | Conectado & CI/CD | [https://github.com/jusolu/cinerank-streaming](https://github.com/jusolu/cinerank-streaming) |
| **Estratégias de RL & Catálogo** | Especificadas | Modeladas em [ARCHITECTURE.md](ARCHITECTURE.md) (LinUCB, Taste Drift EMA, Two-Tower, Expansão Multidimensional). |

---

## 2. Comandos Imediatos para Rodar Localmente

```bash
# 1. Ativar ou instalar dependências
pip install -r requirements.txt

# 2. Iniciar o servidor Flask local
python app.py

# 3. Acessar no navegador
http://127.0.0.1:5000/
```

---

## 3. Próximos Passos de Desenvolvimento

### Fase 1: Expansão Massiva de Catálogo (500 a 2.000+ Filmes)
- **Onde atuar:** `movie_api.py`.
- **Ações:**
  1. Remover o teto de `items_to_enrich[:120]`.
  2. Adicionar novas queries no TMDB Discover:
     - `sort_by=popularity.desc` (os mais assistidos no streaming no Brasil).
     - `sort_by=vote_average.desc&vote_count.gte=500` (clássicos modernos aclamados).
     - Segmentar por gênero (Ficção, Suspense, Terror, Animação, Drama, Comédia).
  3. Salvar incrementalmente no `data/movies.json` para acumular catálogo sem sobrecarregar a API.

### Fase 2: Implementar Motor de Recomendação por Reforço (`recommender.py`)
- **Algoritmo Escolhido:** **LinUCB (Contextual Bandit) + Taste Drift (EMA)**.
- **Estrutura proposta:**
  - Criar `recommender.py` com classe `CineSwipeBandit`:
    - Métodos: `update(user_id, movie_features, reward)` e `recommend(user_id, candidate_pool, top_k=15)`.
    - Vetor de características de cada filme: `[genres_onehot, director_id, cinescore_norm, year_norm]`.
    - Recompensas por gesto:
      - Direita (Gostei): $+1.0$
      - Esquerda (Não Gostei): $-1.0$
      - Cima (Quero Ver): $+0.8$
      - Baixo (Não Assistiria): $-0.5$
  - Conectar ao endpoint `/api/cineswipe/next-deck` no Flask.

### Fase 3: Configurar Banco de Dados Supabase (Free Tier com pgvector)
1. Crie um projeto gratuito no [Supabase](https://supabase.com).
2. Execute o script SQL no editor do Supabase:
```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE movies (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    original_title TEXT,
    year INT,
    release_date DATE,
    streaming_service TEXT,
    streaming_name TEXT,
    director TEXT,
    screenwriters TEXT[],
    cast_members TEXT[],
    genres TEXT[],
    synopsis TEXT,
    poster_url TEXT,
    trailer_url TEXT,
    scores JSONB,
    embedding vector(768), -- Embeddings via Gemini 1.5 Flash
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE user_swipes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    movie_id TEXT REFERENCES movies(id),
    action TEXT NOT NULL, -- 'like', 'dislike', 'watchlist', 'skip'
    reward FLOAT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Fase 4: Pipeline de Ingestão Diária com GitHub Actions
Crie `.github/workflows/daily_sync.yml`:
```yaml
name: Daily Movie Sync (Zero Cost)
on:
  schedule:
    - cron: '0 4 * * *' # Todo dia às 04:00 UTC
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python movie_api.py
        env:
          TMDB_API_KEY: ${{ secrets.TMDB_API_KEY }}
          OMDB_API_KEY: ${{ secrets.OMDB_API_KEY }}
```

---

## 4. Arquitetura de Pastas

```
c:\projeto_filmes_ranking\
├── api/
│   └── index.py            # Entrypoint serverless WSGI da Vercel
├── data/
│   ├── movies.json         # Base consolidada de filmes e notas de streaming
│   └── config.json         # Chaves e parâmetros de sincronização
├── static/
│   ├── css/style.css       # Estilos complementares
│   └── js/app.js           # Engine do Catálogo, Grafo Canvas e CineSwipe
├── templates/
│   └── index.html          # Interface única (Catálogo, Grafo e CineSwipe)
├── .gitignore              # Ignora ambientes virtuais, caches e segredos
├── app.py                  # Servidor Flask e rotas REST da API
├── movie_api.py            # Coletor e consolidador de notas e similaridades
├── scraper.py              # Scraper auxiliar Rotten Tomatoes e IMDb
├── requirements.txt        # Dependências Python mínimas
├── vercel.json             # Regras de roteamento e build da Vercel
├── ARCHITECTURE.md         # Documento mestre de arquitetura, RL e estratégia $0
├── CHANGELOG.md            # Registro detalhado de versões
├── ROADMAP.md              # Este manual de continuidade
└── README.md               # Apresentação do projeto e quickstart
```
