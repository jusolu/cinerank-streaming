"""
Identificadores do filme em outras plataformas, pelo Wikidata (sem chave): Letterboxd, TMDB,
Rotten Tomatoes, Metacritic, MUBI, Netflix, Apple TV, Prime Video, Trakt, Kinopoisk, Douban,
FilmAffinity, AlloCiné, AdoroCinema, Filmweb, Box Office Mojo, The Numbers, LUMIERE.

Servem para (1) links diretos no site, (2) sinais: em quantas plataformas o filme está, se está
num catálogo de streaming, se está na curadoria do MUBI. Grava movie_xref (imdb_id, platform, ext_id).
    .venv-ml/Scripts/python -m ingest.xrefs
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from ingest.db import connect
from ingest.wikidata import SPARQL, USER_AGENT

PLATFORMS = {'P6127': 'letterboxd', 'P4947': 'tmdb', 'P1258': 'rottentomatoes', 'P1712': 'metacritic', 'P7299': 'mubi',
             'P1874': 'netflix', 'P9586': 'appletv', 'P8055': 'primevideo', 'P8013': 'trakt', 'P2603': 'kinopoisk',
             'P4529': 'douban', 'P480': 'filmaffinity', 'P1265': 'allocine', 'P7777': 'adorocinema', 'P5032': 'filmweb',
             'P1237': 'boxofficemojo', 'P3808': 'thenumbers', 'P4282': 'lumiere'}
BATCH = 200
session = requests.Session()
session.headers['User-Agent'] = USER_AGENT


def query(batch):
    vals = ' '.join(f'"{i}"' for i in batch)
    props = ' '.join(f'wdt:{p}' for p in PLATFORMS)
    q = f'SELECT ?imdb ?prop ?v WHERE {{ VALUES ?imdb {{ {vals} }} VALUES ?prop {{ {props} }} ?item wdt:P345 ?imdb ; ?prop ?v . }}'
    for attempt in range(6):
        try:
            r = session.post(SPARQL, data={'query': q, 'format': 'json'}, timeout=120)
            if r.ok:
                return batch, [(b['imdb']['value'], PLATFORMS[b['prop']['value'].rsplit('/', 1)[1]], b['v']['value'])
                               for b in r.json()['results']['bindings']]
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 ** (attempt + 1))
    return batch, None


def main():
    conn = connect()
    conn.execute('CREATE TABLE IF NOT EXISTS movie_xref (imdb_id TEXT, platform TEXT, ext_id TEXT, PRIMARY KEY (imdb_id, platform, ext_id))')
    conn.execute('CREATE TABLE IF NOT EXISTS movie_xref_done (imdb_id TEXT PRIMARY KEY)')
    done = {r[0] for r in conn.execute('SELECT imdb_id FROM movie_xref_done')}
    todo = [r[0] for r in conn.execute('SELECT imdb_id FROM movies WHERE imdb_votes >= 500') if r[0] not in done]
    print(f'[xref] {len(todo):,} filmes', flush=True)
    t0 = time.time()
    batches = [todo[k:k + BATCH] for k in range(0, len(todo), BATCH)]
    with ThreadPoolExecutor(4) as ex:
        for n, (batch, rows) in enumerate(ex.map(query, batches), 1):
            if rows is None:
                continue
            with conn:
                conn.executemany('INSERT OR IGNORE INTO movie_xref VALUES (?, ?, ?)', rows)
                conn.executemany('INSERT OR IGNORE INTO movie_xref_done VALUES (?)', [(i,) for i in batch])
            if n % 50 == 0:
                print(f'  {n}/{len(batches)} lotes ({time.time() - t0:.0f}s)', flush=True)
    total = conn.execute('SELECT COUNT(*) FROM movies WHERE imdb_votes >= 500').fetchone()[0]
    print('\n== Cobertura no catálogo (53 mil filmes): ' + ', '.join(
        f'{p} {n / total:.0%}' for p, n in conn.execute(
            'SELECT platform, COUNT(DISTINCT imdb_id) FROM movie_xref GROUP BY platform ORDER BY 2 DESC')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
