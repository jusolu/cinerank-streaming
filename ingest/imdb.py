"""
Carga em bulk a partir dos datasets oficiais do IMDb (https://developer.imdb.com/non-commercial-datasets/).

Não exige chave de API. Licença: uso pessoal e não comercial.

Uso:
    python -m ingest.imdb                 # baixa (se mudou) e carrega filmes desde 1990
    python -m ingest.imdb --since 2000
    python -m ingest.imdb --skip-download # usa os arquivos já baixados
"""
import argparse
import gzip
import os
import sys
import time
from datetime import datetime, timezone

import requests

from ingest.db import BASE_DIR, connect, set_state
from scoring import apply_cinescore

DATASETS_URL = 'https://datasets.imdbws.com/'
RAW_DIR = os.path.join(BASE_DIR, 'data', 'raw')
FILES = ['title.basics', 'title.ratings', 'title.akas', 'title.crew', 'title.principals', 'name.basics']
CAST_SIZE = 6  # atores principais por filme
# Equipe técnica que ajuda a caracterizar o estilo do filme (categoria do IMDb → papel no banco).
CREW_ROLES = {'cinematographer': 'photography', 'composer': 'music', 'editor': 'editing',
              'production_designer': 'design'}
CREW_SIZE = 2  # por papel e por filme

# No catálogo completo há títulos com poucos milhares de votos e nota ~9,9 (votação concentrada).
# Um prior forte evita que eles dominem: com m = 10.000, o topo é formado por filmes
# com centenas de milhares de votos. A média prévia C é a média simples do próprio catálogo.
CATALOG_PRIOR_VOTES = 10000
CATALOG_LOW_CONFIDENCE_VOTES = 1000
PRIOR_MIN_VOTES = 100  # títulos usados para estimar C


def log(msg):
    print(f"[imdb] {msg}", flush=True)


def download(name, raw_dir=RAW_DIR):
    """Baixa <name>.tsv.gz só quando o tamanho remoto difere do arquivo local."""
    os.makedirs(raw_dir, exist_ok=True)
    path = os.path.join(raw_dir, f'{name}.tsv.gz')
    url = f'{DATASETS_URL}{name}.tsv.gz'
    remote_size = int(requests.head(url, timeout=30).headers.get('content-length', 0))
    if os.path.exists(path) and os.path.getsize(path) == remote_size:
        log(f'{name}: já atualizado ({remote_size / 1e6:.0f} MB)')
        return path

    t0 = time.time()
    tmp = path + '.part'
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    os.replace(tmp, path)
    log(f'{name}: baixado {remote_size / 1e6:.0f} MB em {time.time() - t0:.1f}s')
    return path


def read_tsv(path):
    """Itera as linhas de um .tsv.gz do IMDb como listas; '\\N' vira None."""
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        next(f)  # cabeçalho
        for line in f:
            yield [None if v == '\\N' else v for v in line.rstrip('\n').split('\t')]


def to_int(v):
    return int(v) if v and v.isdigit() else None


def principal_credits(principals_path, movie_ids, roles=None):
    """Créditos (filme, pessoa, papel, ordem) de title.principals.

    roles=None: elenco (até CAST_SIZE) + equipe técnica (até CREW_SIZE por papel).
    roles={'photography', ...}: só esses papéis da equipe técnica.
    """
    out = []
    count = {}
    seen = set()  # a mesma pessoa pode aparecer mais de uma vez no mesmo filme
    for tconst, _ord, nconst, category, *_ in read_tsv(principals_path):
        if tconst not in movie_ids:
            continue
        if category in ('actor', 'actress'):
            role, limit = 'cast', CAST_SIZE
        elif category in CREW_ROLES:
            role, limit = CREW_ROLES[category], CREW_SIZE
        else:
            continue
        if roles is not None and role not in roles:
            continue
        key = (tconst, role)
        n = count.get(key, 0)
        if n >= limit or (tconst, nconst, role) in seen:
            continue
        seen.add((tconst, nconst, role))
        count[key] = n + 1
        out.append((tconst, nconst, role, n))
    return out


def load_crew_only(conn, raw_dir=RAW_DIR, chunk=20000):
    """Acrescenta só a equipe técnica, sem refazer a carga. Grava em lotes curtos para não
    bloquear outros coletores que escrevem no mesmo banco."""
    path = lambda name: os.path.join(raw_dir, f'{name}.tsv.gz')
    t0 = time.time()
    movie_ids = {r[0] for r in conn.execute('SELECT imdb_id FROM movies')}
    roles = set(CREW_ROLES.values())
    credits = principal_credits(path('title.principals'), movie_ids, roles)
    log(f'title.principals: {len(credits):,} créditos de equipe técnica ({time.time() - t0:.0f}s)')

    needed = {c[1] for c in credits}
    known = {r[0] for r in conn.execute('SELECT person_id FROM people')}
    missing = needed - known
    people = [(nconst, name) for nconst, name, *_ in read_tsv(path('name.basics')) if nconst in missing and name]
    log(f'name.basics: {len(people):,} pessoas novas')

    marks = ','.join('?' * len(roles))
    with conn:
        conn.execute(f'DELETE FROM movie_people WHERE role IN ({marks})', tuple(roles))
    for i in range(0, len(people), chunk):
        with conn:
            conn.executemany('INSERT OR IGNORE INTO people (person_id, name) VALUES (?, ?)', people[i:i + chunk])
    valid = known | {p[0] for p in people}
    rows = [c for c in credits if c[1] in valid]
    for i in range(0, len(rows), chunk):
        with conn:
            conn.executemany('INSERT OR IGNORE INTO movie_people (imdb_id, person_id, role, ord) VALUES (?, ?, ?, ?)',
                             rows[i:i + chunk])
    log(f'{len(rows):,} créditos gravados ({time.time() - t0:.0f}s)')


def load(conn, since, raw_dir=RAW_DIR):
    path = lambda name: os.path.join(raw_dir, f'{name}.tsv.gz')

    # 1. Títulos: longas não adultos lançados a partir de `since`.
    t0 = time.time()
    movies = {}
    genres = []
    for tconst, ttype, primary, original, is_adult, start, _end, runtime, gens in read_tsv(path('title.basics')):
        year = to_int(start)
        if ttype != 'movie' or is_adult == '1' or year is None or year < since:
            continue
        movies[tconst] = {
            'imdb_id': tconst, 'title': primary, 'original_title': original,
            'year': year, 'runtime': to_int(runtime),
            'title_pt': None, 'imdb_rating': None, 'imdb_votes': None
        }
        for g in (gens or '').split(','):
            if g:
                genres.append((tconst, g))
    log(f'title.basics: {len(movies):,} filmes desde {since} ({time.time() - t0:.0f}s)')

    # 2. Notas.
    t0 = time.time()
    rated = 0
    for tconst, avg, votes in read_tsv(path('title.ratings')):
        m = movies.get(tconst)
        if m:
            m['imdb_rating'] = float(avg)
            m['imdb_votes'] = int(votes)
            rated += 1
    log(f'title.ratings: {rated:,} com nota ({time.time() - t0:.0f}s)')

    # 3. Título no Brasil (prefere o título de exibição do IMDb para a região BR).
    t0 = time.time()
    best_rank = {}
    for title_id, _ord, title, region, _lang, types, _attrs, _orig in read_tsv(path('title.akas')):
        if region != 'BR' or title_id not in movies:
            continue
        rank = 0 if types and 'imdbDisplay' in types else 1
        if rank < best_rank.get(title_id, 2):
            best_rank[title_id] = rank
            movies[title_id]['title_pt'] = title
    log(f'title.akas: {len(best_rank):,} títulos em português ({time.time() - t0:.0f}s)')

    # 4. Direção e roteiro.
    t0 = time.time()
    credits = []
    needed_people = set()
    for tconst, directors, writers in read_tsv(path('title.crew')):
        if tconst not in movies:
            continue
        for role, ids in (('director', directors), ('writer', writers)):
            for i, pid in enumerate(dict.fromkeys((ids or '').split(','))):
                if pid:
                    credits.append((tconst, pid, role, i))
                    needed_people.add(pid)
    log(f'title.crew: {len(credits):,} créditos ({time.time() - t0:.0f}s)')

    # 5. Elenco principal e equipe técnica (fotografia, trilha, montagem, direção de arte).
    t0 = time.time()
    principal = principal_credits(path('title.principals'), movies)
    credits += principal
    needed_people.update(c[1] for c in principal)
    log(f'title.principals: {len(principal):,} créditos de elenco e equipe ({time.time() - t0:.0f}s)')

    t0 = time.time()
    people = [(nconst, name) for nconst, name, *_ in read_tsv(path('name.basics')) if nconst in needed_people and name]
    log(f'name.basics: {len(people):,} pessoas ({time.time() - t0:.0f}s)')

    # 6. CineScore (só IMDb por enquanto; recalculado quando o TMDB enriquecer o filme).
    base = [m['imdb_rating'] * 10 for m in movies.values() if (m['imdb_votes'] or 0) >= PRIOR_MIN_VOTES]
    prior_mean = round(sum(base) / len(base), 1) if base else 60.0
    for m in movies.values():
        sc = apply_cinescore({'imdb': m['imdb_rating'], 'imdb_votes': m['imdb_votes']},
                             prior_votes=CATALOG_PRIOR_VOTES, prior_mean=prior_mean,
                             low_confidence_votes=CATALOG_LOW_CONFIDENCE_VOTES)
        m['cinescore'] = sc['cinescore']
        m['low_confidence'] = int(sc['low_confidence'])
    log(f'CineScore: prior m={CATALOG_PRIOR_VOTES:,}, C={prior_mean}')

    # 7. Gravação numa única transação. O upsert preserva tmdb_id de filmes já enriquecidos.
    t0 = time.time()
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    known_people = {pid for pid, _ in people}
    with conn:
        conn.executemany("""
            INSERT INTO movies (imdb_id, title, original_title, title_pt, year, runtime,
                                imdb_rating, imdb_votes, cinescore, low_confidence, updated_at)
            VALUES (:imdb_id, :title, :original_title, :title_pt, :year, :runtime,
                    :imdb_rating, :imdb_votes, :cinescore, :low_confidence, :updated_at)
            ON CONFLICT(imdb_id) DO UPDATE SET
                title = excluded.title, original_title = excluded.original_title,
                title_pt = excluded.title_pt, year = excluded.year, runtime = excluded.runtime,
                imdb_rating = excluded.imdb_rating, imdb_votes = excluded.imdb_votes,
                cinescore = excluded.cinescore, low_confidence = excluded.low_confidence,
                updated_at = excluded.updated_at
        """, [{**m, 'updated_at': now} for m in movies.values()])

        conn.execute('DELETE FROM movie_genres')
        conn.executemany('INSERT INTO movie_genres (imdb_id, genre) VALUES (?, ?)', genres)

        conn.executemany(
            'INSERT INTO people (person_id, name) VALUES (?, ?) '
            'ON CONFLICT(person_id) DO UPDATE SET name = excluded.name', people)
        # Créditos vindos do TMDB (person_id 'tmdb:…') são preservados.
        conn.execute("DELETE FROM movie_people WHERE person_id NOT LIKE 'tmdb:%'")
        conn.executemany(
            'INSERT INTO movie_people (imdb_id, person_id, role, ord) VALUES (?, ?, ?, ?)',
            [c for c in credits if c[1] in known_people])

        set_state(conn, 'imdb_loaded_at', now)
        set_state(conn, 'imdb_since', since)
        set_state(conn, 'prior_mean', prior_mean)
        set_state(conn, 'prior_votes', CATALOG_PRIOR_VOTES)
    log(f'gravação no banco ({time.time() - t0:.0f}s)')
    return len(movies)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Carga em bulk dos datasets do IMDb.')
    parser.add_argument('--since', type=int, default=1990, help='Ano inicial (padrão: 1990)')
    parser.add_argument('--skip-download', action='store_true', help='Usa os arquivos já em data/raw')
    parser.add_argument('--crew-only', action='store_true',
                        help='Só acrescenta a equipe técnica (fotografia, trilha, montagem, arte) aos filmes já carregados')
    args = parser.parse_args(argv)

    t0 = time.time()
    if not args.skip_download:
        for name in FILES:
            download(name)
    conn = connect()
    if args.crew_only:
        load_crew_only(conn)
        conn.close()
        return 0
    total = load(conn, args.since)
    conn.close()
    log(f'concluído: {total:,} filmes em {time.time() - t0:.0f}s')


if __name__ == '__main__':
    sys.exit(main())
