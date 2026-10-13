"""
Atributos descritivos dos filmes a partir do Wikidata (SPARQL público, sem chave, licença CC0).

Coleta, por IMDb ID:
  subgenre   gênero detalhado (P136): neo-noir, filme de assalto, coming-of-age…
  based_on   obra de origem (P144) e o tipo dela (romance, HQ, peça, fatos reais…)
  setting    local onde a história se passa (P840)
  award      prêmios recebidos (P166) e indicações (P1411) relevantes: Oscar, Cannes, Veneza, Berlim, Globo de Ouro, BAFTA
  franchise  série ou franquia (P179, P8345)
  color      colorido / preto e branco (P462)
  subject    assunto principal (P921)

Rótulos em português quando existem; senão em inglês.

Uso:
    python -m ingest.wikidata                # filmes com 500+ votos ainda não coletados
    python -m ingest.wikidata --limit 1000
"""
import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from ingest.db import connect, set_state

SPARQL = 'https://query.wikidata.org/sparql'
USER_AGENT = 'CineRank/1.0 (projeto pessoal, nao comercial; https://github.com/jusolu/cinerank-streaming)'
BATCH = 200
LANGS = 'pt-br,pt,en'

# Uma consulta por propriedade evita a explosão de combinações de vários OPTIONAL.
QUERIES = {
    'subgenre': '?item wdt:P136 ?value .',
    'based_on': '?item wdt:P144 ?value .',
    'based_on_type': '?item wdt:P144 ?src . ?src wdt:P31 ?value .',
    'setting': '?item wdt:P840 ?value .',
    'award': '?item wdt:P166 ?value .',
    'nomination': '?item wdt:P1411 ?value .',
    'franchise': '{ ?item wdt:P179 ?value . } UNION { ?item wdt:P8345 ?value . }',
    'color': '?item wdt:P462 ?value .',
    'subject': '?item wdt:P921 ?value .',
    # Lote 2: classificação, bilheteria, crítica, personagens, locais de filmagem.
    'rating_br': '?item wdt:P3216 ?value .',          # ClassInd (Brasil)
    'rating_us': '?item wdt:P1657 ?value .',          # MPAA (EUA)
    'filming_location': '?item wdt:P915 ?value .',
}

# Propriedades com valor numérico ou com qualificador: consultas próprias (sem rótulo).
NUMERIC_QUERIES = {
    # Bilheteria em dólares (quando a moeda é USD).
    'box_office_usd': '?item p:P2142 ?st . ?st ps:P2142 ?value . ?st psv:P2142/wikibase:quantityUnit wd:Q4917 .',
    # Notas de crítica: Rotten Tomatoes (Q105584) e Metacritic (Q150248) via P444 com qualificador P447.
    'score_rt': '?item p:P444 ?st . ?st ps:P444 ?value ; pq:P447 wd:Q105584 .',
    'score_metacritic': '?item p:P444 ?st . ?st ps:P444 ?value ; pq:P447 wd:Q150248 .',
}

# Só prêmios que dizem algo sobre o filme para o público.
AWARD_PATTERN = re.compile(
    r'[oó]scar|academy award|palma de ouro|palme d.or|le[aã]o de ouro|golden lion|urso de ouro|golden bear|'
    r'globo de ouro|golden globe|bafta|grand prix', re.I)

session = requests.Session()
session.headers['User-Agent'] = USER_AGENT


def log(msg):
    print(f"[wikidata] {msg}", flush=True)


def run_query(imdb_ids, pattern):
    values = ' '.join(f'"{i}"' for i in imdb_ids)
    query = f"""
        SELECT ?imdb ?valueLabel WHERE {{
          VALUES ?imdb {{ {values} }}
          ?item wdt:P345 ?imdb .
          {pattern}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{LANGS}". }}
        }}"""
    for attempt in range(5):
        try:
            r = session.post(SPARQL, data={'query': query, 'format': 'json'}, timeout=90)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(float(r.headers.get('Retry-After', 2 ** (attempt + 1))))
            continue
        if r.ok:
            return r.json().get('results', {}).get('bindings', [])
        time.sleep(2 ** attempt)
    raise RuntimeError('Wikidata não respondeu após várias tentativas')


def run_numeric(imdb_ids, pattern):
    values = ' '.join(f'"{i}"' for i in imdb_ids)
    query = f"""
        SELECT ?imdb ?value WHERE {{
          VALUES ?imdb {{ {values} }}
          ?item wdt:P345 ?imdb .
          {pattern}
        }}"""
    for attempt in range(5):
        try:
            r = session.post(SPARQL, data={'query': query, 'format': 'json'}, timeout=90)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(float(r.headers.get('Retry-After', 2 ** (attempt + 1))))
            continue
        if r.ok:
            return r.json().get('results', {}).get('bindings', [])
        time.sleep(2 ** attempt)
    raise RuntimeError('Wikidata não respondeu após várias tentativas')


def clean_label(label):
    # Itens sem rótulo em pt/en voltam como o próprio ID (Q123): descartados.
    label = (label or '').strip()
    return None if not label or re.fullmatch(r'Q\d+', label) else label


PARALLEL = 4  # o serviço de consultas do Wikidata aceita até 5 consultas simultâneas por IP


def collect(imdb_ids):
    rows = set()
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        results = dict(zip(QUERIES, pool.map(lambda pat: run_query(imdb_ids, pat), QUERIES.values())))
    for prop, bindings in results.items():
        for b in bindings:
            value = clean_label(b.get('valueLabel', {}).get('value'))
            if not value:
                continue
            if prop in ('award', 'nomination') and not AWARD_PATTERN.search(value):
                continue
            rows.add((b['imdb']['value'], prop, value))
    return rows


def recollect(conn, props, min_votes):
    """Refaz algumas propriedades (por exemplo, depois de corrigir um filtro) sem repetir as outras."""
    unknown = set(props) - set(QUERIES) - set(NUMERIC_QUERIES)
    if unknown:
        raise SystemExit(f'Propriedades desconhecidas: {", ".join(sorted(unknown))}')
    ids = [r[0] for r in conn.execute('SELECT imdb_id FROM movies WHERE imdb_votes >= ? ORDER BY imdb_votes DESC', (min_votes,))]
    log(f'refazendo {", ".join(props)} para {len(ids):,} filmes')
    t0 = time.time()
    for start in range(0, len(ids), BATCH):
        batch = ids[start:start + BATCH]
        rows = set()
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            results = dict(zip(props, pool.map(
                lambda p: run_numeric(batch, NUMERIC_QUERIES[p]) if p in NUMERIC_QUERIES else run_query(batch, QUERIES[p]),
                props)))
        for prop, bindings in results.items():
            for b in bindings:
                value = (b.get('value', {}).get('value') if prop in NUMERIC_QUERIES
                         else clean_label(b.get('valueLabel', {}).get('value')))
                if value and (prop not in ('award', 'nomination') or AWARD_PATTERN.search(value)):
                    rows.add((b['imdb']['value'], prop, value))
        marks = ','.join('?' * len(props))
        with conn:
            conn.executemany(f'DELETE FROM movie_wikidata WHERE imdb_id = ? AND prop IN ({marks})',
                             [(i, *props) for i in batch])
            conn.executemany('INSERT OR IGNORE INTO movie_wikidata (imdb_id, prop, value) VALUES (?, ?, ?)', sorted(rows))
        if (start // BATCH) % 25 == 0:
            log(f'{start + len(batch):,}/{len(ids):,}')
    log(f'concluído em {time.time() - t0:.0f}s')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Atributos via Wikidata (sem chave).')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--min-votes', type=int, default=500)
    parser.add_argument('--props', default='', help='Refaz só estas propriedades para todos os filmes (ex.: award,nomination)')
    args = parser.parse_args(argv)

    conn = connect()
    if args.props:
        return recollect(conn, [p.strip() for p in args.props.split(',') if p.strip()], args.min_votes)
    pending = [r[0] for r in conn.execute(f"""
        SELECT m.imdb_id FROM movies m
        WHERE m.imdb_votes >= ? AND NOT EXISTS (SELECT 1 FROM movie_wikidata_done d WHERE d.imdb_id = m.imdb_id)
        ORDER BY m.imdb_votes DESC {'LIMIT ' + str(int(args.limit)) if args.limit else ''}
    """, (args.min_votes,))]
    log(f'{len(pending):,} filmes a coletar')
    t0 = time.time()
    total_rows = 0

    for start in range(0, len(pending), BATCH):
        batch = pending[start:start + BATCH]
        rows = collect(batch)
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with conn:
            conn.executemany('DELETE FROM movie_wikidata WHERE imdb_id = ?', [(i,) for i in batch])
            conn.executemany('INSERT OR IGNORE INTO movie_wikidata (imdb_id, prop, value) VALUES (?, ?, ?)', sorted(rows))
            conn.executemany('INSERT OR REPLACE INTO movie_wikidata_done (imdb_id, fetched_at) VALUES (?, ?)',
                             [(i, now) for i in batch])
        total_rows += len(rows)
        done = start + len(batch)
        rate = done / (time.time() - t0)
        log(f'{done:,}/{len(pending):,} · {total_rows:,} atributos · {rate:.1f} filmes/s')

    with conn:
        set_state(conn, 'wikidata_loaded_at', datetime.now(timezone.utc).isoformat(timespec='seconds'))
    log(f'concluído em {time.time() - t0:.0f}s')
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
