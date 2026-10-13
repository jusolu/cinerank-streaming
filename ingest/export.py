"""
Gera data/catalog.db: banco somente leitura usado pelo site.

Contém só títulos com votos suficientes para o ranking, com campos desnormalizados
(gêneros, direção) para listagens rápidas. É o arquivo publicado no deploy.

Uso:
    python -m ingest.export                  # títulos com 500+ votos no IMDb
    python -m ingest.export --min-votes 1000
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import json

from ingest.db import BASE_DIR, DEFAULT_DB, get_state
from ingest.facets import build_all

OUTPUT = os.path.join(BASE_DIR, 'data', 'catalog.db')

SCHEMA = """
CREATE TABLE movies (
    imdb_id         TEXT PRIMARY KEY,
    title           TEXT NOT NULL,       -- título exibido (pt-BR quando disponível)
    original_title  TEXT,
    year            INTEGER,
    runtime         INTEGER,
    genres          TEXT,                -- 'Drama, Thriller'
    directors       TEXT,                -- 'Nome A, Nome B'
    imdb_rating     REAL,
    imdb_votes      INTEGER,
    cinescore       REAL,
    low_confidence  INTEGER,
    rank            INTEGER,             -- posição geral por CineScore
    cast_names      TEXT,                -- até 4 atores principais
    synopsis        TEXT,
    synopsis_lang   TEXT,
    poster_url      TEXT,
    media_source    TEXT,                -- 'tmdb' | 'wikipedia' (origem da sinopse/imagem)
    source_url      TEXT,                -- página de origem, para atribuição
    facets          TEXT,                -- JSON {grupo: [valores]} (ver ingest/facets.py)
    trailer_id      TEXT                 -- ID do trailer no YouTube (Wikidata)
);  -- tabela comum: WITHOUT ROWID desperdiça muito espaço com textos longos (sinopse, atributos)
CREATE INDEX idx_movies_rank ON movies(rank);
CREATE INDEX idx_movies_year_rank ON movies(year, rank);
CREATE INDEX idx_movies_votes ON movies(imdb_votes);

CREATE TABLE movie_genres (imdb_id TEXT NOT NULL, genre TEXT NOT NULL, PRIMARY KEY (genre, imdb_id)) WITHOUT ROWID;
CREATE TABLE people (person_id TEXT PRIMARY KEY, name TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE movie_people (
    imdb_id TEXT NOT NULL, person_id TEXT NOT NULL, role TEXT NOT NULL, ord INTEGER NOT NULL,
    PRIMARY KEY (imdb_id, role, ord, person_id)
) WITHOUT ROWID;
CREATE INDEX idx_mp_person ON movie_people(person_id);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def log(msg):
    print(f"[export] {msg}", flush=True)


def export(min_votes, source=DEFAULT_DB, output=OUTPUT):
    t0 = time.time()
    tmp = output + '.tmp'
    if os.path.exists(tmp):
        for _ in range(15):
            try:
                os.remove(tmp)
                break
            except PermissionError:
                time.sleep(2)

    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    if not get_state(src, 'imdb_loaded_at'):
        raise SystemExit('Banco de origem vazio. Rode antes: python -m ingest.imdb')

    out = sqlite3.connect(f'file:{tmp}', uri=True)  # uri=True permite o ATTACH somente leitura
    out.executescript(SCHEMA)
    out.execute('ATTACH DATABASE ? AS src', (f'file:{source}?mode=ro',))

    with out:
        out.execute("""
            INSERT INTO movies
            SELECT m.imdb_id,
                   COALESCE(m.title_pt, m.title),
                   CASE WHEN m.original_title <> COALESCE(m.title_pt, m.title) THEN m.original_title END,
                   m.year, m.runtime,
                   (SELECT group_concat(genre, ', ') FROM src.movie_genres g WHERE g.imdb_id = m.imdb_id),
                   (SELECT group_concat(name, ', ') FROM (
                        SELECT p.name FROM src.movie_people mp JOIN src.people p USING (person_id)
                        WHERE mp.imdb_id = m.imdb_id AND mp.role = 'director' ORDER BY mp.ord)),
                   m.imdb_rating, m.imdb_votes, m.cinescore, m.low_confidence,
                   ROW_NUMBER() OVER (ORDER BY m.cinescore DESC, m.imdb_votes DESC),
                   (SELECT group_concat(name, ', ') FROM (
                        SELECT p.name FROM src.movie_people mp JOIN src.people p USING (person_id)
                        WHERE mp.imdb_id = m.imdb_id AND mp.role = 'cast' AND mp.person_id NOT LIKE 'tmdb:%'
                        ORDER BY mp.ord LIMIT 4)),
                   -- Sinopse: TMDB em pt (API) > enredo da Wikipédia em pt > TMDB em inglês (cópia) > Wikipédia restante.
                   CASE WHEN t.overview_pt IS NOT NULL THEN t.overview_pt
                        WHEN w.synopsis_lang = 'pt' AND w.synopsis_kind = 'plot' THEN w.synopsis
                        WHEN d.overview_en IS NOT NULL THEN d.overview_en
                        ELSE w.synopsis END,
                   CASE WHEN t.overview_pt IS NOT NULL THEN 'pt'
                        WHEN w.synopsis_lang = 'pt' AND w.synopsis_kind = 'plot' THEN 'pt'
                        WHEN d.overview_en IS NOT NULL THEN 'en'
                        ELSE w.synopsis_lang END,
                   -- Capa: pôster do TMDB (API ou cópia, servido pelo CDN do TMDB) > imagem da Wikipédia.
                   CASE WHEN t.poster_path IS NOT NULL THEN 'https://image.tmdb.org/t/p/w342' || t.poster_path
                        WHEN d.poster_path IS NOT NULL THEN 'https://image.tmdb.org/t/p/w342' || d.poster_path
                        ELSE w.image_url END,
                   -- Fonte da sinopse (para a atribuição na ficha).
                   CASE WHEN t.overview_pt IS NOT NULL THEN 'tmdb'
                        WHEN w.synopsis_lang = 'pt' AND w.synopsis_kind = 'plot' THEN 'wikipedia'
                        WHEN d.overview_en IS NOT NULL THEN 'tmdb'
                        WHEN w.synopsis IS NOT NULL THEN 'wikipedia' END,
                   CASE WHEN t.overview_pt IS NOT NULL THEN 'https://www.themoviedb.org/movie/' || t.tmdb_id
                        WHEN w.synopsis_lang = 'pt' AND w.synopsis_kind = 'plot'
                            THEN 'https://pt.wikipedia.org/wiki/' || replace(w.wiki_title, ' ', '_')
                        WHEN d.overview_en IS NOT NULL THEN 'https://www.themoviedb.org/movie/' || d.tmdb_id
                        WHEN w.wiki_title IS NOT NULL
                            THEN 'https://' || w.wiki_lang || '.wikipedia.org/wiki/' || replace(w.wiki_title, ' ', '_') END,
                   NULL,  -- facets: preenchido abaixo
                   (SELECT MIN(value) FROM src.movie_wikidata x WHERE x.imdb_id = m.imdb_id AND x.prop = 'trailer_youtube')
            FROM src.movies m
            LEFT JOIN src.movie_tmdb t ON t.imdb_id = m.imdb_id
            LEFT JOIN src.movie_wiki w ON w.imdb_id = m.imdb_id
            LEFT JOIN src.movie_dump d ON d.imdb_id = m.imdb_id
            WHERE m.imdb_votes >= ? AND m.cinescore IS NOT NULL
        """, (min_votes,))

        # Pacote de atributos (equipe, origem, enredo, prêmios) de cada filme.
        ids = [r[0] for r in out.execute('SELECT imdb_id FROM movies')]
        facets = build_all(out, ids, table_prefix='src.')
        out.executemany('UPDATE movies SET facets = ? WHERE imdb_id = ?',
                        [(json.dumps(f, ensure_ascii=False, separators=(',', ':')), iid) for iid, f in facets.items()])
        log(f'atributos montados para {len(facets):,} filmes')
        out.execute('INSERT INTO movie_genres SELECT g.imdb_id, g.genre FROM src.movie_genres g JOIN movies USING (imdb_id)')
        out.execute("""
            INSERT INTO movie_people SELECT mp.imdb_id, mp.person_id, mp.role, mp.ord
            FROM src.movie_people mp JOIN movies USING (imdb_id)
            WHERE mp.person_id NOT LIKE 'tmdb:%'
              -- Equipe técnica fica só no pacote de atributos; aqui, o que a busca e o grafo usam.
              AND mp.role IN ('director', 'writer', 'cast')
        """)
        out.execute('INSERT INTO people SELECT p.person_id, p.name FROM src.people p '
                    'WHERE p.person_id IN (SELECT DISTINCT person_id FROM movie_people)')

        meta = {
            'built_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'imdb_loaded_at': get_state(src, 'imdb_loaded_at'),
            'since': get_state(src, 'imdb_since'),
            'prior_mean': get_state(src, 'prior_mean'),
            'prior_votes': get_state(src, 'prior_votes'),
            'min_votes': min_votes,
        }
        out.executemany('INSERT INTO meta VALUES (?, ?)', [(k, str(v)) for k, v in meta.items()])

    out.execute('DETACH DATABASE src')
    out.execute('PRAGMA journal_mode=DELETE')
    out.execute('VACUUM')
    count = out.execute('SELECT COUNT(*) FROM movies').fetchone()[0]
    out.close()
    src.close()
    # No Windows, o antivírus ou um servidor local pode segurar o arquivo por alguns segundos.
    for attempt in range(15):
        try:
            os.replace(tmp, output)
            break
        except PermissionError:
            if attempt == 14:
                raise SystemExit(f'{output} está em uso por outro programa (servidor local aberto?). Feche-o e rode de novo.')
            time.sleep(2)
    log(f'{count:,} filmes → {output} ({os.path.getsize(output) / 1e6:.1f} MB, {time.time() - t0:.0f}s)')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Gera o banco somente leitura do site.')
    parser.add_argument('--min-votes', type=int, default=500)
    args = parser.parse_args(argv)
    export(args.min_votes)


if __name__ == '__main__':
    sys.exit(main())
