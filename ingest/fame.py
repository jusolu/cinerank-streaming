"""
Índice de conhecimento: quão conhecido é cada filme do catálogo (0 a 100) e a faixa
pouco / médio / muito conhecido.

Sinais (cada um vira percentil dentro do catálogo):
  votos no IMDb               quantas pessoas avaliaram no IMDb             peso 0,35
  visualizações na Wikipédia  em inglês, últimos 60 dias (API da Wikipédia, 50 artigos por consulta)  peso 0,25
  idiomas na Wikipédia        em quantas Wikipédias há artigo (Wikidata)    peso 0,25
  notas no MovieLens          quantas pessoas avaliaram no MovieLens        peso 0,15
                              (só até 2023; para filmes mais novos fica de fora)

As faixas saem de um KMeans (k=3) sobre os percentis, ordenado pela média do índice.

Coleta com cache em tabelas (dá para interromper e retomar):
  movie_fame_src  imdb_id, sitelinks, enwiki, pageviews
  movie_fame      imdb_id, votes, ml_ratings, sitelinks, pageviews, fame, tier

Uso (.venv-ml):
    .venv-ml/Scripts/python -m ingest.fame                 # coleta o que falta e calcula
    .venv-ml/Scripts/python -m ingest.fame --score-only
"""
import argparse
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests
from sklearn.cluster import KMeans

from ingest.db import connect
from ingest.wikidata import SPARQL, USER_AGENT

MIN_VOTES = 500
BATCH = 200
WEIGHTS = {'votes': 0.35, 'pageviews': 0.25, 'sitelinks': 0.25, 'ml_ratings': 0.15}
WIKI_API = 'https://en.wikipedia.org/w/api.php'
PV_DAYS = 60  # máximo da API em lote; a API REST por artigo limita a ~4 consultas/s
TIERS = ['Pouco conhecido', 'Médio', 'Muito conhecido']

session = requests.Session()
session.headers['User-Agent'] = USER_AGENT


def log(msg):
    print(f'[fama] {msg}', flush=True)


def ensure_tables(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS movie_fame_src (imdb_id TEXT PRIMARY KEY, sitelinks INTEGER, '
                 'enwiki TEXT, pageviews INTEGER)')
    conn.execute('CREATE TABLE IF NOT EXISTS movie_fame (imdb_id TEXT PRIMARY KEY, votes INTEGER, ml_ratings INTEGER, '
                 'sitelinks INTEGER, pageviews INTEGER, fame REAL, tier TEXT)')


def sitelinks(batch):
    values = ' '.join(f'"{i}"' for i in batch)
    query = f"""
        SELECT ?imdb ?links ?article WHERE {{
          VALUES ?imdb {{ {values} }}
          ?item wdt:P345 ?imdb ; wikibase:sitelinks ?links .
          OPTIONAL {{ ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }}
        }}"""
    for attempt in range(6):
        try:
            r = session.post(SPARQL, data={'query': query, 'format': 'json'}, timeout=90)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.ok:
            out = {}
            for b in r.json()['results']['bindings']:
                iid = b['imdb']['value']
                links = int(b['links']['value'])
                art = b.get('article', {}).get('value')
                title = art.rsplit('/wiki/', 1)[1] if art else None
                if iid not in out or links > out[iid][0]:
                    out[iid] = (links, title)
            # sem item no Wikidata = nenhum artigo em nenhuma Wikipédia
            return [(i, *out.get(i, (0, None))) for i in batch]
        time.sleep(float(r.headers.get('Retry-After', 2 ** (attempt + 1))))
    raise RuntimeError('Wikidata não respondeu')


def pageviews(titles):
    """Visualizações dos últimos 60 dias para até 50 artigos de uma vez (segue redirecionamentos)."""
    names = [urllib.parse.unquote(t).replace('_', ' ') for t in titles]
    params = {'action': 'query', 'prop': 'pageviews', 'pvipdays': PV_DAYS, 'titles': '|'.join(names),
              'redirects': 1, 'format': 'json', 'formatversion': 2}
    views, alias, cont = {}, {}, {}
    for _ in range(50):  # a API entrega as visualizações em partes (pvipcontinue)
        r = None
        for attempt in range(6):
            try:
                r = session.get(WIKI_API, params={**params, **cont}, timeout=60)
            except requests.RequestException:
                r = None
            if r is not None and r.ok:
                break
            time.sleep(float(r.headers.get('Retry-After', 2 ** (attempt + 1))) if r is not None else 2 ** attempt)
        if r is None or not r.ok:
            return [None] * len(titles)
        data = r.json()
        q = data.get('query', {})
        alias.update({x['from']: x['to'] for x in q.get('normalized', []) + q.get('redirects', [])})
        for p in q.get('pages', []):
            if p.get('pageviews'):
                views[p['title']] = sum(v or 0 for v in p['pageviews'].values())
            else:
                views.setdefault(p['title'], 0)
        if 'continue' not in data:
            break
        cont = data['continue']
    out = []
    for t in names:
        final = alias.get(alias.get(t, t), alias.get(t, t))
        out.append(views.get(final, 0))
    return out


def collect(conn):
    ids = [r[0] for r in conn.execute('SELECT imdb_id FROM movies WHERE imdb_votes >= ? ORDER BY imdb_votes DESC', (MIN_VOTES,))]
    done = {r[0] for r in conn.execute('SELECT imdb_id FROM movie_fame_src')}
    todo = [i for i in ids if i not in done]
    log(f'Wikidata: {len(todo):,} filmes a consultar ({len(done):,} já em cache)')
    batches = [todo[k:k + BATCH] for k in range(0, len(todo), BATCH)]
    with ThreadPoolExecutor(4) as ex:  # o Wikidata aceita até 5 consultas simultâneas
        for n, rows in enumerate(ex.map(sitelinks, batches), 1):
            with conn:
                conn.executemany('INSERT OR REPLACE INTO movie_fame_src (imdb_id, sitelinks, enwiki) VALUES (?, ?, ?)', rows)
            if n % 25 == 0:
                log(f'  Wikidata {n}/{len(batches)} lotes')

    todo = conn.execute('SELECT imdb_id, enwiki FROM movie_fame_src WHERE enwiki IS NOT NULL AND pageviews IS NULL').fetchall()
    log(f'Pageviews: {len(todo):,} artigos a consultar, 50 por vez')
    t0 = time.time()
    chunks = [todo[k:k + 50] for k in range(0, len(todo), 50)]
    with ThreadPoolExecutor(3) as ex:
        for n, (chunk, views) in enumerate(zip(chunks, ex.map(lambda c: pageviews([t for _, t in c]), chunks)), 1):
            with conn:
                conn.executemany('UPDATE movie_fame_src SET pageviews = ? WHERE imdb_id = ?',
                                 [(v, i) for (i, _), v in zip(chunk, views) if v is not None])
            if n % 100 == 0:
                log(f'  pageviews {n * 50:,}/{len(todo):,} ({time.time() - t0:.0f}s)')
    with conn:  # filmes sem artigo em inglês: zero visitas
        conn.execute('UPDATE movie_fame_src SET pageviews = 0 WHERE enwiki IS NULL')


def ml_counts(conn):
    from ingest.vectors import ML_DIR
    import os
    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    links['imdb_id'] = 'tt' + links['imdbId'].str.zfill(7)
    r = pd.read_csv(os.path.join(ML_DIR, 'ratings.csv'), engine='pyarrow', usecols=['movieId'])
    counts = r['movieId'].value_counts().rename('ml_ratings')
    return links.set_index('movieId').join(counts)[['imdb_id', 'ml_ratings']].fillna(0).set_index('imdb_id')['ml_ratings']


def score(conn):
    df = pd.DataFrame(conn.execute(
        'SELECT m.imdb_id, m.year, m.imdb_votes AS votes, s.sitelinks, s.pageviews FROM movies m '
        'LEFT JOIN movie_fame_src s USING(imdb_id) WHERE m.imdb_votes >= ?', (MIN_VOTES,)).fetchall(),
        columns=['imdb_id', 'year', 'votes', 'sitelinks', 'pageviews']).set_index('imdb_id')
    df['ml_ratings'] = ml_counts(conn).reindex(df.index).fillna(0)
    df.loc[df['year'] >= 2023, 'ml_ratings'] = np.nan  # o MovieLens 32M vai até 2023: ausência não é desconhecimento
    pct = pd.DataFrame({k: df[k].rank(pct=True) for k in WEIGHTS}, index=df.index)
    w = pd.Series(WEIGHTS)
    df['fame'] = (100 * (pct * w).sum(axis=1) / pct.notna().mul(w).sum(axis=1)).round(1)

    X = pct.apply(lambda col: col.fillna(pct['votes'])).to_numpy()
    km = KMeans(3, n_init=20, random_state=0).fit(X)
    order = np.argsort([df['fame'][km.labels_ == c].mean() for c in range(3)])
    df['tier'] = [TIERS[int(np.where(order == c)[0][0])] for c in km.labels_]
    with conn:
        conn.execute('DELETE FROM movie_fame')
        conn.executemany('INSERT INTO movie_fame VALUES (?, ?, ?, ?, ?, ?, ?)',
                         [(i, int(r.votes), None if pd.isna(r.ml_ratings) else int(r.ml_ratings),
                           None if pd.isna(r.sitelinks) else int(r.sitelinks),
                           None if pd.isna(r.pageviews) else int(r.pageviews), float(r.fame), r.tier)
                          for i, r in df.iterrows()])

    print('\n== Faixas de conhecimento (catálogo com 500+ votos)')
    g = df.groupby('tier')
    summary = pd.DataFrame({'filmes': g.size(), 'índice': g['fame'].median(), 'votos IMDb': g['votes'].median(),
                            'views 60 dias': g['pageviews'].median(), 'idiomas wiki': g['sitelinks'].median(),
                            'notas ML': g['ml_ratings'].median(),
                            'recomendáveis (20k+)': g['votes'].apply(lambda v: int((v >= 20000).sum()))}).reindex(TIERS)
    print(summary.round(1).to_string())
    print('\nExemplos por faixa (mais votados de cada uma):')
    names = dict(conn.execute('SELECT imdb_id, title FROM movies').fetchall())
    for t in TIERS:
        sub = df[df.tier == t].sort_values('fame', ascending=False)
        mid = sub.iloc[len(sub) // 2: len(sub) // 2 + 3]
        print(f'  {t:16} topo: {"; ".join(names[i] for i in sub.index[:3])} · meio: {"; ".join(names[i] for i in mid.index)}')
    corr = pct.corr(method='spearman').round(2)
    print('\nCorrelação entre os sinais:\n' + corr.to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--score-only', action='store_true')
    args = ap.parse_args()
    conn = connect()
    ensure_tables(conn)
    if not args.score_only:
        collect(conn)
    score(conn)
    return 0


if __name__ == '__main__':
    sys.exit(main())
