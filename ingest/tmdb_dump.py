"""
Pôsteres, sinopses (em inglês) e palavras-chave a partir de uma cópia pública da base do TMDB
publicada no Hugging Face. Não exige chave: as imagens são servidas pelo CDN do TMDB.

Fonte: https://huggingface.co/datasets/HenryWaltson/TMDB-IMDB-Movies-Dataset (atualizada em jan/2026;
cobre bem até meados de 2024). É uma cópia feita por terceiros: use apenas de forma pessoal e não comercial.

Uso:
    python -m ingest.tmdb_dump                  # baixa (se ainda não baixou) e carrega
    python -m ingest.tmdb_dump --skip-download
"""
import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone

import requests

from ingest.db import BASE_DIR, connect, set_state

DUMP_URL = ('https://huggingface.co/datasets/HenryWaltson/TMDB-IMDB-Movies-Dataset/resolve/main/'
            'TMDB%20%20IMDB%20Movies%20Dataset.csv')
DUMP_PATH = os.path.join(BASE_DIR, 'data', 'raw', 'tmdb_dump.csv')


def log(msg):
    print(f"[tmdb_dump] {msg}", flush=True)


def download():
    os.makedirs(os.path.dirname(DUMP_PATH), exist_ok=True)
    t0 = time.time()
    tmp = DUMP_PATH + '.part'
    with requests.get(DUMP_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    os.replace(tmp, DUMP_PATH)
    log(f'baixado {os.path.getsize(DUMP_PATH) / 1e6:.0f} MB em {time.time() - t0:.0f}s')


EXTRA_COLUMNS = {'original_language': 'TEXT', 'countries': 'TEXT', 'companies': 'TEXT', 'budget': 'INTEGER'}


def ensure_columns(conn):
    """Acrescenta colunas novas a bancos criados antes delas existirem."""
    have = {r[1] for r in conn.execute('PRAGMA table_info(movie_dump)')}
    for name, kind in EXTRA_COLUMNS.items():
        if name not in have:
            conn.execute(f'ALTER TABLE movie_dump ADD COLUMN {name} {kind}')


def load(conn):
    csv.field_size_limit(10**8)
    ensure_columns(conn)
    known = {r[0] for r in conn.execute('SELECT imdb_id FROM movies')}
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    rows, keywords = [], []
    t0 = time.time()
    with open(DUMP_PATH, encoding='utf-8', newline='') as f:
        for r in csv.DictReader(f):
            iid = (r.get('tconst') or '').strip()
            if iid not in known:
                continue
            try:
                tmdb_id = int(r.get('id') or 0) or None
            except ValueError:
                tmdb_id = None
            try:
                budget = int(float(r.get('budget') or 0)) or None
            except ValueError:
                budget = None
            rows.append((iid, tmdb_id, (r.get('overview') or '').strip() or None,
                         (r.get('poster_path') or '').strip() or None,
                         (r.get('tagline') or '').strip() or None, now,
                         (r.get('original_language') or '').strip() or None,
                         (r.get('production_countries') or '').strip() or None,
                         (r.get('production_companies') or '').strip() or None,
                         budget))
            for k in (r.get('keywords') or '').split(','):
                k = k.strip()
                if k:
                    keywords.append((iid, k))
    log(f'{len(rows):,} filmes do catálogo encontrados na cópia ({time.time() - t0:.0f}s)')

    with conn:
        conn.executemany("""
            INSERT INTO movie_dump (imdb_id, tmdb_id, overview_en, poster_path, tagline, loaded_at,
                                    original_language, countries, companies, budget)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(imdb_id) DO UPDATE SET
                tmdb_id = excluded.tmdb_id, overview_en = excluded.overview_en,
                poster_path = excluded.poster_path, tagline = excluded.tagline, loaded_at = excluded.loaded_at,
                original_language = excluded.original_language, countries = excluded.countries,
                companies = excluded.companies, budget = excluded.budget
        """, rows)
        # Palavras-chave do enredo (úteis para o grafo); não sobrescreve as vindas da API do TMDB.
        conn.executemany('INSERT OR IGNORE INTO movie_keywords (imdb_id, keyword) VALUES (?, ?)', keywords)
        set_state(conn, 'tmdb_dump_loaded_at', now)
    log(f'{sum(1 for r in rows if r[3]):,} pôsteres · {sum(1 for r in rows if r[2]):,} sinopses · {len(keywords):,} palavras-chave')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Pôsteres e sinopses via cópia pública do TMDB (sem chave).')
    parser.add_argument('--skip-download', action='store_true')
    args = parser.parse_args(argv)
    if not args.skip_download and not os.path.exists(DUMP_PATH):
        download()
    conn = connect()
    load(conn)
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
