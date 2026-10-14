"""
Premiações completas por filme, do Wikidata: todos os prêmios recebidos (P166) e indicações
(P1411), sem filtro, classificados por tipo. Base da hipótese 9 (premiação como sinal).

Grava movie_award (imdb_id, kind 'win'|'nom', award, category) e imprime a cobertura.
    .venv-ml/Scripts/python -m ingest.awards
"""
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from ingest.db import connect
from ingest.wikidata import SPARQL, USER_AGENT

BATCH = 150
CATEGORY = [  # ordem importa: o primeiro que casar define a categoria
    ('oscar', r'academy award|oscar|óscar'),
    ('globo_de_ouro', r'golden globe'),
    ('bafta', r'bafta|british academy'),
    ('cannes', r'cannes|palme d|palma de ouro|caméra d|camera d'),
    ('berlim', r'berlin|berlinale|golden bear|silver bear|urso de'),
    ('veneza', r'venice|venezia|golden lion|leone d|leão de'),
    ('sundance', r'sundance'),
    ('sindicatos', r'guild|directors guild|producers guild|writers guild|screen actors'),
    ('criticos', r'critics|critic|film critics|national board of review|fipresci'),
    ('nacionais', r'césar|cesar award|goya|david di donatello|european film award|german film award|'
                  r'filmfare|national film award|japan academy|blue dragon|grande prêmio do cinema brasileiro|'
                  r'ariel award|genie award|canadian screen|aacta|guldbagge|robert award'),
    ('festivais', r'festival|toronto|tribeca|san sebasti|locarno|karlovy|rotterdam|busan|sitges|annecy|'
                  r'fantastic|tiff|international film'),
    ('genero', r'saturn award|hugo award|nebula|annie award|bram stoker|empire award'),
    ('populares', r"people's choice|mtv|teen choice|kids' choice|razzie|golden raspberry|nickelodeon"),
]
CAT = [(k, re.compile(p, re.I)) for k, p in CATEGORY]
session = requests.Session()
session.headers['User-Agent'] = USER_AGENT


def categorize(label):
    for k, rx in CAT:
        if rx.search(label or ''):
            return k
    return 'outros'


def query(batch):
    values = ' '.join(f'"{i}"' for i in batch)
    q = f"""
        SELECT ?imdb ?kind ?awardLabel WHERE {{
          VALUES ?imdb {{ {values} }}
          ?item wdt:P345 ?imdb .
          {{ ?item wdt:P166 ?award . BIND("win" AS ?kind) }} UNION {{ ?item wdt:P1411 ?award . BIND("nom" AS ?kind) }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt". }}
        }}"""
    for attempt in range(6):
        try:
            r = session.post(SPARQL, data={'query': q, 'format': 'json'}, timeout=120)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.ok:
            rows = []
            for b in r.json()['results']['bindings']:
                label = b.get('awardLabel', {}).get('value', '')
                rows.append((b['imdb']['value'], b['kind']['value'], label, categorize(label)))
            return batch, rows
        time.sleep(float(r.headers.get('Retry-After', 2 ** (attempt + 1))))
    return batch, None


def main():
    conn = connect()
    conn.execute('CREATE TABLE IF NOT EXISTS movie_award (imdb_id TEXT, kind TEXT, award TEXT, category TEXT, '
                 'PRIMARY KEY (imdb_id, kind, award))')
    conn.execute('CREATE TABLE IF NOT EXISTS movie_award_done (imdb_id TEXT PRIMARY KEY)')
    ids = [r[0] for r in conn.execute('SELECT imdb_id FROM movies WHERE imdb_votes >= 500 ORDER BY imdb_votes DESC')]
    done = {r[0] for r in conn.execute('SELECT imdb_id FROM movie_award_done')}
    todo = [i for i in ids if i not in done]
    print(f'[prêmios] {len(todo):,} filmes a consultar', flush=True)
    batches = [todo[k:k + BATCH] for k in range(0, len(todo), BATCH)]
    t0 = time.time()
    with ThreadPoolExecutor(4) as ex:
        for n, (batch, rows) in enumerate(ex.map(query, batches), 1):
            if rows is None:
                continue
            with conn:
                conn.executemany('INSERT OR IGNORE INTO movie_award VALUES (?, ?, ?, ?)', rows)
                conn.executemany('INSERT OR IGNORE INTO movie_award_done VALUES (?)', [(i,) for i in batch])
            if n % 40 == 0:
                print(f'  {n}/{len(batches)} lotes ({time.time() - t0:.0f}s)', flush=True)
    print('\n== Cobertura (filmes com 500+ votos)')
    for kind in ('win', 'nom'):
        print(f'  {"prêmios" if kind == "win" else "indicações"}: ' + ', '.join(
            f'{c} {n:,}' for c, n in conn.execute(
                'SELECT category, COUNT(DISTINCT imdb_id) FROM movie_award WHERE kind = ? GROUP BY category ORDER BY 2 DESC', (kind,))))
    print('  filmes com algum prêmio:', conn.execute("SELECT COUNT(DISTINCT imdb_id) FROM movie_award WHERE kind='win'").fetchone()[0],
          '· com alguma indicação:', conn.execute("SELECT COUNT(DISTINCT imdb_id) FROM movie_award WHERE kind='nom'").fetchone()[0])
    print('  "outros" mais comuns:', conn.execute(
        "SELECT award, COUNT(*) n FROM movie_award WHERE category='outros' GROUP BY award ORDER BY n DESC LIMIT 15").fetchall())
    return 0


if __name__ == '__main__':
    sys.exit(main())
