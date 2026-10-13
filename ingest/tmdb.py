"""
Enriquecimento via API do TMDB: sinopse em pt-BR, pôster, trailer, elenco,
palavras-chave e onde assistir no Brasil. Requer TMDB_API_KEY (gratuita).

Retomável: títulos já buscados são pulados, e o progresso é gravado a cada lote.

Uso:
    python -m ingest.tmdb --limit 200          # amostra: 200 títulos mais votados ainda não buscados
    python -m ingest.tmdb --min-votes 100      # todos com 100+ votos no IMDb
    python -m ingest.tmdb --refresh-days 30    # rebusca títulos buscados há mais de 30 dias
"""
import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

from ingest.db import connect, get_state, load_dotenv
from scoring import apply_cinescore

API = 'https://api.themoviedb.org/3'
COUNTRY = 'BR'
MAX_RPS = 35       # abaixo do limite do TMDB (~40-50 req/s)
WORKERS = 8
BATCH = 100
CAST_SIZE = 8


def log(msg):
    print(f"[tmdb] {msg}", flush=True)


class RateLimiter:
    """Espaça as requisições para no máximo `rps` por segundo, entre todas as threads."""

    def __init__(self, rps):
        self.interval = 1.0 / rps
        self.lock = threading.Lock()
        self.next_at = time.monotonic()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = self.next_at - now
            self.next_at = max(now, self.next_at) + self.interval
        if delay > 0:
            time.sleep(delay)


class TMDB:
    def __init__(self, api_key, rps=MAX_RPS):
        self.session = requests.Session()
        self.api_key = api_key
        self.limiter = RateLimiter(rps)
        self.requests = 0

    def get(self, path, **params):
        for attempt in range(5):
            self.limiter.wait()
            self.requests += 1
            r = self.session.get(f'{API}{path}', params={'api_key': self.api_key, **params}, timeout=15)
            if r.status_code == 429:
                time.sleep(float(r.headers.get('Retry-After', 2)))
                continue
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f'TMDB falhou repetidamente em {path}')


def fetch_light(api, imdb_id):
    """Uma chamada: /find já traz sinopse em pt-BR, pôster e nota. Sem elenco, palavras-chave ou provedores."""
    found = api.get(f'/find/{imdb_id}', external_source='imdb_id', language='pt-BR')
    results = (found or {}).get('movie_results') or []
    if not results:
        return {'imdb_id': imdb_id, 'tmdb_id': None}
    r = results[0]
    return {
        'imdb_id': imdb_id,
        'tmdb_id': r['id'],
        'overview_pt': r.get('overview') or None,
        'poster_path': r.get('poster_path'),
        'backdrop_path': r.get('backdrop_path'),
        'trailer_key': None,
        'tmdb_rating': r.get('vote_average') if r.get('vote_count') else None,
        'tmdb_votes': r.get('vote_count') or 0,
        'title_pt': r.get('title'),
        'keywords': None,
        'providers': None,
        'cast': None,
    }


def fetch_one(api, imdb_id):
    """Retorna o pacote de dados de um título (ou só o marcador de 'não encontrado')."""
    found = api.get(f'/find/{imdb_id}', external_source='imdb_id')
    results = (found or {}).get('movie_results') or []
    if not results:
        return {'imdb_id': imdb_id, 'tmdb_id': None}

    tmdb_id = results[0]['id']
    det = api.get(f'/movie/{tmdb_id}', language='pt-BR',
                  append_to_response='credits,keywords,videos,watch/providers',
                  include_video_language='pt,en,null')
    if not det:
        return {'imdb_id': imdb_id, 'tmdb_id': None}

    trailer = None
    for v in (det.get('videos') or {}).get('results', []):
        if v.get('site') == 'YouTube' and v.get('type') in ('Trailer', 'Teaser'):
            trailer = v.get('key')
            if v.get('type') == 'Trailer':
                break

    providers = []
    region = ((det.get('watch/providers') or {}).get('results') or {}).get(COUNTRY) or {}
    for kind in ('flatrate', 'rent', 'buy', 'ads', 'free'):
        for p in region.get(kind, []):
            providers.append((imdb_id, COUNTRY, p['provider_id'], p['provider_name'], kind))

    cast = [(f"tmdb:{c['id']}", c['name'], i)
            for i, c in enumerate((det.get('credits') or {}).get('cast', [])[:CAST_SIZE]) if c.get('name')]

    return {
        'imdb_id': imdb_id,
        'tmdb_id': tmdb_id,
        'overview_pt': det.get('overview') or None,
        'poster_path': det.get('poster_path'),
        'backdrop_path': det.get('backdrop_path'),
        'trailer_key': trailer,
        'tmdb_rating': det.get('vote_average') if det.get('vote_count') else None,
        'tmdb_votes': det.get('vote_count') or 0,
        'title_pt': det.get('title'),
        'keywords': [k['name'] for k in (det.get('keywords') or {}).get('keywords', []) if k.get('name')],
        'providers': providers,
        'cast': cast,
    }


def save(conn, items, prior):
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with conn:
        for it in items:
            iid = it['imdb_id']
            conn.execute("""
                INSERT INTO movie_tmdb (imdb_id, tmdb_id, overview_pt, poster_path, backdrop_path,
                                        trailer_key, tmdb_rating, tmdb_votes, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(imdb_id) DO UPDATE SET
                    tmdb_id = excluded.tmdb_id, overview_pt = excluded.overview_pt,
                    poster_path = excluded.poster_path, backdrop_path = excluded.backdrop_path,
                    trailer_key = excluded.trailer_key, tmdb_rating = excluded.tmdb_rating,
                    tmdb_votes = excluded.tmdb_votes, fetched_at = excluded.fetched_at
            """, (iid, it['tmdb_id'], it.get('overview_pt'), it.get('poster_path'), it.get('backdrop_path'),
                  it.get('trailer_key'), it.get('tmdb_rating'), it.get('tmdb_votes'), now))
            if it['tmdb_id'] is None:
                continue

            conn.execute('UPDATE movies SET tmdb_id = ? WHERE imdb_id = ?', (it['tmdb_id'], iid))
            # O título em português do TMDB só preenche lacunas do IMDb.
            if it.get('title_pt'):
                conn.execute('UPDATE movies SET title_pt = COALESCE(title_pt, ?) WHERE imdb_id = ?',
                             (it['title_pt'], iid))

            if it['keywords'] is not None:
                conn.execute('DELETE FROM movie_keywords WHERE imdb_id = ?', (iid,))
                conn.executemany('INSERT OR IGNORE INTO movie_keywords (imdb_id, keyword) VALUES (?, ?)',
                                 [(iid, k) for k in it['keywords']])

            if it['providers'] is not None:
                conn.execute('DELETE FROM movie_providers WHERE imdb_id = ?', (iid,))
                conn.executemany('INSERT OR IGNORE INTO movie_providers VALUES (?, ?, ?, ?, ?)', it['providers'])

            if it['cast'] is not None:
                conn.execute("DELETE FROM movie_people WHERE imdb_id = ? AND role = 'cast' AND person_id LIKE 'tmdb:%'", (iid,))
                conn.executemany('INSERT INTO people (person_id, name) VALUES (?, ?) '
                                 'ON CONFLICT(person_id) DO UPDATE SET name = excluded.name',
                                 [(pid, name) for pid, name, _ in it['cast']])
                conn.executemany("INSERT OR IGNORE INTO movie_people (imdb_id, person_id, role, ord) VALUES (?, ?, 'cast', ?)",
                                 [(iid, pid, i) for pid, _, i in it['cast']])

            # CineScore com IMDb + TMDB, mesmo prior da carga do IMDb.
            imdb_rating, imdb_votes = conn.execute(
                'SELECT imdb_rating, imdb_votes FROM movies WHERE imdb_id = ?', (iid,)).fetchone()
            sc = apply_cinescore({'imdb': imdb_rating, 'imdb_votes': imdb_votes,
                                  'tmdb': it['tmdb_rating'], 'tmdb_votes': it['tmdb_votes']}, **prior)
            conn.execute('UPDATE movies SET cinescore = ?, low_confidence = ? WHERE imdb_id = ?',
                         (sc['cinescore'], int(sc['low_confidence']), iid))


def select_pending(conn, min_votes, refresh_days, limit):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=refresh_days)).isoformat() if refresh_days else None
    sql = """
        SELECT m.imdb_id FROM movies m
        LEFT JOIN movie_tmdb t ON t.imdb_id = m.imdb_id
        WHERE m.imdb_votes >= ? AND (t.imdb_id IS NULL {refresh})
        ORDER BY m.imdb_votes DESC
    """.format(refresh='OR t.fetched_at < ?' if cutoff else '')
    params = [min_votes] + ([cutoff] if cutoff else [])
    if limit:
        sql += ' LIMIT ?'
        params.append(limit)
    return [r[0] for r in conn.execute(sql, params)]


def main(argv=None):
    parser = argparse.ArgumentParser(description='Enriquecimento do catálogo via TMDB.')
    parser.add_argument('--min-votes', type=int, default=500, help='Votos mínimos no IMDb (padrão: 500)')
    parser.add_argument('--limit', type=int, default=0, help='Máximo de títulos nesta execução (0 = todos)')
    parser.add_argument('--refresh-days', type=int, default=0, help='Rebusca títulos mais antigos que N dias')
    parser.add_argument('--full', action='store_true',
                        help='Busca completa (2 chamadas): também palavras-chave, trailer e onde assistir')
    args = parser.parse_args(argv)

    load_dotenv()
    api_key = os.environ.get('TMDB_API_KEY')
    if not api_key:
        log('TMDB_API_KEY não configurada. Crie a chave em https://www.themoviedb.org/settings/api e salve em .env (TMDB_API_KEY=...)')
        return 1

    conn = connect()
    if not get_state(conn, 'imdb_loaded_at'):
        log('Rode antes a carga do IMDb: python -m ingest.imdb')
        return 1
    prior = {
        'prior_votes': int(get_state(conn, 'prior_votes', 10000)),
        'prior_mean': float(get_state(conn, 'prior_mean', 60.0)),
        'low_confidence_votes': 1000,
    }

    pending = select_pending(conn, args.min_votes, args.refresh_days, args.limit)
    total_eligible = conn.execute('SELECT COUNT(*) FROM movies WHERE imdb_votes >= ?', (args.min_votes,)).fetchone()[0]
    log(f'{len(pending):,} títulos a buscar ({total_eligible:,} elegíveis com {args.min_votes}+ votos)')
    if not pending:
        return 0

    api = TMDB(api_key)
    t0 = time.time()
    done = missing = failed = 0
    buffer = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        fetch = fetch_one if args.full else fetch_light
        futures = {pool.submit(fetch, api, iid): iid for iid in pending}
        for fut in as_completed(futures):
            try:
                item = fut.result()
            except Exception as e:
                failed += 1
                log(f'falha em {futures[fut]}: {e}')
                continue
            missing += item['tmdb_id'] is None
            buffer.append(item)
            done += 1
            if len(buffer) >= BATCH:
                save(conn, buffer, prior)
                buffer.clear()
                rate = done / (time.time() - t0)
                log(f'{done:,}/{len(pending):,} · {rate:.1f} títulos/s · {api.requests:,} requisições')
    if buffer:
        save(conn, buffer, prior)

    elapsed = time.time() - t0
    rate = done / elapsed if elapsed else 0
    log(f'concluído: {done:,} títulos em {elapsed:.0f}s ({rate:.1f}/s), {missing:,} sem correspondência no TMDB, {failed:,} falhas')
    remaining = len(select_pending(conn, args.min_votes, 0, 0))
    if remaining and rate:
        log(f'restam {remaining:,} títulos ≈ {remaining / rate / 60:.0f} min neste ritmo')
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
