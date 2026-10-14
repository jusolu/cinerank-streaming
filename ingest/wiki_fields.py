"""
Campos dos artigos da Wikipédia em inglês, 50 artigos por consulta (API action=query, sem chave):
enredo completo (seção Plot), orçamento e bilheteria (caixa de informações), nota do Rotten Tomatoes
e do Metacritic citadas no texto, CinemaScore e se há seção de prêmios (Accolades).

Texto da Wikipédia: CC BY-SA 4.0. O enredo fica no banco local para análise (ritmo e arco emocional).
Grava movie_wikifields; dá para interromper e retomar.
    .venv-ml/Scripts/python -m ingest.wiki_fields
"""
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests

from ingest.db import connect

API = 'https://en.wikipedia.org/w/api.php'
UA = 'CineRank/1.0 (projeto pessoal, nao comercial; https://github.com/jusolu) python-requests'
session = requests.Session()
session.headers['User-Agent'] = UA


def money(s):
    """'$12.5 million' / '{{US$|30 million}}' / '$1,234,567' → dólares (primeiro valor; None se não der)."""
    if not s:
        return None
    s = re.sub(r'<ref.*?(</ref>|/>)', '', s, flags=re.S)
    s = re.sub(r'\{\{(?:US\$|USD)\|', '$', s)
    m = re.search(r'\$\s*([\d.,]+)\s*(million|billion|bn|m)?', s, re.I)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(',', ''))
    except ValueError:
        return None
    unit = (m.group(2) or '').lower()
    return v * (1e9 if unit in ('billion', 'bn') else 1e6 if unit in ('million', 'm') else 1)


def infobox(w, field):
    m = re.search(r'\|\s*' + field + r'\s*=\s*(.+)', w)
    return m.group(1).strip() if m else None


def clean(text):
    text = re.sub(r'<ref.*?(</ref>|/>)', '', text, flags=re.S)
    text = re.sub(r'\{\{[^{}]*\}\}', '', text)
    text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]', r'\1', text)
    text = re.sub(r"'{2,}", '', text)
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse(w):
    plot = re.search(r'==\s*(?:Plot|Synopsis|Plot summary)\s*==\s*\n(.*?)(?=\n==[^=])', w, re.S)
    rt = re.search(r'Rotten Tomatoes[^\n]{0,300}?(\d{1,3})\s*%', w)
    mc = re.search(r'Metacritic[^\n]{0,300}?(\d{1,3})\s*(?:out of|/)\s*100', w)
    cs = re.search(r'CinemaScore[^\n]{0,200}?grade of ["“]?([A-F][+−-]?)', w)
    return {
        'plot': clean(plot.group(1)) if plot else None,
        'budget_usd': money(infobox(w, 'budget')),
        'gross_usd': money(infobox(w, 'gross')),
        'rt': int(rt.group(1)) if rt and int(rt.group(1)) <= 100 else None,
        'metacritic': int(mc.group(1)) if mc and int(mc.group(1)) <= 100 else None,
        'cinemascore': cs.group(1).replace('−', '-') if cs else None,
        'accolades': int(bool(re.search(r'==\s*(Accolades|Awards|Awards and nominations)\s*==', w))),
        'length': len(w),
    }


def fetch(chunk):
    names = {urllib.parse.unquote(t).replace('_', ' '): i for i, t in chunk}
    params = {'action': 'query', 'prop': 'revisions', 'rvprop': 'content', 'rvslots': 'main', 'titles': '|'.join(names),
              'redirects': 1, 'format': 'json', 'formatversion': 2}
    for attempt in range(6):
        try:
            r = session.get(API, params=params, timeout=120)
            if r.ok and r.headers.get('content-type', '').startswith('application/json'):
                data = r.json()
                break
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 ** (attempt + 1))
    else:
        return chunk, None
    q = data.get('query', {})
    alias = {x['from']: x['to'] for x in q.get('normalized', []) + q.get('redirects', [])}
    back = {}
    for name, iid in names.items():
        back[alias.get(alias.get(name, name), alias.get(name, name))] = iid
    rows = []
    for p in q.get('pages', []):
        iid = back.get(p.get('title'))
        w = (p.get('revisions') or [{}])[0].get('slots', {}).get('main', {}).get('content')
        if iid and w:
            rows.append((iid, parse(w)))
    return chunk, rows


def main():
    conn = connect()
    conn.execute('CREATE TABLE IF NOT EXISTS movie_wikifields (imdb_id TEXT PRIMARY KEY, plot TEXT, budget_usd REAL, '
                 'gross_usd REAL, rt INTEGER, metacritic INTEGER, cinemascore TEXT, accolades INTEGER, length INTEGER)')
    done = {r[0] for r in conn.execute('SELECT imdb_id FROM movie_wikifields')}
    todo = [(i, t) for i, t in conn.execute('SELECT imdb_id, enwiki FROM movie_fame_src WHERE enwiki IS NOT NULL') if i not in done]
    print(f'[wiki] {len(todo):,} artigos a ler, 50 por consulta', flush=True)
    chunks = [todo[k:k + 50] for k in range(0, len(todo), 50)]
    t0 = time.time()
    with ThreadPoolExecutor(2) as ex:
        for n, (chunk, rows) in enumerate(ex.map(fetch, chunks), 1):
            if rows:
                with conn:
                    conn.executemany('INSERT OR REPLACE INTO movie_wikifields VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                                     [(i, d['plot'], d['budget_usd'], d['gross_usd'], d['rt'], d['metacritic'], d['cinemascore'],
                                       d['accolades'], d['length']) for i, d in rows])
            if n % 50 == 0:
                print(f'  {n * 50:,}/{len(todo):,} ({time.time() - t0:.0f}s)', flush=True)
    q = lambda sql: conn.execute(sql).fetchone()[0]
    print(f'\n[wiki] {q("SELECT COUNT(*) FROM movie_wikifields"):,} artigos · enredo {q("SELECT COUNT(plot) FROM movie_wikifields"):,} · '
          f'orçamento {q("SELECT COUNT(budget_usd) FROM movie_wikifields"):,} · bilheteria {q("SELECT COUNT(gross_usd) FROM movie_wikifields"):,} · '
          f'RT {q("SELECT COUNT(rt) FROM movie_wikifields"):,} · Metacritic {q("SELECT COUNT(metacritic) FROM movie_wikifields"):,} · '
          f'CinemaScore {q("SELECT COUNT(cinemascore) FROM movie_wikifields"):,} · seção de prêmios {q("SELECT SUM(accolades) FROM movie_wikifields"):,}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
