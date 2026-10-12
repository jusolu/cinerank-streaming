# CineRank & CineSwipe — Roteiro de Continuidade para Agentes

Este documento é o **manual de transferência de bastão** para qualquer agente de IA ou desenvolvedor que for dar continuidade a este projeto.

---

## 1. Status Atual do Repositório

| Módulo | Status | Descrição |
|---|---|---|
| **Catálogo & Ranking** | Concluído (v2.1) | 24 filmes recentes de 2026 com notas TMDB, RT, IMDb, filtros por streaming, período e busca. |
| **Grafo de Conexões** | Concluído (v2.1) | Canvas 2D interativo com algoritmo de força física calculando afinidade de roteiro e equipe. |
| **CineSwipe (Tinder 4 eixos)** | Concluído (v2.1) | Descoberta por gestos (Esquerda, Direita, Cima, Baixo), selos dinâmicos, teclado e Watchlist. |
| **Vercel Config** | Pronto para Deploy | `vercel.json`, `api/index.py` e `requirements.txt` configurados para deploy serverless. |
| **Contas Cloud** | Limpas ($0 custo) | Vercel (`jusolu`) com 0 projetos; GCP (`jusolu@gmail.com`) com 0 projetos ativos. |

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

## 3. Como Publicar na Vercel (1 Comando)

Como a conta já está autenticada via CLI como `jusolu`, para criar o deploy na Vercel basta rodar:

```powershell
cmd /c npx vercel --prod --yes
```

> **Nota:** Não execute scripts `.ps1` diretamente no PowerShell se a política do Windows estiver restrita; utilize sempre o prefixo `cmd /c npx ...`.

---

## 4. Próximos Passos Recomendados para o Próximo Agente

### Passo 1: Configurar Banco de Dados Supabase (Free Tier)
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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Passo 2: Criar o Workflow do GitHub Actions para Sincronização Diária
Crie o arquivo `.github/workflows/daily_sync.yml`:
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

### Passo 3: Adicionar Sistema de Autenticação Leve
- O CineSwipe atualmente persiste dados de gosto e watchlist em `localStorage` do navegador do usuário.
- Para habilitar sincronização entre dispositivos (celular e desktop), adicione o Supabase Auth (Google Login gratuito) ou login sem senha por Magic Link via e-mail.

---

## 5. Arquitetura de Pastas

```
c:\projeto_filmes_ranking\
├── api/
│   └── index.py            # Entrypoint serverless WSGI da Vercel
├── data/
│   ├── movies.json         # Base consolidada de filmes e notas de streaming
│   └── config.json         # Chaves e parâmetros de sincronização
├── static/
│   ├── css/                # Folhas de estilo (opcional modularização)
│   └── js/
│       └── app.js          # Engine do Catálogo, Grafo Canvas e CineSwipe
├── templates/
│   └── index.html          # Interface com Catálogo, Grafo e CineSwipe
├── .gitignore              # Ignora ambientes virtuais, caches e segredos
├── app.py                  # Servidor Flask e rotas REST da API
├── movie_api.py            # Coletor e consolidador de notas e similaridades
├── scraper.py              # Scraper auxiliar Rotten Tomatoes e IMDb
├── requirements.txt        # Dependências Python mínimas
├── vercel.json             # Regras de roteamento e build da Vercel
├── ARCHITECTURE.md         # Documento mestre de arquitetura e estratégia $0
├── CHANGELOG.md            # Registro detalhado de versões
├── ROADMAP.md              # Este manual de continuidade
└── README.md               # Apresentação do projeto e quickstart
```
