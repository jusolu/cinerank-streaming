"""
Conexão e esquema do catálogo.

SQLite local para testes (custo zero). O SQL evita recursos específicos do SQLite
para que o mesmo esquema possa ser aplicado no Postgres (Supabase) depois.
"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.environ.get('CINERANK_DB', os.path.join(BASE_DIR, 'data', 'cinerank.db'))

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    imdb_id         TEXT PRIMARY KEY,
    tmdb_id         INTEGER,
    title           TEXT NOT NULL,
    original_title  TEXT,
    title_pt        TEXT,
    year            INTEGER,
    runtime         INTEGER,
    imdb_rating     REAL,
    imdb_votes      INTEGER,
    cinescore       REAL,
    low_confidence  INTEGER,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_movies_year ON movies(year);
CREATE INDEX IF NOT EXISTS idx_movies_votes ON movies(imdb_votes);
CREATE INDEX IF NOT EXISTS idx_movies_cinescore ON movies(cinescore);

CREATE TABLE IF NOT EXISTS movie_genres (
    imdb_id  TEXT NOT NULL REFERENCES movies(imdb_id),
    genre    TEXT NOT NULL,
    PRIMARY KEY (imdb_id, genre)
);

CREATE TABLE IF NOT EXISTS people (
    person_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movie_people (
    imdb_id    TEXT NOT NULL REFERENCES movies(imdb_id),
    person_id  TEXT NOT NULL REFERENCES people(person_id),
    role       TEXT NOT NULL,            -- 'director' | 'writer'
    ord        INTEGER NOT NULL,
    PRIMARY KEY (imdb_id, person_id, role)
);
CREATE INDEX IF NOT EXISTS idx_movie_people_person ON movie_people(person_id);

-- Enriquecimento via TMDB (somente para títulos selecionados).
CREATE TABLE IF NOT EXISTS movie_tmdb (
    imdb_id        TEXT PRIMARY KEY REFERENCES movies(imdb_id),
    tmdb_id        INTEGER,               -- NULL quando o TMDB não conhece o título
    overview_pt    TEXT,
    poster_path    TEXT,
    backdrop_path  TEXT,
    trailer_key    TEXT,
    tmdb_rating    REAL,
    tmdb_votes     INTEGER,
    fetched_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movie_keywords (
    imdb_id  TEXT NOT NULL REFERENCES movies(imdb_id),
    keyword  TEXT NOT NULL,
    PRIMARY KEY (imdb_id, keyword)
);
CREATE INDEX IF NOT EXISTS idx_movie_keywords_kw ON movie_keywords(keyword);

-- Onde assistir (dados do JustWatch via TMDB). kind: flatrate | rent | buy | ads | free
CREATE TABLE IF NOT EXISTS movie_providers (
    imdb_id        TEXT NOT NULL REFERENCES movies(imdb_id),
    country        TEXT NOT NULL,
    provider_id    INTEGER NOT NULL,
    provider_name  TEXT NOT NULL,
    kind           TEXT NOT NULL,
    PRIMARY KEY (imdb_id, country, provider_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_movie_providers_p ON movie_providers(country, provider_id, kind);

-- Sinopse e imagem vindas da Wikipédia (fonte sem chave; o TMDB tem prioridade quando existir).
CREATE TABLE IF NOT EXISTS movie_wiki (
    imdb_id          TEXT PRIMARY KEY REFERENCES movies(imdb_id),
    wiki_lang        TEXT,      -- 'pt' | 'en' | NULL (sem artigo)
    wiki_title       TEXT,
    synopsis         TEXT,
    synopsis_lang    TEXT,
    synopsis_kind    TEXT,      -- 'plot' (seção de enredo) | 'lead' (introdução do artigo)
    image_url        TEXT,
    fetched_at       TEXT NOT NULL
);

-- Cópia pública da base do TMDB (Hugging Face), sem chave: pôster, sinopse em inglês, palavras-chave.
CREATE TABLE IF NOT EXISTS movie_dump (
    imdb_id      TEXT PRIMARY KEY REFERENCES movies(imdb_id),
    tmdb_id      INTEGER,
    overview_en  TEXT,
    poster_path  TEXT,
    tagline      TEXT,
    loaded_at    TEXT NOT NULL
);

-- Atributos do Wikidata em formato longo: (filme, propriedade, valor).
CREATE TABLE IF NOT EXISTS movie_wikidata (
    imdb_id  TEXT NOT NULL REFERENCES movies(imdb_id),
    prop     TEXT NOT NULL,
    value    TEXT NOT NULL,
    PRIMARY KEY (imdb_id, prop, value)
);
CREATE TABLE IF NOT EXISTS movie_wikidata_done (
    imdb_id     TEXT PRIMARY KEY,
    fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""


def load_dotenv(path=os.path.join(BASE_DIR, '.env')):
    """Carrega KEY=valor de um .env local (fora do git) sem sobrescrever variáveis já definidas."""
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect(path=DEFAULT_DB):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)  # vários coletores podem escrever ao mesmo tempo
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript(SCHEMA)
    return conn


def set_state(conn, key, value):
    conn.execute(
        'INSERT INTO sync_state(key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        (key, str(value))
    )


def get_state(conn, key, default=None):
    row = conn.execute('SELECT value FROM sync_state WHERE key = ?', (key,)).fetchone()
    return row[0] if row else default
