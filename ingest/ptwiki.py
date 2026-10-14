"""
Interesse no Brasil e em Portugal: artigo na Wikipédia em português (pelo Wikidata) e visualizações
dos últimos 60 dias (API da Wikipédia em lote, 50 artigos por consulta, sem chave).

Também guarda o país de origem do filme (Wikidata P495) para as análises.
Grava movie_ptwiki (imdb_id, ptwiki, pt_pageviews) e movie_country (imdb_id, country).
    .venv-ml/Scripts/python -m ingest.ptwiki
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from ingest.db import connect
from ingest.fame import pageviews as _unused  # noqa: F401  (mesma lógica, outro idioma abaixo)
from ingest.wikidata import SPARQL, USER_AGENT

PT_API = 'https://pt.wikipedia.org/w/api.php'
session = requests.Session()
session.headers['User-Agent'] = USER_AGENT


def wikidata(batch):
    vals = ' '.join(f'"{i}"' for i in batch)
    q = f"""SELECT ?imdb ?article ?countryLabel WHERE {{
      VALUES ?imdb {{ {vals} }} ?item wdt:P345 ?imdb .
      OPTIONAL {{ ?article schema:about ?item ; schema:isPartOf <https://pt.wikipedia.org/> . }}
      OPTIONAL {{ ?item wdt:P495 ?country . }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "pt,en". }} }}"""
    for attempt in range(6):
        try:
            r = session.post(SPARQL, data={'query': q, 'format': 'json'}, timeout=120)
            if r.ok:
                return batch, r.json()['results']['bindings']
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 ** (attempt + 1))
    return batch, None


def pt_views(titles):
    from urllib.parse import unquote
    names = [unquote(t).replace('_', ' ') for t in titles]
    params = {'action': 'query', 'prop': 'pageviews', 'pvipdays': 60, 'titles': '|'.join(names), 'redirects': 1,
              'format': 'json', 'formatversion': 2}
    views, alias, cont = {}, {}, {}
    for _ in range(50):
        r = None
        for attempt in range(6):
            try:
                r = session.get(PT_API, params={**params, **cont}, timeout=60)
                if r.ok:
                    break
            except requests.RequestException:
                r = None
            time.sleep(2 ** (attempt + 1))
        if r is None or not r.ok:
            return [None] * len(titles)
        data = r.json()
        q = data.get('query', {})
        alias.update({x['from']: x['to'] for x in q.get('normalized', []) + q.get('redirects', [])})
        for p in q.get('pages', []):
            views[p['title']] = views.get(p['title'], 0) + sum(v or 0 for v in (p.get('pageviews') or {}).values())
        if 'continue' not in data:
            break
        cont = data['continue']
    return [views.get(alias.get(alias.get(t, t), alias.get(t, t)), 0) for t in names]


def main():
    conn = connect()
    conn.execute('CREATE TABLE IF NOT EXISTS movie_ptwiki (imdb_id TEXT PRIMARY KEY, ptwiki TEXT, pt_pageviews INTEGER)')
    conn.execute('CREATE TABLE IF NOT EXISTS movie_country (imdb_id TEXT, country TEXT, PRIMARY KEY (imdb_id, country))')
    done = {r[0] for r in conn.execute('SELECT imdb_id FROM movie_ptwiki')}
    todo = [r[0] for r in conn.execute('SELECT imdb_id FROM movies WHERE imdb_votes >= 500') if r[0] not in done]
    print(f'[pt] Wikidata: {len(todo):,} filmes', flush=True)
    batches = [todo[k:k + 200] for k in range(0, len(todo), 200)]
    with ThreadPoolExecutor(4) as ex:
        for batch, rows in ex.map(wikidata, batches):
            if rows is None:
                continue
            art, countries = {}, set()
            for b in rows:
                iid = b['imdb']['value']
                if 'article' in b:
                    art[iid] = b['article']['value'].rsplit('/wiki/', 1)[1]
                if 'countryLabel' in b:
                    countries.add((iid, b['countryLabel']['value']))
            with conn:
                conn.executemany('INSERT OR REPLACE INTO movie_ptwiki (imdb_id, ptwiki) VALUES (?, ?)', [(i, art.get(i)) for i in batch])
                conn.executemany('INSERT OR IGNORE INTO movie_country VALUES (?, ?)', sorted(countries))
    todo = conn.execute('SELECT imdb_id, ptwiki FROM movie_ptwiki WHERE ptwiki IS NOT NULL AND pt_pageviews IS NULL').fetchall()
    print(f'[pt] visualizações: {len(todo):,} artigos', flush=True)
    chunks = [todo[k:k + 50] for k in range(0, len(todo), 50)]
    t0 = time.time()
    with ThreadPoolExecutor(3) as ex:
        for n, (chunk, v) in enumerate(zip(chunks, ex.map(lambda c: pt_views([t for _, t in c]), chunks)), 1):
            with conn:
                conn.executemany('UPDATE movie_ptwiki SET pt_pageviews = ? WHERE imdb_id = ?',
                                 [(x, i) for (i, _), x in zip(chunk, v) if x is not None])
            if n % 100 == 0:
                print(f'  {n * 50:,}/{len(todo):,} ({time.time() - t0:.0f}s)', flush=True)
    q = lambda s: conn.execute(s).fetchone()[0]
    print(f'[pt] artigos em português: {q("SELECT COUNT(ptwiki) FROM movie_ptwiki"):,} de {q("SELECT COUNT(*) FROM movie_ptwiki"):,} · '
          f'países de origem: {q("SELECT COUNT(DISTINCT imdb_id) FROM movie_country"):,} filmes')
    return 0


if __name__ == '__main__':
    sys.exit(main())
